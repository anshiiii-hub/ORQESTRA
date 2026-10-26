from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    mode: Mapped[str] = mapped_column(String(32), default="csv_demo")
    routing_mode: Mapped[str] = mapped_column(String(16), default="fixed")
    input_csv: Mapped[str] = mapped_column(Text, default="")
    planning: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(64), default="")
    agent_version: Mapped[str] = mapped_column(String(16), default="1.0.0")
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    input_bindings: Mapped[dict] = mapped_column(JSON, default=dict)
    routing_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_envelope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_envelope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number > 0", name="ck_task_attempt_number_positive"),
        UniqueConstraint("task_id", "attempt_number", name="uq_task_attempt_number"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    worker_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    request_envelope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_envelope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        CheckConstraint("task_id <> dependency_task_id", name="ck_task_dependency_not_self"),
        UniqueConstraint("task_id", "dependency_task_id", name="uq_task_dependency_edge"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    dependency_task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    query: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint("run_id", "url", name="uq_research_source_run_url"),
        UniqueConstraint("run_id", "position", name="uq_research_source_run_position"),
        CheckConstraint("trust_level = 'untrusted'", name="ck_research_source_untrusted"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(200), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trust_level: Mapped[str] = mapped_column(String(24), default="untrusted")
    status: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DatasetAnalysis(Base):
    __tablename__ = "dataset_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(8))
    source_size_bytes: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    column_count: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    profile_json: Mapped[dict] = mapped_column(JSON)
    chart_artifacts: Mapped[list] = mapped_column(JSON)
    normalized_csv: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CodeSandboxRun(Base):
    __tablename__ = "code_sandbox_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(24), index=True)
    patch_json: Mapped[dict] = mapped_column(JSON)
    artifact_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member', 'viewer')", name="ck_membership_role"),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    agent_versions: Mapped[dict] = mapped_column(JSON)


class DocumentFile(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    original_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="uploaded", index=True)
    stored_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("file_id", "chunk_index", name="uq_document_chunk_file_index"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    source_offset: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
