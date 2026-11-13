"""Executes an agent's steps against one input and logs every call."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app import llm
from app.models import Agent, InputSource, Run, RunStep

VAR_RE = re.compile(r"\{\{\s*([a-z_]+(?:\.[A-Za-z0-9_ -]+)?)\s*\}\}")
META_VARS = {"subject", "sender", "filename", "client"}


def template_vars(prompt: str) -> list[str]:
    return VAR_RE.findall(prompt)


def validate_steps(steps: list[dict]) -> None:
    """Raise ValueError if a prompt references something unavailable at that point."""
    if not steps:
        raise ValueError("an agent needs at least one step")
    seen: list[str] = []
    for i, step in enumerate(steps):
        name = step["name"]
        if name in seen:
            raise ValueError(f"duplicate step name {name!r}")
        for var in template_vars(step["prompt"]):
            if var == "previous" and i == 0:
                raise ValueError(f"step {name!r}: {{{{previous}}}} is not available in step 1")
            if var.startswith("step."):
                ref = var[5:].strip()
                if ref not in seen:
                    raise ValueError(f"step {name!r}: {{{{{var}}}}} must reference an earlier step")
            elif var not in {"input", "previous"} | META_VARS:
                raise ValueError(f"step {name!r}: unknown variable {{{{{var}}}}}")
        seen.append(name)


def input_ref(src: InputSource) -> str:
    label = src.filename or src.meta.get("message_id") or src.meta.get("subject") or "pasted"
    return f"{src.kind}:#{src.id} {label} sha256:{src.sha256[:12]}"


def render(
    prompt: str, src: InputSource, client_name: str, outputs: list[tuple[str, str]]
) -> tuple[str, list[str]]:
    """Fill template variables; return the prompt and the sources it consumed."""
    sources: list[str] = []

    def add(s: str) -> None:
        if s not in sources:
            sources.append(s)

    def sub(m: re.Match) -> str:
        var = m.group(1)
        if var == "input":
            add(input_ref(src))
            return src.text
        if var == "previous":
            name, text = outputs[-1]
            add(f"step:{len(outputs)} {name}")
            return text
        if var.startswith("step."):
            ref = var[5:].strip()
            for pos, (name, text) in enumerate(outputs, start=1):
                if name == ref:
                    add(f"step:{pos} {name}")
                    return text
        if var in META_VARS:
            if var == "client":
                return client_name
            add(f"{src.kind}-meta:#{src.id}")
            value = {
                "subject": src.meta.get("subject"),
                "sender": src.meta.get("from"),
                "filename": src.filename,
            }[var]
            return value or ""
        raise ValueError(f"unresolvable variable {var}")

    return VAR_RE.sub(sub, prompt), sources


def execute_run(
    session: Session,
    agent: Agent,
    src: InputSource,
    provider_factory: Callable[[str], object] | None = None,
) -> Run:
    snapshot = {
        "name": agent.name,
        "provider": agent.provider,
        "model": agent.model,
        "system_prompt": agent.system_prompt,
        "input_kind": agent.input_kind,
        "steps": agent.steps,
    }
    run = Run(agent_id=agent.id, input_id=src.id, status="running", agent_snapshot=snapshot)
    session.add(run)
    session.flush()

    outputs: list[tuple[str, str]] = []
    try:
        provider = (provider_factory or llm.get_provider)(agent.provider)
    except llm.LLMError as exc:
        provider, run.status, run.error = None, "failed", str(exc)

    if provider is not None:
        for pos, step in enumerate(agent.steps, start=1):
            prompt, sources = render(step["prompt"], src, agent.client.name, outputs)
            row = RunStep(
                run_id=run.id,
                position=pos,
                name=step["name"],
                provider=agent.provider,
                model=agent.model,
                sources=sources,
                prompt=prompt,
                status="running",
            )
            t0 = time.perf_counter()
            try:
                result = provider.complete(agent.model, agent.system_prompt, prompt)
            except Exception as exc:  # provider/network errors are recorded, not raised
                row.latency_ms = round((time.perf_counter() - t0) * 1000)
                row.status, row.error = "failed", str(exc)
                session.add(row)
                run.status, run.error = "failed", f"step {pos} ({step['name']}) failed: {exc}"
                break
            row.latency_ms = round((time.perf_counter() - t0) * 1000)
            row.output = result.text
            row.input_tokens = result.input_tokens
            row.output_tokens = result.output_tokens
            row.cost_usd = llm.cost_usd(agent.model, result.input_tokens, result.output_tokens)
            row.status = "succeeded"
            session.add(row)
            outputs.append((step["name"], result.text))
        else:
            run.status = "succeeded"
            run.final_output = outputs[-1][1] if outputs else ""

    session.flush()
    session.refresh(run)
    run.total_cost_usd = sum((s.cost_usd for s in run.steps), Decimal(0))
    run.total_latency_ms = sum(s.latency_ms for s in run.steps)
    run.total_input_tokens = sum(s.input_tokens for s in run.steps)
    run.total_output_tokens = sum(s.output_tokens for s in run.steps)
    run.finished_at = datetime.now(UTC)
    session.commit()
    session.refresh(run)
    return run
