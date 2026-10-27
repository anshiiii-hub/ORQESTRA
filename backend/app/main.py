import asyncio
import json
import os
from collections.abc import Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Literal

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .approvals import (
    REGISTERED_TOOLS,
    ApprovalDecisionRequest,
    ApprovalError,
    ToolExecutionRequest,
    check_and_expire_approvals,
    decide_approval,
    execute_gated_tool,
    serialize_approval,
)
from .auth import Identity, current_identity
from .auth import router as auth_router
from .benchmarks import execute_benchmark_suite, get_benchmark_summary
from .data_analysis import (
    MAX_UPLOAD_BYTES,
    AnalysisError,
    analyze_dataset,
    validate_upload_filename,
)
from .db import SessionLocal, get_db
from .export_contracts import schemas
from .heartbeats import agent_catalog
from .knowledge import (
    MAX_UPLOAD_BYTES as MAX_KNOWLEDGE_BYTES,
)
from .knowledge import (
    KnowledgeError,
    KnowledgeSearchRequest,
    extract_text_and_validate,
    index_file_chunks,
    knowledge_settings,
    search_workspace_chunks,
    serialize_document_file,
)
from .knowledge import (
    validate_upload_filename as validate_knowledge_filename,
)
from .llm import PlanningError, planner_settings
from .migrate import migration_config, require_current_schema
from .models import (
    Approval,
    Artifact,
    BenchmarkResult,
    CodeSandboxRun,
    DatasetAnalysis,
    DocumentChunk,
    DocumentFile,
    Event,
    Membership,
    ResearchRun,
    ResearchSource,
    Task,
    TaskAttempt,
    User,
    VerificationReport,
    WorkerHeartbeat,
    Workflow,
    now,
)
from .planner import demo_plan
from .planning import PlannerProposal
from .planning_runtime import add_tasks, initial_state
from .recovery import (
    RecoveryError,
    get_workflow_checkpoint,
    pause_workflow,
    resume_workflow,
    retry_workflow,
)
from .reporting import (
    ReportingError,
    ReportSynthesizeRequest,
    VerificationRequest,
    serialize_artifact,
    serialize_verification_report,
    synthesize_workflow_report,
    verify_deliverable,
)
from .research import (
    ResearchAgent,
    ResearchError,
    ResearchGateway,
    ResearchSearchRequest,
    research_settings,
)
from .sandbox import (
    MAX_CHECKS,
    MAX_CONCURRENT_RUNS,
    MAX_FILES,
    MAX_INPUT_BYTES,
    MAX_JSON_REQUEST_BYTES,
    MAX_PATCH_FILES,
    SandboxController,
    SandboxError,
    SandboxRunRequest,
    sandbox_runtime_configured,
)
from .workspaces import router as workspace_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_current_schema()
    yield


app = FastAPI(title="ORQESTRA AI", version="1.0.0", lifespan=lifespan)
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


class WorkflowRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preserve_checkpoint: bool = True


class BenchmarkExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=36)
    set_id: str | None = None
    routing_mode: Literal["single", "fixed", "adaptive"] = "fixed"
    replicates: int = Field(default=3, ge=3, le=5, strict=True)


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
        "recovery_metadata": w.recovery_metadata,
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
@app.get("/healthz")
def health():
    return {
        "status": "ok",
        "service": "orqestra-api",
        "version": "1.0.0",
        "timestamp": now().isoformat(),
    }


@app.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        current_heads = MigrationContext.configure(db.connection()).get_current_heads()
        expected_heads = ScriptDirectory.from_config(migration_config()).get_heads()
    except Exception:
        raise HTTPException(503, "Database readiness check failed") from None

    if set(current_heads) != set(expected_heads):
        raise HTTPException(503, "Database schema is not current")

    active_threshold = now() - timedelta(seconds=60)
    active_workers = (
        db.scalar(
            select(func.count(WorkerHeartbeat.id)).where(
                WorkerHeartbeat.seen_at >= active_threshold
            )
        )
        or 0
    )
    return {
        "status": "ready",
        "database": "connected",
        "schema_revision": ",".join(sorted(current_heads)),
        "active_workers": active_workers,
        "timestamp": now().isoformat(),
    }


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


