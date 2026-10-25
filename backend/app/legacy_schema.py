"""Frozen Checkpoint 01 metadata used only to validate unversioned adoption."""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)


def checkpoint_one_metadata() -> MetaData:
    metadata = MetaData()
    Table(
        "workflows",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("prompt", Text, nullable=False),
        Column("status", String(32), nullable=False),
        Column("mode", String(32), nullable=False),
        Column("input_csv", Text, nullable=False),
        Column("output", JSON),
        Column("error", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Index("ix_workflows_status", "status"),
    )
    Table(
        "tasks",
        metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "workflow_id",
            String(36),
            ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("name", String(64), nullable=False),
        Column("agent", String(64), nullable=False),
        Column("status", String(32), nullable=False),
        Column("dependencies", JSON, nullable=False),
        Column("result", JSON),
        Column("error", Text),
        Column("attempts", Integer, nullable=False),
        Column("ordinal", Integer, nullable=False),
        Index("ix_tasks_workflow_id", "workflow_id"),
    )
    Table(
        "events",
        metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "workflow_id",
            String(36),
            ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("kind", String(80), nullable=False),
        Column("details", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Index("ix_events_workflow_id", "workflow_id"),
    )
    return metadata
