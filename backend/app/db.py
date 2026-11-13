import os
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_URL = "postgresql+psycopg://consultflow:consultflow@localhost:55441/consultflow"


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


engine = create_engine(database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