def serialize_research(run: ResearchRun, sources: Sequence[ResearchSource]):
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "query": run.query,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "sources": [
            {
                "id": source.id,
                "position": source.position,
                "url": source.url,
                "title": source.title,
                "excerpt": source.excerpt,
                "content_type": source.content_type,
                "content_sha256": source.content_sha256,
                "fetched_at": source.fetched_at.isoformat() if source.fetched_at else None,
                "trust_level": source.trust_level,
                "status": source.status,
                "error_code": source.error_code,
            }
            for source in sources
        ],
    }


@app.get("/api/v1/research/config")
def research_configuration(identity: Identity = Depends(current_identity)):
    try:
        settings = research_settings()
        available = settings.available
    except ResearchError:
        available = False
    return {
        "available": available,
        "max_urls": 3,
        "max_response_bytes": 262144,
        "max_timeout_seconds": 6,
        "content_trust": "untrusted",
    }


@app.post("/api/v1/research/search", status_code=201)
def search_research_sources(
    body: ResearchSearchRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, body.workspace_id, identity, ("owner", "member"))
    try:
        gateway = ResearchGateway(research_settings())
        agent = ResearchAgent(gateway)
        prepared = gateway.prepare(body.urls)
    except ResearchError as error:
        status_code = (
            503 if error.code in ("research_unavailable", "research_not_configured") else 422
        )
        raise HTTPException(status_code, str(error)) from None

    cleaned_query = " ".join(body.query.split())
    run = ResearchRun(
        workspace_id=body.workspace_id,
        created_by=identity.user.id,
        query=cleaned_query,
        status="running",
    )
    db.add(run)
    db.commit()
    try:
        records = agent.search_prepared(cleaned_query, prepared)
    except ResearchError as error:
        run.status, run.finished_at = "failed", now()
        db.commit()
        raise HTTPException(422, str(error)) from None
    except Exception:
        run.status, run.finished_at = "failed", now()
        db.commit()
        raise HTTPException(502, "Research sources could not be processed safely") from None

    for position, record in enumerate(records):
        db.add(
            ResearchSource(
                id=record.id,
                run_id=run.id,
                position=position,
                url=record.url,
                title=record.title,
                excerpt=record.excerpt,
                content_type=record.content_type,
                content_sha256=record.content_sha256,
                fetched_at=datetime.fromisoformat(record.fetched_at) if record.fetched_at else None,
                trust_level=record.trust_level,
                status=record.status,
                error_code=record.error_code,
            )
        )
    successful = sum(record.status == "fetched" for record in records)
    run.status = (
        "completed" if successful == len(records) else "partial" if successful else "failed"
    )
    run.finished_at = now()
    db.commit()
    db.refresh(run)
    sources = db.scalars(
        select(ResearchSource)
        .where(ResearchSource.run_id == run.id)
        .order_by(ResearchSource.position)
    ).all()
    return serialize_research(run, sources)


