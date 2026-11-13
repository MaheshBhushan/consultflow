from decimal import Decimal

import pytest
from sqlalchemy import text

from app import llm
from app.db import engine

EMAIL_AGENT = {
    "name": "Invoice dispute triage",
    "provider": "offline",
    "model": "offline-extractive-v1",
    "system_prompt": "You are a billing assistant for {{client}}.",
    "input_kind": "email",
    "steps": [
        {"name": "extract", "prompt": "Extract the facts from this email:\n{{input}}"},
        {
            "name": "draft reply",
            "prompt": "Subject: Re: {{subject}}\nTo: {{sender}}\n"
            "Draft a reply using:\n{{previous}}",
        },
    ],
}


def make_client(client, name="Northwind Logistics"):
    r = client.post("/api/clients", json={"name": name, "contact_email": "ops@northwind.example"})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_configure_run_inspect_email_flow(client, samples):
    c = make_client(client)
    assert client.get("/api/clients").json()[0]["name"] == "Northwind Logistics"

    agent = client.post(f"/api/clients/{c['id']}/agents", json=EMAIL_AGENT)
    assert agent.status_code == 201, agent.text
    agent = agent.json()

    with open(samples / "invoice-dispute.eml", "rb") as fh:
        up = client.post(
            f"/api/clients/{c['id']}/inputs/upload",
            files={"file": ("invoice-dispute.eml", fh, "message/rfc822")},
        )
    assert up.status_code == 201, up.text
    src = up.json()
    assert src["kind"] == "email" and src["origin"] == "upload"

    run = client.post(f"/api/agents/{agent['id']}/runs", json={"input_id": src["id"]})
    assert run.status_code == 201, run.text
    run = run.json()
    assert run["status"] == "succeeded"
    assert [s["name"] for s in run["steps"]] == ["extract", "draft reply"]

    step1, step2 = run["steps"]
    assert step1["sources"] == [
        f"email:#{src['id']} invoice-dispute.eml sha256:{src['sha256'][:12]}"
    ]
    assert step2["sources"] == [f"email-meta:#{src['id']}", "step:1 extract"]
    assert "Re: Invoice INV-2026-0412 disputed" in step2["prompt"]
    assert step1["output"] in step2["prompt"]
    assert "$3,200.00" in step1["output"]
    for s in run["steps"]:
        assert s["input_tokens"] > 0 and s["output_tokens"] > 0
        assert s["latency_ms"] >= 0
        assert Decimal(s["cost_usd"]) == Decimal(0)  # offline model is priced at $0
    assert run["final_output"] == step2["output"]
    assert run["total_input_tokens"] == step1["input_tokens"] + step2["input_tokens"]
    assert run["agent_snapshot"]["steps"] == EMAIL_AGENT["steps"]

    # The log is persisted in PostgreSQL, not just returned.
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT position, provider, model, sources, latency_ms, cost_usd, status "
                "FROM run_steps WHERE run_id = :r ORDER BY position"
            ),
            {"r": run["id"]},
        ).all()
    assert [r.position for r in rows] == [1, 2]
    assert rows[0].sources == step1["sources"]
    assert all(r.status == "succeeded" and r.model == "offline-extractive-v1" for r in rows)

    fetched = client.get(f"/api/runs/{run['id']}").json()
    assert fetched["steps"] == run["steps"]
    history = client.get(f"/api/agents/{agent['id']}/runs").json()
    assert [h["id"] for h in history] == [run["id"]]


def test_document_agent_with_named_step_reference(client, samples):
    c = make_client(client)
    agent = client.post(
        f"/api/clients/{c['id']}/agents",
        json={
            "name": "SOW review",
            "input_kind": "document",
            "steps": [
                {"name": "summary", "prompt": "Summarise {{filename}}:\n{{input}}"},
                {"name": "risks", "prompt": "List risks in:\n{{input}}"},
                {"name": "brief", "prompt": "{{step.summary}}\n{{step.risks}}"},
            ],
        },
    ).json()
    src = client.post(
        f"/api/clients/{c['id']}/inputs/upload",
        files={
            "file": ("sow.md", (samples / "statement-of-work.md").read_bytes(), "text/markdown")
        },
    ).json()
    run = client.post(f"/api/agents/{agent['id']}/runs", json={"input_id": src["id"]}).json()
    assert run["status"] == "succeeded"
    assert run["steps"][2]["sources"] == ["step:1 summary", "step:2 risks"]
    assert run["steps"][0]["sources"] == [
        f"document-meta:#{src['id']}",
        f"document:#{src['id']} sow.md sha256:{src['sha256'][:12]}",
    ]


