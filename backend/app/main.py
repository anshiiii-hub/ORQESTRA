import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import Base, engine, get_db
from .models import Workflow, Task, Event
from .planner import demo_plan

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Safe for the prototype; use Alembic migrations before schema changes in production.
    Base.metadata.create_all(engine)
    yield

app = FastAPI(title="ORCHESTRA AI Checkpoint 1", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("APP_ORIGIN", "http://localhost:5173")], allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-API-Key"])

def auth(x_api_key: str | None = Header(default=None)):
    key = os.getenv("API_KEY", "local-dev-key-change-before-exposure")
    if x_api_key != key: raise HTTPException(401, "Invalid API key")

class CreateWorkflow(BaseModel):
    prompt: str = Field(min_length=3, max_length=3000)
    csv_text: str = Field(min_length=3, max_length=250000)

def event(db, wid, kind, details=None):
    db.add(Event(workflow_id=wid, kind=kind, details=details or {}))

def serialize(w: Workflow, tasks: list[Task]):
    return {"id": w.id, "prompt": w.prompt, "status": w.status, "mode": w.mode, "output": w.output, "error": w.error,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "tasks": [{"id": t.id, "name": t.name, "agent": t.agent, "status": t.status, "dependencies": t.dependencies, "attempts": t.attempts, "result": t.result, "error": t.error} for t in tasks]}

@app.get("/health")
def health(): return {"status": "ok", "version": "0.1.0"}

@app.post("/api/v1/workflows", status_code=202, dependencies=[Depends(auth)])
def create_workflow(body: CreateWorkflow, db: Session = Depends(get_db)):
    plan = demo_plan()
    w = Workflow(prompt=body.prompt, input_csv=body.csv_text, status="queued")
    db.add(w)
    db.flush()
    for i, p in enumerate(plan.tasks):
        db.add(Task(workflow_id=w.id, name=p.id, agent=p.agent, dependencies=p.dependencies, ordinal=i))
    event(db, w.id, "workflow_created", {"task_count": len(plan.tasks), "planner": "deterministic_demo"})
    db.commit()
    return {"workflow_id": w.id, "status": w.status, "url": f"/api/v1/workflows/{w.id}"}

@app.get("/api/v1/workflows", dependencies=[Depends(auth)])
def list_workflows(db: Session = Depends(get_db)):
    ws = db.scalars(select(Workflow).order_by(Workflow.created_at.desc()).limit(50)).all()
    return [{"id": w.id, "prompt": w.prompt, "status": w.status, "created_at": w.created_at.isoformat()} for w in ws]

@app.get("/api/v1/workflows/{wid}", dependencies=[Depends(auth)])
def get_workflow(wid: str, db: Session = Depends(get_db)):
    w = db.get(Workflow, wid)
    if not w: raise HTTPException(404, "Workflow not found")
    tasks = db.scalars(select(Task).where(Task.workflow_id == wid).order_by(Task.ordinal)).all()
    return serialize(w, tasks)

@app.post("/api/v1/workflows/{wid}/cancel", dependencies=[Depends(auth)])
def cancel_workflow(wid: str, db: Session = Depends(get_db)):
    w = db.get(Workflow, wid)
    if not w: raise HTTPException(404, "Workflow not found")
    if w.status in ("succeeded", "failed", "cancelled"): raise HTTPException(409, "Workflow already terminal")
    w.status = "cancelled"
    for t in db.scalars(select(Task).where(Task.workflow_id == wid, Task.status == "pending")):
        t.status = "cancelled"
    event(db, wid, "workflow_cancelled")
    db.commit()
    return {"workflow_id": wid, "status": "cancelled"}

@app.get("/api/v1/workflows/{wid}/events", dependencies=[Depends(auth)])
def get_events(wid: str, after: int = 0, db: Session = Depends(get_db)):
    if not db.get(Workflow, wid): raise HTTPException(404, "Workflow not found")
    events = db.scalars(select(Event).where(Event.workflow_id == wid, Event.id > after).order_by(Event.id).limit(200)).all()
    return [{"id": e.id, "kind": e.kind, "details": e.details, "created_at": e.created_at.isoformat()} for e in events]

@app.get("/api/v1/workflows/{wid}/stream", dependencies=[Depends(auth)])
async def stream_events(wid: str, after: int = 0):
    # This prototype endpoint uses a header token, not query-string credentials.
    async def events():
        last = after
        for _ in range(150):
            with next_db() as db:
                found = db.scalars(select(Event).where(Event.workflow_id == wid, Event.id > last).order_by(Event.id).limit(100)).all()
                for e in found:
                    import json
                    yield f"id: {e.id}\nevent: workflow\ndata: {json.dumps({'kind':e.kind,'details':e.details})}\n\n"
                    last = e.id
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="text/event-stream")

def next_db():
    from .db import SessionLocal
    return SessionLocal()
