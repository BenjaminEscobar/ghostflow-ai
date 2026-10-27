"""Persistence: PostgreSQL in Docker Compose, SQLite for local development and tests.

Documents (recordings, workflow definitions, run results) are stored as JSON columns; the
Pydantic models are the schema. Tables are created on startup (Alembic is in the backlog).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

JsonDoc = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RecordingRow(Base):
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    event_count: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JsonDoc)


class WorkflowRow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    source_recording_id: Mapped[str | None] = mapped_column(ForeignKey("recordings.id"), nullable=True)
    annotated_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    definition: Mapped[dict] = mapped_column(JsonDoc)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(16))
    inputs: Mapped[dict] = mapped_column(JsonDoc)  # sensitive values replaced by "***"
    result: Mapped[dict] = mapped_column(JsonDoc)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def list_rows(session: Session, model, limit: int = 100, **filters):
    stmt = select(model).filter_by(**filters).order_by(model.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))
