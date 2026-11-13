from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def now() -> datetime:
    return datetime.now(UTC)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=now)

    agents: Mapped[list["Agent"]] = relationship(back_populates="client", cascade="all, delete")
    inputs: Mapped[list["InputSource"]] = relationship(
        back_populates="client", cascade="all, delete"
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    input_kind: Mapped[str] = mapped_column(String(20))  # "document" | "email"
    # Ordered list of {"name": str, "prompt": str}
    steps: Mapped[list[dict]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)

    client: Mapped[Client] = relationship(back_populates="agents")
    runs: Mapped[list["Run"]] = relationship(back_populates="agent", cascade="all, delete")


class InputSource(Base):
    """An uploaded document or an email (pasted text or .eml upload) owned by a client."""

    __tablename__ = "input_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))  # "document" | "email"
    origin: Mapped[str] = mapped_column(String(20))  # "upload" | "paste"
    filename: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)

    client: Mapped[Client] = relationship(back_populates="inputs")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    input_id: Mapped[int] = mapped_column(ForeignKey("input_sources.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="running")
    # Snapshot of the agent config at run time, so later edits do not rewrite history.
    agent_snapshot: Mapped[dict] = mapped_column(JsonType)
    final_output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    total_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]

    agent: Mapped[Agent] = relationship(back_populates="runs")
    input: Mapped[InputSource] = relationship()
    steps: Mapped[list["RunStep"]] = relationship(
        back_populates="run", cascade="all, delete", order_by="RunStep.position"
    )


class RunStep(Base):
    """One LLM call inside a run, with the cost/latency/source log for that call."""

    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    # Which data the rendered prompt consumed, e.g. ["email:#3 <msg-id>", "step:1 summarize"]
    sources: Mapped[list[str]] = mapped_column(JsonType)
    prompt: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(default=now)

    run: Mapped[Run] = relationship(back_populates="steps")
