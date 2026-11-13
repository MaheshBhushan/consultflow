import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import llm, runner
from app.db import get_session, init_db
from app.ingest import IngestError, Parsed, parse_document, parse_email
from app.models import Agent, Client, InputSource, Run
from app.schemas import (
    AgentIn,
    AgentOut,
    ClientIn,
    ClientOut,
    EmailPaste,
    InputOut,
    RunIn,
    RunOut,
    RunSummary,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="ConsultFlow", lifespan=lifespan)
api = APIRouter(prefix="/api")
DB = Depends(get_session)


def _get(session: Session, model, obj_id: int):
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {obj_id} not found")
    return obj


@api.get("/health")
def health() -> dict:
    return {"status": "ok"}


@api.get("/providers")
def providers() -> dict:
    return llm.available_providers()


@api.post("/clients", response_model=ClientOut, status_code=201)
def create_client(body: ClientIn, session: Session = DB):
    client = Client(**body.model_dump())
    session.add(client)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, f"client {body.name!r} already exists") from exc
    return client


@api.get("/clients", response_model=list[ClientOut])
def list_clients(session: Session = DB):
    return session.scalars(select(Client).order_by(Client.name)).all()


@api.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: int, session: Session = DB):
    return _get(session, Client, client_id)


@api.post("/clients/{client_id}/agents", response_model=AgentOut, status_code=201)
def create_agent(client_id: int, body: AgentIn, session: Session = DB):
    _get(session, Client, client_id)
    data = body.model_dump()
    agent = Agent(client_id=client_id, **data)
    session.add(agent)
    session.commit()
    return agent


@api.get("/clients/{client_id}/agents", response_model=list[AgentOut])
def list_agents(client_id: int, session: Session = DB):
    _get(session, Client, client_id)
    return session.scalars(
        select(Agent).where(Agent.client_id == client_id).order_by(Agent.id)
    ).all()


@api.get("/agents/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, session: Session = DB):
    return _get(session, Agent, agent_id)


@api.put("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, body: AgentIn, session: Session = DB):
    agent = _get(session, Agent, agent_id)
    for key, value in body.model_dump().items():
        setattr(agent, key, value)
    session.commit()
    return agent


def _store_input(session: Session, client_id: int, parsed: Parsed, origin: str, **extra):
    _get(session, Client, client_id)
    src = InputSource(
        client_id=client_id,
        kind=parsed.kind,
        origin=origin,
        sha256=parsed.sha256,
        text=parsed.text,
        meta=parsed.meta,
        **extra,
    )
    session.add(src)
    session.commit()
    return src


@api.post("/clients/{client_id}/inputs/upload", response_model=InputOut, status_code=201)
async def upload_input(client_id: int, file: UploadFile, session: Session = DB):
    data = await file.read()
    try:
        parsed = parse_document(file.filename or "", data)
    except IngestError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _store_input(
        session,
        client_id,
        parsed,
        "upload",
        filename=file.filename,
        content_type=file.content_type,
    )


@api.post("/clients/{client_id}/inputs/email", response_model=InputOut, status_code=201)
def paste_email(client_id: int, body: EmailPaste, session: Session = DB):
    try:
        parsed = parse_email(body.raw)
    except IngestError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _store_input(session, client_id, parsed, "paste", content_type="message/rfc822")


@api.get("/clients/{client_id}/inputs", response_model=list[InputOut])
def list_inputs(client_id: int, session: Session = DB):
    _get(session, Client, client_id)
    return session.scalars(
        select(InputSource).where(InputSource.client_id == client_id).order_by(InputSource.id)
    ).all()


@api.post("/agents/{agent_id}/runs", response_model=RunOut, status_code=201)
def start_run(agent_id: int, body: RunIn, session: Session = DB):
    agent = _get(session, Agent, agent_id)
    src = _get(session, InputSource, body.input_id)
    if src.client_id != agent.client_id:
        raise HTTPException(422, "input belongs to a different client")
    if src.kind != agent.input_kind:
        raise HTTPException(422, f"agent expects a {agent.input_kind}, input is a {src.kind}")
    return runner.execute_run(session, agent, src)


@api.get("/agents/{agent_id}/runs", response_model=list[RunSummary])
def list_runs(agent_id: int, session: Session = DB):
    _get(session, Agent, agent_id)
    return session.scalars(
        select(Run).where(Run.agent_id == agent_id).order_by(Run.id.desc())
    ).all()


@api.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: Session = DB):
    return _get(session, Run, run_id)


app.include_router(api)

STATIC_DIR = Path(
    os.environ.get("STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend/dist")
)
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(STATIC_DIR / "index.html")
