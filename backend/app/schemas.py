from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import llm
from app.runner import validate_steps


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_email: str | None = Field(default=None, max_length=320)
    notes: str = ""

    @field_validator("contact_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v and "@" not in v:
            raise ValueError("contact_email must contain @")
        return v or None


class ClientOut(ORM):
    id: int
    name: str
    contact_email: str | None
    notes: str
    created_at: datetime


class Step(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_ -]{1,60}$")
    prompt: str = Field(min_length=1)


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = "offline"
    model: str = "offline-extractive-v1"
    system_prompt: str = ""
    input_kind: Literal["document", "email"]
    steps: list[Step] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _check(self) -> "AgentIn":
        models = llm.PROVIDER_MODELS.get(self.provider)
        if models is None:
            raise ValueError(f"unknown provider {self.provider!r}")
        if self.model not in models:
            raise ValueError(f"model {self.model!r} is not offered by {self.provider!r}")
        validate_steps([s.model_dump() for s in self.steps])
        return self


class AgentOut(ORM):
    id: int
    client_id: int
    name: str
    provider: str
    model: str
    system_prompt: str
    input_kind: str
    steps: list[Step]
    created_at: datetime
    updated_at: datetime


class EmailPaste(BaseModel):
    raw: str = Field(min_length=1)


class InputOut(ORM):
    id: int
    client_id: int
    kind: str
    origin: str
    filename: str | None
    content_type: str | None
    sha256: str
    meta: dict
    text: str
    created_at: datetime


class RunIn(BaseModel):
    input_id: int


class RunStepOut(ORM):
    id: int
    position: int
    name: str
    provider: str
    model: str
    sources: list[str]
    prompt: str
    output: str | None
    status: str
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    started_at: datetime


class RunSummary(ORM):
    id: int
    agent_id: int
    input_id: int
    status: str
    error: str | None
    total_cost_usd: Decimal
    total_latency_ms: int
    total_input_tokens: int
    total_output_tokens: int
    started_at: datetime
    finished_at: datetime | None


class RunOut(RunSummary):
    agent_snapshot: dict
    final_output: str | None
    steps: list[RunStepOut]