def test_pasted_email_and_kind_mismatch(client):
    c = make_client(client)
    pasted = client.post(
        f"/api/clients/{c['id']}/inputs/email",
        json={
            "raw": "From: a@b.example\nSubject: Renewal\n\nPlease renew the contract by 2026-12-31."
        },
    )
    assert pasted.status_code == 201
    assert pasted.json()["meta"]["subject"] == "Renewal"
    doc_agent = client.post(
        f"/api/clients/{c['id']}/agents",
        json={
            "name": "doc",
            "input_kind": "document",
            "steps": [{"name": "s", "prompt": "{{input}}"}],
        },
    ).json()
    r = client.post(f"/api/agents/{doc_agent['id']}/runs", json={"input_id": pasted.json()["id"]})
    assert r.status_code == 422
    assert "expects a document" in r.json()["detail"]


def test_input_from_other_client_is_rejected(client):
    a, b = make_client(client, "A"), make_client(client, "B")
    agent = client.post(
        f"/api/clients/{a['id']}/agents",
        json={"name": "x", "input_kind": "email", "steps": [{"name": "s", "prompt": "{{input}}"}]},
    ).json()
    src = client.post(f"/api/clients/{b['id']}/inputs/email", json={"raw": "hello there"}).json()
    r = client.post(f"/api/agents/{agent['id']}/runs", json={"input_id": src["id"]})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "steps,msg",
    [
        ([{"name": "a", "prompt": "{{previous}}"}], "not available in step 1"),
        ([{"name": "a", "prompt": "{{step.b}}"}], "earlier step"),
        ([{"name": "a", "prompt": "{{nope}}"}], "unknown variable"),
        ([{"name": "a", "prompt": "x"}, {"name": "a", "prompt": "y"}], "duplicate step"),
        ([], "at least 1"),
    ],
)
def test_agent_validation(client, steps, msg):
    c = make_client(client)
    r = client.post(
        f"/api/clients/{c['id']}/agents",
        json={"name": "bad", "input_kind": "email", "steps": steps},
    )
    assert r.status_code == 422
    assert msg in r.text


def test_unknown_model_rejected(client):
    c = make_client(client)
    r = client.post(
        f"/api/clients/{c['id']}/agents",
        json={
            "name": "x",
            "provider": "anthropic",
            "model": "gpt-4o-mini",
            "input_kind": "email",
            "steps": [{"name": "s", "prompt": "{{input}}"}],
        },
    )
    assert r.status_code == 422


def test_duplicate_client_conflict(client):
    make_client(client)
    assert client.post("/api/clients", json={"name": "Northwind Logistics"}).status_code == 409


def test_priced_provider_cost_and_failed_step_are_logged(client, monkeypatch):
    calls = []

    class FakeHaiku:
        def complete(self, model, system, prompt):
            calls.append(model)
            if len(calls) == 2:
                raise llm.LLMError("anthropic HTTP 529: overloaded")
            return llm.LLMResult("step one output", 1000, 200)

    monkeypatch.setattr(llm, "get_provider", lambda name: FakeHaiku())
    c = make_client(client)
    agent = client.post(
        f"/api/clients/{c['id']}/agents",
        json={
            "name": "priced",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "input_kind": "email",
            "steps": [
                {"name": "one", "prompt": "{{input}}"},
                {"name": "two", "prompt": "{{previous}}"},
                {"name": "three", "prompt": "{{previous}}"},
            ],
        },
    ).json()
    src = client.post(f"/api/clients/{c['id']}/inputs/email", json={"raw": "hello there"}).json()
    run = client.post(f"/api/agents/{agent['id']}/runs", json={"input_id": src["id"]}).json()
    assert run["status"] == "failed"
    assert "step 2 (two) failed" in run["error"]
    assert len(run["steps"]) == 2  # step three never ran
    assert Decimal(run["steps"][0]["cost_usd"]) == Decimal("0.002")
    assert run["steps"][1]["status"] == "failed"
    assert "529" in run["steps"][1]["error"]
    assert Decimal(run["total_cost_usd"]) == Decimal("0.002")


def test_missing_key_marks_run_failed(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c = make_client(client)
    agent = client.post(
        f"/api/clients/{c['id']}/agents",
        json={
            "name": "oa",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "input_kind": "email",
            "steps": [{"name": "s", "prompt": "{{input}}"}],
        },
    ).json()
    src = client.post(f"/api/clients/{c['id']}/inputs/email", json={"raw": "hello"}).json()
    run = client.post(f"/api/agents/{agent['id']}/runs", json={"input_id": src["id"]}).json()
    assert run["status"] == "failed"
    assert "OPENAI_API_KEY" in run["error"]
    assert run["steps"] == []


def test_upload_rejects_unsupported(client):
    c = make_client(client)
    r = client.post(
        f"/api/clients/{c['id']}/inputs/upload", files={"file": ("x.exe", b"MZ", "application/x")}
    )
    assert r.status_code == 422


def test_providers_endpoint(client):
    data = client.get("/api/providers").json()
    assert data["offline"] == {"models": ["offline-extractive-v1"], "available": True}