@app.get("/api/v1/research/runs/{run_id}")
def get_research_run(
    run_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    run = db.get(ResearchRun, run_id)
    membership = (
        db.get(Membership, (run.workspace_id, identity.user.id)) if run is not None else None
    )
    if run is None or membership is None:
        raise HTTPException(404, "Research run not found")
    sources = db.scalars(
        select(ResearchSource)
        .where(ResearchSource.run_id == run_id)
        .order_by(ResearchSource.position)
    ).all()
    return serialize_research(run, sources)


def serialize_dataset_analysis(dataset: DatasetAnalysis):
    return {
        "id": dataset.id,
        "workspace_id": dataset.workspace_id,
        "filename": dataset.filename,
        "file_format": dataset.file_format,
        "source_size_bytes": dataset.source_size_bytes,
        "content_sha256": dataset.content_sha256,
        "row_count": dataset.row_count,
        "column_count": dataset.column_count,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "metadata": dataset.metadata_json,
        "profile": dataset.profile_json,
        "chart_artifacts": [
            {"id": chart["id"], "title": chart["title"]} for chart in dataset.chart_artifacts
        ],
        "csv_export_url": f"/api/v1/data-analysis/{dataset.id}/export.csv",
    }


@app.post("/api/v1/data-analysis/import", status_code=201)
async def import_dataset(
    request: Request,
    workspace_id: str = Query(min_length=1, max_length=36),
    filename: str = Query(min_length=1, max_length=255),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity, ("owner", "member"))
    try:
        safe_name = validate_upload_filename(filename)
    except AnalysisError as error:
        raise HTTPException(422, str(error)) from None
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "The upload exceeds the 5 MiB limit")
        except ValueError:
            raise HTTPException(400, "Invalid upload length") from None
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The upload exceeds the 5 MiB limit")
    try:
        result = analyze_dataset(safe_name, bytes(content))
    except AnalysisError as error:
        status = 413 if error.code in ("file_too_large", "xlsx_expanded_too_large") else 422
        raise HTTPException(status, str(error)) from None
    dataset = DatasetAnalysis(
        workspace_id=workspace_id,
        created_by=identity.user.id,
        filename=result.filename,
        file_format=result.file_format,
        source_size_bytes=result.source_size_bytes,
        content_sha256=result.content_sha256,
        row_count=result.row_count,
        column_count=result.column_count,
        metadata_json=result.metadata,
        profile_json=result.profile,
        chart_artifacts=result.chart_artifacts,
        normalized_csv=result.normalized_csv,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return serialize_dataset_analysis(dataset)


def visible_dataset_analysis(db: Session, dataset_id: str, identity: Identity):
    dataset = db.get(DatasetAnalysis, dataset_id)
    membership = (
        db.get(Membership, (dataset.workspace_id, identity.user.id))
        if dataset is not None
        else None
    )
    if dataset is None or membership is None:
        raise HTTPException(404, "Dataset analysis not found")
    return dataset


@app.get("/api/v1/data-analysis/{dataset_id}")
def get_dataset_analysis(
    dataset_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    dataset = visible_dataset_analysis(db, dataset_id, identity)
    return serialize_dataset_analysis(dataset) | {
        "profile": dataset.profile_json,
        "metadata": dataset.metadata_json,
    }


@app.get("/api/v1/data-analysis/{dataset_id}/export.csv")
def export_dataset_csv(
    dataset_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    dataset = visible_dataset_analysis(db, dataset_id, identity)
    return Response(
        dataset.normalized_csv,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dataset.id}.csv"'},
    )


@app.get("/api/v1/data-analysis/{dataset_id}/charts/{chart_id}.svg")
def get_dataset_chart(
    dataset_id: str,
    chart_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    dataset = visible_dataset_analysis(db, dataset_id, identity)
    chart = next((item for item in dataset.chart_artifacts if item["id"] == chart_id), None)
    if chart is None:
        raise HTTPException(404, "Chart not found")
    return Response(chart["svg"], media_type="image/svg+xml")


@app.get("/api/v1/sandbox/config")
def sandbox_configuration(identity: Identity = Depends(current_identity)):
    return {
        "available": sandbox_runtime_configured(),
        "max_files": MAX_FILES,
        "max_patch_files": MAX_PATCH_FILES,
        "max_input_bytes": MAX_INPUT_BYTES,
        "max_checks": MAX_CHECKS,
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
        "checks": ["pytest", "unittest", "compileall"],
        "memory_bytes": 256 * 1024 * 1024,
        "cpu_cores": 0.5,
        "pids": 32,
        "network": "none",
        "host_mounts": 0,
    }


@app.post("/api/v1/sandbox/run", status_code=201)
async def run_code_sandbox(
    request: Request,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_JSON_REQUEST_BYTES:
                raise HTTPException(413, "The sandbox request is too large")
        except ValueError:
            raise HTTPException(400, "Invalid request length") from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_JSON_REQUEST_BYTES:
            raise HTTPException(413, "The sandbox request is too large")
    try:
        payload = SandboxRunRequest.model_validate_json(bytes(body))
    except (ValueError, ValidationError):
        raise HTTPException(422, "The sandbox request is invalid") from None

    from .auth import require_role

    require_role(db, payload.workspace_id, identity, ("owner", "member"))
    if not sandbox_runtime_configured():
        raise HTTPException(503, "The isolated sandbox runtime is disabled or unavailable")
    try:
        result = await asyncio.to_thread(SandboxController().run, payload)
    except SandboxError as error:
        status = 422 if error.code == "sandbox_diff_too_large" else 503
        raise HTTPException(status, str(error)) from None

    run = CodeSandboxRun(
        workspace_id=payload.workspace_id,
        created_by=identity.user.id,
        status=result["status"],
        patch_json={"diff": result["diff"]},
        artifact_json=result,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return serialize_sandbox_run(run)


def serialize_sandbox_run(run: CodeSandboxRun):
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "status": run.status,
        "diff": run.patch_json["diff"],
        "artifact": run.artifact_json,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@app.get("/api/v1/sandbox/runs/{run_id}")
def get_sandbox_run(
    run_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    run = db.get(CodeSandboxRun, run_id)
    membership = (
        db.get(Membership, (run.workspace_id, identity.user.id)) if run is not None else None
    )
    if run is None or membership is None:
        raise HTTPException(404, "Sandbox run not found")
    return serialize_sandbox_run(run)


@app.get("/api/v1/knowledge/config")
def knowledge_configuration(identity: Identity = Depends(current_identity)):
    return knowledge_settings().model_dump()


@app.post("/api/v1/files/upload", status_code=201)
async def upload_document_file(
    request: Request,
    workspace_id: str = Query(min_length=1, max_length=36),
    filename: str = Query(min_length=1, max_length=255),
    auto_index: bool = Query(default=True),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity, ("owner", "member"))
    try:
        safe_name = validate_knowledge_filename(filename)
    except KnowledgeError as error:
        raise HTTPException(422, str(error)) from None

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_KNOWLEDGE_BYTES:
                raise HTTPException(413, "The file exceeds the 5 MiB limit")
        except ValueError:
            raise HTTPException(400, "Invalid upload length") from None

    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_KNOWLEDGE_BYTES:
            raise HTTPException(413, "The file exceeds the 5 MiB limit")

    try:
        text, mime_type, size, sha256 = extract_text_and_validate(safe_name, bytes(content))
    except KnowledgeError as error:
        status = 413 if error.code == "file_too_large" else 422
        raise HTTPException(status, str(error)) from None

    doc_file = DocumentFile(
        workspace_id=workspace_id,
        uploaded_by=identity.user.id,
        original_name=safe_name,
        mime_type=mime_type,
        sha256=sha256,
        size_bytes=size,
        status="uploaded",
        stored_text=text,
    )
    db.add(doc_file)
    db.flush()

    chunk_count = 0
    if auto_index:
        chunk_count = index_file_chunks(db, doc_file)

    db.commit()
    db.refresh(doc_file)
    return serialize_document_file(doc_file, chunk_count)


@app.get("/api/v1/files")
def list_document_files(
    workspace_id: str = Query(min_length=1, max_length=36),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func

    from .auth import require_role

    require_role(db, workspace_id, identity)
    files = db.scalars(
        select(DocumentFile)
        .where(DocumentFile.workspace_id == workspace_id)
        .order_by(DocumentFile.created_at.desc())
    ).all()
    chunk_counts: dict[str, int] = {
        str(row[0]): int(row[1])
        for row in db.execute(
            select(DocumentChunk.file_id, func.count(DocumentChunk.id))
            .where(DocumentChunk.workspace_id == workspace_id)
            .group_by(DocumentChunk.file_id)
        ).all()
    }
    return [serialize_document_file(f, chunk_counts.get(f.id, 0)) for f in files]


@app.get("/api/v1/files/{file_id}")
def get_document_file(
    file_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func

    from .auth import require_role

    file = db.get(DocumentFile, file_id)
    if file is None:
        raise HTTPException(404, "File not found")
    require_role(db, file.workspace_id, identity)
    chunk_count = (
        db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.file_id == file.id)) or 0
    )
    return serialize_document_file(file, chunk_count)


@app.post("/api/v1/files/{file_id}/index")
def reindex_document_file(
    file_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    file = db.get(DocumentFile, file_id)
    if file is None:
        raise HTTPException(404, "File not found")
    require_role(db, file.workspace_id, identity, ("owner", "member"))
    chunk_count = index_file_chunks(db, file)
    db.commit()
    db.refresh(file)
    return serialize_document_file(file, chunk_count)


@app.delete("/api/v1/files/{file_id}")
def delete_document_file(
    file_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from sqlalchemy import delete as sa_delete

    from .auth import require_role

    file = db.get(DocumentFile, file_id)
    if file is None:
        raise HTTPException(404, "File not found")
    require_role(db, file.workspace_id, identity, ("owner", "member"))
    db.execute(sa_delete(DocumentChunk).where(DocumentChunk.file_id == file.id))
    db.delete(file)
    db.commit()
    return {"deleted": True, "file_id": file_id}


@app.post("/api/v1/knowledge/search")
def search_knowledge(
    body: KnowledgeSearchRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, body.workspace_id, identity)
    results = search_workspace_chunks(
        db,
        body.workspace_id,
        body.query,
        limit=body.limit,
        file_id=body.file_id,
    )
    return {
        "workspace_id": body.workspace_id,
        "query": body.query,
        "total_matches": len(results),
        "results": [r.model_dump() for r in results],
    }


@app.get("/api/v1/tools")
def list_registered_tools(identity: Identity = Depends(current_identity)):
    return list(REGISTERED_TOOLS.values())


@app.get("/api/v1/approvals")
def list_approvals(
    workspace_id: str = Query(min_length=1, max_length=36),
    status: str | None = Query(default=None, max_length=24),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity)
    check_and_expire_approvals(db, workspace_id)

    stmt = select(Approval).where(Approval.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Approval.status == status)
    stmt = stmt.order_by(Approval.created_at.desc())
    approvals = db.scalars(stmt).all()

    user_ids = {a.requested_by for a in approvals} | {
        a.decision_by for a in approvals if a.decision_by
    }
    users = (
        {u.id: u.email for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )

    return [
        serialize_approval(
            a,
            decider_email=users.get(a.decision_by or ""),
            requester_email=users.get(a.requested_by),
        )
        for a in approvals
    ]


@app.get("/api/v1/approvals/{approval_id}")
def get_approval(
    approval_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    approval = db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(404, "Approval request not found")
    require_role(db, approval.workspace_id, identity)

    requester = db.get(User, approval.requested_by)
    decider = db.get(User, approval.decision_by) if approval.decision_by else None
    return serialize_approval(
        approval,
        decider_email=decider.email if decider else None,
        requester_email=requester.email if requester else None,
    )


@app.post("/api/v1/approvals/{approval_id}/decision")
def submit_approval_decision(
    approval_id: str,
    body: ApprovalDecisionRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    approval = db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(404, "Approval request not found")
    require_role(db, approval.workspace_id, identity, ("owner", "member"))

    try:
        updated = decide_approval(
            db,
            approval_id=approval.id,
            decision=body.decision,
            decider_id=identity.user.id,
            reason=body.reason,
        )
    except ApprovalError as error:
        raise HTTPException(409, str(error)) from None

    requester = db.get(User, updated.requested_by)
    decider = db.get(User, updated.decision_by) if updated.decision_by else None
    return serialize_approval(
        updated,
        decider_email=decider.email if decider else None,
        requester_email=requester.email if requester else None,
    )


@app.post("/api/v1/tools/execute")
def execute_tool(
    body: ToolExecutionRequest,
    response: Response,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, body.workspace_id, identity, ("owner", "member"))
    try:
        result = execute_gated_tool(
            db=db,
            workspace_id=body.workspace_id,
            user_id=identity.user.id,
            tool_name=body.tool_name,
            parameters=body.parameters,
            approval_id=body.approval_id,
            workflow_id=body.workflow_id,
            task_id=body.task_id,
            idempotency_key=body.idempotency_key,
        )
    except ApprovalError as error:
        status_code = (
            403
            if error.code == "unapproved_action"
            else 422
            if error.code == "fingerprint_mismatch"
            else 400
        )
        raise HTTPException(status_code, str(error)) from None

    if result.get("status") == "waiting_approval":
        response.status_code = 202
    return result


@app.post("/api/v1/reports/synthesize")
def synthesize_report(
    body: ReportSynthesizeRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, body.workspace_id, identity, ("owner", "member"))
    try:
        md_artifact, html_artifact, json_artifact = synthesize_workflow_report(
            db=db,
            workspace_id=body.workspace_id,
            workflow_id=body.workflow_id,
            title=body.title,
            custom_sections=body.custom_sections,
        )
    except ReportingError as error:
        raise HTTPException(404, str(error)) from None

    return {
        "status": "synthesized",
        "artifacts": [
            serialize_artifact(md_artifact),
            serialize_artifact(html_artifact),
            serialize_artifact(json_artifact),
        ],
    }


@app.get("/api/v1/artifacts")
def list_artifacts(
    workspace_id: str = Query(min_length=1, max_length=36),
    workflow_id: str | None = Query(default=None, max_length=36),
    artifact_type: str | None = Query(default=None, max_length=64),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity)
    stmt = select(Artifact).where(Artifact.workspace_id == workspace_id)
    if workflow_id:
        stmt = stmt.where(Artifact.workflow_id == workflow_id)
    if artifact_type:
        stmt = stmt.where(Artifact.artifact_type == artifact_type)
    stmt = stmt.order_by(Artifact.created_at.desc())
    artifacts = db.scalars(stmt).all()
    return [serialize_artifact(a) for a in artifacts]


@app.get("/api/v1/artifacts/{artifact_id}")
def get_artifact_metadata(
    artifact_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    require_role(db, artifact.workspace_id, identity)
    return serialize_artifact(artifact)


@app.get("/api/v1/artifacts/{artifact_id}/download")
def download_artifact(
    artifact_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    require_role(db, artifact.workspace_id, identity)

    content_bytes = (
        artifact.content_text.encode("utf-8") if artifact.content_text is not None else b""
    )
    return Response(
        content=content_bytes,
        media_type=artifact.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.name}"'},
    )


@app.post("/api/v1/verification/verify")
def submit_verification(
    body: VerificationRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, body.workspace_id, identity, ("owner", "member"))
    try:
        report = verify_deliverable(
            db=db,
            workspace_id=body.workspace_id,
            workflow_id=body.workflow_id,
            artifact_id=body.artifact_id,
            custom_checks=body.custom_checks,
        )
    except ReportingError as error:
        raise HTTPException(404, str(error)) from None

    return serialize_verification_report(report)


@app.get("/api/v1/verification/{verification_id}")
def get_verification_report(
    verification_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    report = db.get(VerificationReport, verification_id)
    if report is None:
        raise HTTPException(404, "Verification report not found")
    require_role(db, report.workspace_id, identity)
    return serialize_verification_report(report)


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


@app.post("/api/v1/workflows/{wid}/pause")
@app.post("/api/v1/runs/{wid}/pause")
def pause_run(
    wid: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    visible_workflow(db, wid, identity, ("owner", "member"))
    try:
        w = pause_workflow(db, wid, identity.user.id)
        return {"workflow_id": wid, "status": w.status, "checkpoint": w.recovery_metadata}
    except RecoveryError as err:
        raise HTTPException(err.status_code, err.message) from None


@app.post("/api/v1/workflows/{wid}/resume")
@app.post("/api/v1/runs/{wid}/resume")
def resume_run(
    wid: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    visible_workflow(db, wid, identity, ("owner", "member"))
    try:
        w = resume_workflow(db, wid, identity.user.id)
        return {"workflow_id": wid, "status": w.status, "checkpoint": w.recovery_metadata}
    except RecoveryError as err:
        raise HTTPException(err.status_code, err.message) from None


@app.post("/api/v1/workflows/{wid}/retry")
@app.post("/api/v1/runs/{wid}/retry")
def retry_run(
    wid: str,
    body: WorkflowRetryRequest = WorkflowRetryRequest(),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    visible_workflow(db, wid, identity, ("owner", "member"))
    try:
        w = retry_workflow(db, wid, identity.user.id, preserve_checkpoint=body.preserve_checkpoint)
        return {"workflow_id": wid, "status": w.status, "checkpoint": w.recovery_metadata}
    except RecoveryError as err:
        raise HTTPException(err.status_code, err.message) from None


@app.get("/api/v1/workflows/{wid}/checkpoint")
def checkpoint_run(
    wid: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    visible_workflow(db, wid, identity, ("owner", "member", "viewer"))
    try:
        return get_workflow_checkpoint(db, wid)
    except RecoveryError as err:
        raise HTTPException(err.status_code, err.message) from None


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


@app.get("/api/v1/metrics/overview")
def metrics_overview(
    workspace_id: str = Query(...),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity)
    total_workflows = (
        db.scalar(select(func.count(Workflow.id)).where(Workflow.workspace_id == workspace_id)) or 0
    )
    by_status_raw = db.execute(
        select(Workflow.status, func.count(Workflow.id))
        .where(Workflow.workspace_id == workspace_id)
        .group_by(Workflow.status)
    ).all()
    status_counts = {str(status): int(count) for status, count in by_status_raw}
    active_threshold = now() - timedelta(seconds=60)
    active_workers = (
        db.scalar(
            select(func.count(WorkerHeartbeat.id)).where(
                WorkerHeartbeat.seen_at >= active_threshold
            )
        )
        or 0
    )
    recovery_rows = db.execute(
        select(Workflow.status, Workflow.recovery_metadata).where(
            Workflow.workspace_id == workspace_id
        )
    ).all()
    eligible_recovery = [
        metadata for _, metadata in recovery_rows if metadata and metadata.get("pause_history")
    ]
    resumed_count = sum(bool(metadata.get("resume_history")) for metadata in eligible_recovery)
    paused_count = status_counts.get("paused", 0)
    recovery_rate = round(resumed_count / len(eligible_recovery), 3) if eligible_recovery else None

    return {
        "workspace_id": workspace_id,
        "total_workflows": total_workflows,
        "status_counts": status_counts,
        "active_workers": active_workers,
        "recovery_metrics": {
            "eligible_workflows": len(eligible_recovery),
            "resumed_from_checkpoint": resumed_count,
            "currently_paused": paused_count,
            "recovery_rate": recovery_rate,
        },
        "timestamp": now().isoformat(),
    }


@app.post("/api/v1/benchmarks/executions")
def run_benchmarks(
    body: BenchmarkExecutionRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, body.workspace_id, identity, ("owner", "member"))
    try:
        results = execute_benchmark_suite(
            db=db,
            workspace_id=body.workspace_id,
            set_id=body.set_id,
            routing_mode=body.routing_mode,
            replicates=body.replicates,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return {
        "status": "completed",
        "count": len(results),
        "results": [
            {
                "id": r.id,
                "case_id": r.benchmark_case_id,
                "execution_id": r.execution_id,
                "routing_mode": r.routing_mode,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "tokens_used": r.tokens_used,
                "cost_usd": r.cost_usd,
                "score": r.score,
                "metadata": r.benchmark_metadata,
                "created_at": r.created_at.isoformat(),
            }
            for r in results
        ],
    }


@app.get("/api/v1/benchmarks/summary")
def benchmark_summary(
    workspace_id: str = Query(...),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity)
    return get_benchmark_summary(db, workspace_id)


@app.get("/api/v1/benchmarks/executions/{execution_id}")
def get_benchmark_execution(
    execution_id: str,
    workspace_id: str = Query(...),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    from .auth import require_role

    require_role(db, workspace_id, identity)
    results = list(
        db.scalars(
            select(BenchmarkResult)
            .where(
                BenchmarkResult.execution_id == execution_id,
                BenchmarkResult.workspace_id == workspace_id,
            )
            .order_by(BenchmarkResult.created_at.asc())
        ).all()
    )
    if not results:
        raise HTTPException(404, "Benchmark execution not found")
    return {
        "execution_id": execution_id,
        "count": len(results),
        "results": [
            {
                "id": r.id,
                "case_id": r.benchmark_case_id,
                "routing_mode": r.routing_mode,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "tokens_used": r.tokens_used,
                "cost_usd": r.cost_usd,
                "score": r.score,
                "metadata": r.benchmark_metadata,
                "created_at": r.created_at.isoformat(),
            }
            for r in results
        ],
    }


def next_db():
    return SessionLocal()
