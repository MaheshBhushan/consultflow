"""End-to-end check against a running ConsultFlow (e.g. `docker compose up`).

Exercises create client -> configure agents -> add inputs -> run -> inspect, over HTTP,
and asserts the UI bundle is served. Prints the per-step log of each run.

    uv run --no-project --with httpx python scripts/e2e_flow.py --base-url http://127.0.0.1:18410
"""

import argparse
import re
import sys
import time
from pathlib import Path

import httpx

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def check(cond: bool, msg: str) -> None:
    if not cond:
        sys.exit(f"FAIL: {msg}")


def wait_ready(http: httpx.Client, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if http.get("/api/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    sys.exit("FAIL: service did not become healthy")


def print_run(run: dict) -> None:
    print(
        f"run #{run['id']} status={run['status']} total_cost_usd={run['total_cost_usd']} "
        f"total_latency_ms={run['total_latency_ms']} tokens={run['total_input_tokens']}/"
        f"{run['total_output_tokens']}"
    )
    for s in run["steps"]:
        print(
            f"  step {s['position']} {s['name']!r} {s['provider']}/{s['model']} {s['status']} "
            f"tokens={s['input_tokens']}/{s['output_tokens']} cost_usd={s['cost_usd']} "
            f"latency_ms={s['latency_ms']} sources={s['sources']}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:18410")
    ap.add_argument("--provider", default="offline")
    ap.add_argument("--model", default="offline-extractive-v1")
    args = ap.parse_args()

    http = httpx.Client(base_url=args.base_url, timeout=120)
    wait_ready(http)

    # UI is served by the same container.
    index = http.get("/")
    check(index.status_code == 200 and '<div id="root">' in index.text, "UI index not served")
    asset = re.search(r'src="(/assets/[^"]+\.js)"', index.text)
    check(asset is not None, "no JS bundle referenced")
    bundle = http.get(asset.group(1))
    check(bundle.status_code == 200 and "Run agent" in bundle.text, "JS bundle not served")
    print(f"UI served: / and {asset.group(1)} ({len(bundle.content)} bytes)")

    client = http.post(
        "/api/clients",
        json={
            "name": f"Northwind Logistics e2e {int(time.time())}",
            "contact_email": "dana.whitfield@northwind-logistics.example",
        },
    )
    check(client.status_code == 201, client.text)
    cid = client.json()["id"]

    email_agent = http.post(
        f"/api/clients/{cid}/agents",
        json={
            "name": "Invoice dispute triage",
            "provider": args.provider,
            "model": args.model,
            "system_prompt": "You are a billing assistant for a consultancy. Be concise.",
            "input_kind": "email",
            "steps": [
                {
                    "name": "extract",
                    "prompt": "List the invoice number, disputed amount, corrected total and "
                    "deadline in this email:\n{{input}}",
                },
                {
                    "name": "draft reply",
                    "prompt": "Write a short reply to {{sender}} about '{{subject}}' "
                    "confirming these facts:\n{{previous}}",
                },
            ],
        },
    )
    check(email_agent.status_code == 201, email_agent.text)
    email_agent = email_agent.json()

    # Re-configure: add a third step via PUT, as a consultant editing the agent would.
    cfg = {k: email_agent[k] for k in ("name", "provider", "model", "system_prompt", "input_kind")}
    cfg["steps"] = email_agent["steps"] + [
        {"name": "crm note", "prompt": "One-line CRM note from:\n{{step.extract}}"}
    ]
    updated = http.put(f"/api/agents/{email_agent['id']}", json=cfg)
    check(updated.status_code == 200 and len(updated.json()["steps"]) == 3, updated.text)

    eml = http.post(
        f"/api/clients/{cid}/inputs/upload",
        files={"file": ("invoice-dispute.eml", (SAMPLES / "invoice-dispute.eml").read_bytes())},
    )
    check(eml.status_code == 201 and eml.json()["kind"] == "email", eml.text)
    pasted = http.post(
        f"/api/clients/{cid}/inputs/email",
        json={
            "raw": "From: ap@northwind-logistics.example\nSubject: Remittance advice\n\n"
            "We paid $15,250.00 for INV-2026-0412 on 2026-11-18. Please confirm receipt."
        },
    )
    check(pasted.status_code == 201, pasted.text)

    doc_agent = http.post(
        f"/api/clients/{cid}/agents",
        json={
            "name": "SOW review",
            "provider": args.provider,
            "model": args.model,
            "input_kind": "document",
            "steps": [
                {"name": "deliverables", "prompt": "List deliverables and dates:\n{{input}}"},
                {"name": "risks", "prompt": "List delivery risks in {{filename}}:\n{{input}}"},
            ],
        },
    )
    check(doc_agent.status_code == 201, doc_agent.text)
    doc = http.post(
        f"/api/clients/{cid}/inputs/upload",
        files={"file": ("statement-of-work.md", (SAMPLES / "statement-of-work.md").read_bytes())},
    )
    check(doc.status_code == 201 and doc.json()["kind"] == "document", doc.text)

    jobs = [
        (email_agent["id"], eml.json()["id"], 3),
        (email_agent["id"], pasted.json()["id"], 3),
        (doc_agent.json()["id"], doc.json()["id"], 2),
    ]
    for agent_id, input_id, n_steps in jobs:
        run = http.post(f"/api/agents/{agent_id}/runs", json={"input_id": input_id})
        check(run.status_code == 201, run.text)
        fetched = http.get(f"/api/runs/{run.json()['id']}").json()
        check(fetched["status"] == "succeeded", f"run failed: {fetched['error']}")
        check(len(fetched["steps"]) == n_steps, "wrong number of logged steps")
        for s in fetched["steps"]:
            check(s["sources"] and s["input_tokens"] > 0 and s["output_tokens"] > 0, str(s))
        print_run(fetched)

    history = http.get(f"/api/agents/{email_agent['id']}/runs").json()
    check(len(history) == 2, "run history missing")
    print("E2E OK")


if __name__ == "__main__":
    main()
