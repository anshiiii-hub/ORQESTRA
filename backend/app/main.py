import asyncio
import json
import os
from collections.abc import Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Identity, current_identity
from .auth import router as auth_router
from .db import SessionLocal, get_db
from .export_contracts import schemas
from .heartbeats import agent_catalog
from .llm import PlanningError, planner_settings
from .migrate import require_current_schema
from .models import Event, Membership, Task, TaskAttempt, Workflow, now
from .planner import demo_plan
from .planning import PlannerProposal
from .planning_runtime import add_tasks, initial_state
from .workspaces import router as workspace_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_current_schema()
    yield


app = FastAPI(title="ORQESTRA AI", version="0.7.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("APP_ORIGIN", "http://localhost:5173")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Last-Event-ID"],
)
app.include_router(auth_router)
app.include_router(workspace_router)


@app.exception_handler(RequestValidationError)
async def safe_validation_errors(request: Request, error: RequestValidationError):
    # Do not echo credentials, raw payloads, or nonfinite JSON values in error responses.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "loc": [str(part)[:64] for part in item["loc"]],
                    "msg": item["msg"][:200],
                    "type": item["type"],
                }
                for item in error.errors()[:20]
            ]
        },
    )


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
    planner_mode: Literal["offline", "llm"] = "offline"
    routing_mode: Literal["fixed", "adaptive"] = "fixed"
    planning_max_tokens: int = Field(default=20000, ge=512, le=64000, strict=True)
    planning_max_cost_usd: float = Field(default=0.05, gt=0, le=1, allow_inf_nan=False, strict=True)
    planning_deadline_seconds: int = Field(default=60, ge=5, le=180, strict=True)


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


def serialize(
    w: Workflow, tasks: Sequence[Task], attempts_by_task: dict[str, list[TaskAttempt]] | None = None
):
    attempts_by_task = attempts_by_task or {}
    return {
        "id": w.id,
        "workspace_id": w.workspace_id,
        "created_by": w.created_by,
        "prompt": w.prompt,
        "status": w.status,
        "mode": w.mode,
        "routing_mode": w.routing_mode,
        "output": w.output,
        "error": w.error,
        "planning": w.planning,
        "plan": w.execution_plan,
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
                "routing_decision": t.routing_decision,
                "execution_attempts": [
                    {
                        "number": attempt.attempt_number,
                        "status": attempt.status,
                        "error_code": attempt.error_code,
                        "started_at": attempt.started_at.isoformat()
                        if attempt.started_at
                        else None,
                        "finished_at": attempt.finished_at.isoformat()
                        if attempt.finished_at
                        else None,
                    }
                    for attempt in attempts_by_task.get(t.id, [])
                ],
                "result": t.result,
                "error": t.error,
            }
            for t in tasks
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.7.0"}


@app.get("/api/v1/planner/config")
def planner_configuration(identity: Identity = Depends(current_identity)):
    try:
        settings = planner_settings()
        return {
            "llm_available": settings.enabled,
            "model": settings.model if settings.enabled else None,
            "provider": "openai" if settings.enabled else None,
            "max_attempts": 2,
            "max_tokens": 64000,
            "max_cost_usd": 1,
            "max_deadline_seconds": 180,
        }
    except PlanningError:
        return {
            "llm_available": False,
            "model": None,
            "provider": None,
            "max_attempts": 2,
            "max_tokens": 64000,
            "max_cost_usd": 1,
            "max_deadline_seconds": 180,
        }


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
    settings = None
    if body.planner_mode == "llm":
        try:
            settings = planner_settings()
            if not settings.enabled:
                raise PlanningError("provider_not_configured", "LLM planning is not configured")
        except PlanningError:
            raise HTTPException(503, "LLM planning is not configured") from None
    w = Workflow(
        prompt=body.prompt,
        input_csv=body.csv_text,
        status="queued",
        workspace_id=body.workspace_id,
        created_by=identity.user.id,
        mode="csv_llm_plan" if settings else "csv_demo",
        routing_mode=body.routing_mode,
    )
    db.add(w)
    db.flush()
    if settings:
        w.planning = initial_state(
            settings,
            body.planning_max_tokens,
            body.planning_max_cost_usd,
            body.planning_deadline_seconds,
        )
    else:
        try:
            add_tasks(
                db,
                w,
                PlannerProposal(schema_version="1.0", tasks=plan.tasks, final_task_id="verify"),
            )
        except PlanningError:
            raise HTTPException(503, "No agent satisfies the selected routing policy") from None
        w.planning = {
            "schema_version": "1.0",
            "provider": "offline",
            "model": None,
            "status": "succeeded",
            "attempts": [],
            "accounted_tokens": 0,
            "accounted_cost_usd": "0",
            "error_code": None,
        }
    event(
        db,
        w.id,
        "workflow_created",
        {
            "task_count": 0 if settings else len(plan.tasks),
            "planner": "openai" if settings else "offline",
        },
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
    attempts = db.scalars(
        select(TaskAttempt)
        .where(TaskAttempt.workflow_id == wid)
        .order_by(TaskAttempt.attempt_number)
    ).all()
    attempts_by_task: dict[str, list[TaskAttempt]] = {}
    for attempt in attempts:
        attempts_by_task.setdefault(attempt.task_id, []).append(attempt)
    return serialize(w, tasks, attempts_by_task)


@app.get("/api/v1/workflows/{wid}/plan")
def get_workflow_plan(
    wid: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)
):
    workflow = visible_workflow(db, wid, identity)
    return {"workflow_id": wid, "planning": workflow.planning, "plan": workflow.execution_plan}


@app.post("/api/v1/workflows/{wid}/cancel")
def cancel_workflow(
    wid: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)
):
    w = visible_workflow(db, wid, identity, ("owner", "member"))
    if w.status in ("succeeded", "failed", "cancelled"):
        raise HTTPException(409, "Workflow already terminal")
    w.status = "cancelled"
    if w.planning and w.planning.get("status") in ("pending", "running"):
        state = deepcopy(w.planning)
        state["status"] = "cancelled"
        w.planning = state
    for t in db.scalars(
        select(Task).where(Task.workflow_id == wid, Task.status.in_(["pending", "running"]))
    ):
        if t.current_attempt_id:
            attempt = db.get(TaskAttempt, t.current_attempt_id)
            if attempt is not None and attempt.status == "running":
                attempt.status = "cancelled"
                attempt.error_code = "workflow_cancelled"
                attempt.finished_at = now()
        t.status = "cancelled"
        t.current_attempt_id = t.lease_owner = None
        t.lease_expires_at = None
        event(db, wid, "task_cancelled", {"task": t.name})
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
