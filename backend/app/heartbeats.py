"""Observational worker liveness, separate from task leases."""

from datetime import timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import WorkerHeartbeat, now
from .registry import REGISTRY, AgentRegistry

WORKER_ID = str(uuid4())
WORKER_STARTED_AT = now()
HEARTBEAT_INTERVAL = 5
HEARTBEAT_TTL = 30


def record_heartbeat(
    db: Session, registry: AgentRegistry = REGISTRY, worker_id: str = WORKER_ID
) -> None:
    heartbeat = db.get(WorkerHeartbeat, worker_id)
    if heartbeat is None:
        heartbeat = WorkerHeartbeat(id=worker_id, started_at=WORKER_STARTED_AT)
        db.add(heartbeat)
    heartbeat.seen_at = now()
    heartbeat.agent_versions = {
        agent.agent_id: {"agent_version": agent.version, "schema_version": agent.schema_version}
        for agent in registry.all()
        if agent.enabled
    }


def agent_catalog(db: Session, registry: AgentRegistry = REGISTRY) -> list[dict]:
    cutoff = now() - timedelta(seconds=HEARTBEAT_TTL)
    live = db.scalars(select(WorkerHeartbeat).where(WorkerHeartbeat.seen_at >= cutoff)).all()
    catalog = []
    for agent in registry.all():
        workers = [
            heartbeat
            for heartbeat in live
            if heartbeat.agent_versions.get(agent.agent_id)
            == {"agent_version": agent.version, "schema_version": agent.schema_version}
        ]
        seen = max(
            (
                heartbeat.seen_at.replace(tzinfo=timezone.utc)
                if heartbeat.seen_at.tzinfo is None
                else heartbeat.seen_at
                for heartbeat in workers
            ),
            default=None,
        )
        catalog.append(
            {
                **agent.metadata(),
                "availability": "disabled"
                if not agent.enabled
                else "ready"
                if workers
                else "offline",
                "available_workers": len(workers) if agent.enabled else 0,
                "last_seen_at": seen.isoformat() if seen else None,
            }
        )
    return catalog
