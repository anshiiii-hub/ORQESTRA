import asyncio
import json
import os
from collections.abc import Sequence
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Identity, current_identity
from .auth import router as auth_router
from .db import SessionLocal, get_db
from .export_contracts import schemas
from .heartbeats import agent_catalog
from .migrate import require_current_schema
from .models import Event, Membership, Task, Workflow
from .planner import demo_plan
from .workspaces import router as workspace_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_current_schema()
    yield


app = FastAPI(title="ORQESTRA AI", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("APP_ORIGIN", "http://localhost:5173")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Last-Event-ID"],
)
app.include_router(auth_router)
app.include_router(workspace_router)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class CreateWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=3, max_length=3000)
    csv_text: str = Field(min_length=3, max_length=250000)


def event(db, wid, kind, details=None):
    db.add(Event(workflow_id=wid, kind=kind, details=details or {}))


def visible_workflow(
    db: Session,
    wid: str,
    identity: Identity,
    roles: tuple[str, ...] = ("owner", "member", "viewer"),
) -> Workflow:
    workflow = db.get(Workflow, wid)
    membership = (
        db.get(Membership, (workflow.workspace_id, identity.user.id))
        if workflow and workflow.workspace_id
        else None
    )
    if workflow is None or membership is None:
        raise HTTPException(404, "Workflow not found")
    if membership.role not in roles:
        raise HTTPException(403, "Your workspace role does not allow this action")
    return workflow


def serialize(w: Workflow, tasks: Sequence[Task]):
    return {
        "id": w.id,
        "workspace_id": w.workspace_id,
        "created_by": w.created_by,
        "prompt": w.prompt,
        "status": w.status,
        "mode": w.mode,
        "output": w.output,
        "error": w.error,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "tasks": [
            {
                "id": t.id,
                "name": t.name,
                "agent": t.agent,
                "capability": t.capability,
                "agent_version": t.agent_version,
                "schema_version": t.schema_version,
                "error_code": t.error_code,
                "execution": t.result_envelope,
                "status": t.status,
                "dependencies": t.dependencies,
                "attempts": t.attempts,
                "result": t.result,
                "error": t.error,
            }
            for t in tasks
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.4.0"}


@app.get("/api/v1/agents")
def list_agents(identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    return agent_catalog(db)


@app.get("/api/v1/agents/contracts")
def agent_contracts(identity: Identity = Depends(current_identity)):
    return schemas()


@app.post("/api/v1/workflows", status_code=202)
def create_workflow(
    body: CreateWorkflow,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, body.workspace_id, identity, ("owner", "member"))
    try:
        plan = demo_plan()
    except ValidationError:
        raise HTTPException(503, "Required demo agents are unavailable") from None
    w = Workflow(
        prompt=body.prompt,
        input_csv=body.csv_text,
        status="queued",
        workspace_id=body.workspace_id,
        created_by=identity.user.id,
    )
    db.add(w)
    db.flush()
    for i, p in enumerate(plan.tasks):
        db.add(
            Task(
                workflow_id=w.id,
                name=p.id,
                agent=p.agent,
                dependencies=p.dependencies,
                ordinal=i,
                capability=p.capability,
                agent_version=p.agent_version,
                schema_version=p.schema_version,
                input_bindings=p.input_bindings,
            )
        )
    event(
        db,
        w.id,
        "workflow_created",
        {"task_count": len(plan.tasks), "planner": "deterministic_demo"},
    )
    db.commit()
    return {"workflow_id": w.id, "status": w.status, "url": f"/api/v1/workflows/{w.id}"}


@app.get("/api/v1/workflows")
def list_workflows(
    workspace_id: str = Query(min_length=1, max_length=36),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity)
    ws = db.scalars(
        select(Workflow)
        .where(Workflow.workspace_id == workspace_id)
        .order_by(Workflow.created_at.desc())
        .limit(50)
    ).all()
    return [
        {"id": w.id, "prompt": w.prompt, "status": w.status, "created_at": w.created_at.isoformat()}
        for w in ws
    ]


@app.get("/api/v1/workflows/{wid}")
def get_workflow(
    wid: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)
):
    w = visible_workflow(db, wid, identity)
    tasks = db.scalars(select(Task).where(Task.workflow_id == wid).order_by(Task.ordinal)).all()
    return serialize(w, tasks)


@app.post("/api/v1/workflows/{wid}/cancel")
def cancel_workflow(
    wid: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)
):
    w = visible_workflow(db, wid, identity, ("owner", "member"))
    if w.status in ("succeeded", "failed", "cancelled"):
        raise HTTPException(409, "Workflow already terminal")
    w.status = "cancelled"
    for t in db.scalars(select(Task).where(Task.workflow_id == wid, Task.status == "pending")):
        t.status = "cancelled"
    event(db, wid, "workflow_cancelled")
    db.commit()
    return {"workflow_id": wid, "status": "cancelled"}


@app.get("/api/v1/workflows/{wid}/events")
def get_events(
    wid: str,
    after: int = Query(default=0, ge=0),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    visible_workflow(db, wid, identity)
    events = db.scalars(
        select(Event)
        .where(Event.workflow_id == wid, Event.id > after)
        .order_by(Event.id)
        .limit(200)
    ).all()
    return [
        {"id": e.id, "kind": e.kind, "details": e.details, "created_at": e.created_at.isoformat()}
        for e in events
    ]


@app.get("/api/v1/workflows/{wid}/stream")
async def stream_events(
    wid: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    visible_workflow(db, wid, identity)
    try:
        cursor = max(after, int(last_event_id or 0))
        if cursor < 0:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Invalid event cursor") from None
    db.close()

    async def events():
        last = cursor
        for _ in range(150):
            with next_db() as stream_db:
                try:
                    live_identity = current_identity(request, stream_db)
                    workflow = visible_workflow(stream_db, wid, live_identity)
                except HTTPException:
                    return
                found = stream_db.scalars(
                    select(Event)
                    .where(Event.workflow_id == wid, Event.id > last)
                    .order_by(Event.id)
                    .limit(100)
                ).all()
                payloads = [
                    (e.id, json.dumps({"kind": e.kind, "details": e.details})) for e in found
                ]
                terminal = workflow.status in ("succeeded", "failed", "cancelled")
            for event_id, payload in payloads:
                yield f"id: {event_id}\nevent: workflow\ndata: {payload}\n\n"
                last = event_id
            if terminal and len(payloads) < 100:
                return
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


def next_db():
    return SessionLocal()
