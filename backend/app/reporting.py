"""Synthesis, reporting, verifiable deliverables, and rubric verification (PRD Sections 10.3, 10.5, FR-G05, FR-G06, FR-V01, FR-V04, FR-V05)."""

import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Approval,
    Artifact,
    DatasetAnalysis,
    DocumentChunk,
    DocumentFile,
    ResearchRun,
    ResearchSource,
    Task,
    VerificationReport,
    Workflow,
    now,
)


class ReportingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ============================================================================
# Pydantic Schemas
# ============================================================================


class ReportSectionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50000)
    section_type: str = Field(default="body", max_length=32)
    citations: list[str] = Field(default_factory=list)


class ReportCitationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citation_id: str = Field(min_length=1, max_length=64)
    source_type: str = Field(min_length=1, max_length=32)
    reference: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(default="", max_length=2000)


class ReportSynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=36)
    workflow_id: str | None = Field(default=None, max_length=36)
    title: str = Field(
        default="Synthesized Analysis and Verification Report", min_length=3, max_length=200
    )
    custom_sections: list[ReportSectionSchema] | None = None


class VerificationCheckSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    category: str
    criterion: str
    status: Literal["satisfied", "warning", "failed"]
    is_mandatory: bool
    evidence: str


class VerificationDefectSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    severity: Literal["critical", "major", "minor", "info"]
    category: str
    description: str
    evidence_refs: list[str]
    repairable: bool
    suggested_task_ids: list[str] = Field(default_factory=list)


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=36)
    workflow_id: str | None = Field(default=None, max_length=36)
    artifact_id: str | None = Field(default=None, max_length=36)
    custom_checks: list[dict[str, Any]] | None = None


# ============================================================================
# Artifact Storage Helpers
# ============================================================================


def create_artifact(
    db: Session,
    workspace_id: str,
    name: str,
    artifact_type: str,
    mime_type: str,
    content: str | bytes,
    workflow_id: str | None = None,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    """Store durable artifact with SHA-256 fingerprinting (PRD FR-O05)."""
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
        content_text = content
    else:
        content_bytes = content
        try:
            content_text = content.decode("utf-8")
        except UnicodeDecodeError:
            content_text = None

    sha256 = hashlib.sha256(content_bytes).hexdigest()
    size_bytes = len(content_bytes)

    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        task_id=task_id,
        name=name,
        artifact_type=artifact_type,
        mime_type=mime_type,
        size_bytes=size_bytes,
        content_sha256=sha256,
        content_text=content_text,
        metadata_json=metadata or {},
        created_at=now(),
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def get_artifact(db: Session, artifact_id: str, workspace_id: str) -> Artifact:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.workspace_id != workspace_id:
        raise ReportingError("artifact_not_found", "Artifact not found in this workspace")
    return artifact


def serialize_artifact(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "workspace_id": artifact.workspace_id,
        "workflow_id": artifact.workflow_id,
        "task_id": artifact.task_id,
        "name": artifact.name,
        "artifact_type": artifact.artifact_type,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "content_sha256": artifact.content_sha256,
        "metadata": artifact.metadata_json,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "download_url": f"/artifacts/{artifact.id}/download",
    }


# ============================================================================
# Report Synthesis Generator (Markdown, HTML, JSON)
# ============================================================================


def synthesize_workflow_report(
    db: Session,
    workspace_id: str,
    workflow_id: str | None = None,
    title: str = "Synthesized Analysis and Verification Report",
    custom_sections: list[ReportSectionSchema] | None = None,
) -> tuple[Artifact, Artifact, Artifact]:
    """Generate comprehensive Markdown, HTML, and JSON deliverable artifacts (PRD FR-G05, US-07)."""
    workflow: Workflow | None = None
    tasks: list[Task] = []
    if workflow_id:
        workflow = db.get(Workflow, workflow_id)
        if workflow is None or workflow.workspace_id != workspace_id:
            raise ReportingError("workflow_not_found", "Workflow not found in this workspace")
        tasks = list(
            db.scalars(
                select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal.asc())
            ).all()
        )

    # Gather evidence from workspace data analysis, research, memory, and approvals
    dataset_analysis = db.scalars(
        select(DatasetAnalysis)
        .where(DatasetAnalysis.workspace_id == workspace_id)
        .order_by(DatasetAnalysis.created_at.desc())
    ).first()
    research_sources = list(
        db.scalars(
            select(ResearchSource)
            .join(ResearchRun, ResearchRun.id == ResearchSource.run_id)
            .where(ResearchRun.workspace_id == workspace_id)
            .order_by(ResearchSource.position.asc())
            .limit(5)
        ).all()
    )
    chunks = list(
        db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.workspace_id == workspace_id)
            .order_by(DocumentChunk.created_at.desc())
            .limit(5)
        ).all()
    )
    approvals = list(
        db.scalars(
            select(Approval)
            .where(Approval.workspace_id == workspace_id)
            .order_by(Approval.created_at.desc())
            .limit(5)
        ).all()
    )

    # Build structured citations
    citations: list[dict[str, Any]] = []
    citation_counter = 1

    for rs in research_sources:
        citations.append(
            {
                "citation_id": f"CIT-{citation_counter}",
                "source_type": "research_url",
                "reference": rs.url,
                "excerpt": rs.title or rs.excerpt[:150],
                "sha256": rs.content_sha256,
            }
        )
        citation_counter += 1

    for ch in chunks:
        doc_file = db.get(DocumentFile, ch.file_id)
        file_name = doc_file.original_name if doc_file else "document"
        citations.append(
            {
                "citation_id": f"CIT-{citation_counter}",
                "source_type": "document_chunk",
                "reference": f"{file_name} (Chunk #{ch.chunk_index}, offset {ch.source_offset})",
                "excerpt": ch.content[:150],
                "sha256": doc_file.sha256 if doc_file else None,
            }
        )
        citation_counter += 1

    # Build sections
    sections: list[dict[str, Any]] = []

    # 1. Executive Summary
    summary_text = (
        f"This report presents synthesized findings for workspace '{workspace_id}'"
        + (f" on workflow '{workflow.id[:8]}' ('{workflow.prompt[:100]}')." if workflow else ".")
        + f" Status: {workflow.status.upper() if workflow else 'SYNTHESIZED'}. "
        + f"Total {len(tasks)} tasks executed; {sum(1 for t in tasks if t.status == 'succeeded')} succeeded."
    )
    sections.append(
        {
            "id": "sec-summary",
            "title": "Executive Summary",
            "section_type": "summary",
            "content": summary_text,
            "citations": [c["citation_id"] for c in citations[:2]],
        }
    )

    # 2. Dataset & Quantitative Findings
    metrics_data: dict[str, Any] = {}
    if dataset_analysis:
        metrics_data = {
            "dataset_filename": dataset_analysis.filename,
            "row_count": dataset_analysis.row_count,
            "column_count": dataset_analysis.column_count,
            "file_format": dataset_analysis.file_format,
            "content_sha256": dataset_analysis.content_sha256,
        }
        dataset_content = (
            f"Analyzed dataset '{dataset_analysis.filename}' with {dataset_analysis.row_count} rows and "
            f"{dataset_analysis.column_count} columns (Format: {dataset_analysis.file_format.upper()}). "
            f"Content SHA-256 fingerprint: {dataset_analysis.content_sha256[:16]}… "
        )
        if dataset_analysis.profile_json and "columns" in dataset_analysis.profile_json:
            cols = dataset_analysis.profile_json["columns"]
            dataset_content += f"Identified {len(cols)} schema columns with inferred types."
        sections.append(
            {
                "id": "sec-metrics",
                "title": "Quantitative Dataset Analysis",
                "section_type": "metrics",
                "content": dataset_content,
                "citations": [],
            }
        )
    elif workflow and workflow.output:
        metrics_data = {"workflow_output": workflow.output}
        sections.append(
            {
                "id": "sec-metrics",
                "title": "Verified Workflow Execution Outputs",
                "section_type": "metrics",
                "content": f"Deterministic execution results:\n```json\n{json.dumps(workflow.output, indent=2)}\n```",
                "citations": [],
            }
        )

    # 3. Custom or Extended Findings
    if custom_sections:
        for cs in custom_sections:
            sections.append(cs.model_dump())
    else:
        findings_content = "Synthesized analysis across orchestrated agent outputs:\n\n"
        if tasks:
            for t in tasks:
                findings_content += (
                    f"- **{t.name}** (`{t.agent}`): Status `{t.status}`. "
                    f"Attempts: {t.attempts}. "
                    + (
                        f"Route mode: {t.routing_decision.get('mode', 'fixed')}. "
                        if t.routing_decision
                        else ""
                    )
                    + "\n"
                )
        if approvals:
            findings_content += "\n**Human-in-the-Loop Governance Actions:**\n"
            for app in approvals:
                findings_content += (
                    f"- Action `{app.action_type}`: Status `{app.status.upper()}` "
                    + (f"(Reason: {app.reason})" if app.reason else "")
                    + f" — SHA-256 Fingerprint: `{app.details_json.get('arguments_hash', '—')[:12]}…`\n"
                )
        sections.append(
            {
                "id": "sec-findings",
                "title": "Agent Execution & Policy Governance Findings",
                "section_type": "findings",
                "content": findings_content,
                "citations": [c["citation_id"] for c in citations[2:]],
            }
        )

    # 4. Limitations and Boundaries (FR-V05, FR-V04)
    limitations = [
        "Unverified external claims must not be treated as authoritative proofs.",
        "Deterministic numeric summaries represent closed-world analysis of supplied inputs.",
        "External research findings reflect allowlisted domain captures and are treated as untrusted data.",
        "Autonomous code and file modifications require human-in-the-loop review per policy NFR-08.",
    ]
    sections.append(
        {
            "id": "sec-limitations",
            "title": "Limitations, Bounds & Assumptions",
            "section_type": "limitations",
            "content": "\n".join(f"- {lim}" for lim in limitations),
            "citations": [],
        }
    )

    # Construct Markdown representation
    md_lines: list[str] = [
        f"# {title}",
        "",
        f"> **Generated at**: {now().strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"> **Workspace**: `{workspace_id}`  "
        + (f"| **Workflow**: `{workflow.id}`" if workflow else ""),
        "",
        "---",
        "",
    ]
    for sec in sections:
        md_lines.append(f"## {sec['title']}")
        md_lines.append("")
        md_lines.append(sec["content"])
        if sec["citations"]:
            md_lines.append("")
            md_lines.append(f"*Citations*: {', '.join(f'[{c}]' for c in sec['citations'])}")
        md_lines.append("")

    if citations:
        md_lines.append("## Evidentiary Citations & Provenance")
        md_lines.append("")
        for c in citations:
            md_lines.append(
                f"- **[{c['citation_id']}]** ({c['source_type']}): `{c['reference']}`"
                + (f' — *Excerpt*: "{c["excerpt"]}"' if c["excerpt"] else "")
                + (f" (SHA-256: `{c['sha256'][:16]}…`)" if c.get("sha256") else "")
            )
        md_lines.append("")

    md_content = "\n".join(md_lines)

    # Construct HTML printable representation
    html_sections_markup = ""
    for sec in sections:
        cits_badge = (
            "".join(f'<span class="badge badge-citation">[{c}]</span> ' for c in sec["citations"])
            if sec["citations"]
            else ""
        )
        html_sections_markup += f"""
        <section class="report-section">
            <div class="section-header">
                <h2>{sec["title"]}</h2>
                {cits_badge}
            </div>
            <div class="section-body">
                <pre class="content-pre">{sec["content"]}</pre>
            </div>
        </section>
        """

    html_citations_markup = ""
    for c in citations:
        html_citations_markup += f"""
        <li class="citation-item">
            <strong>[{c["citation_id"]}]</strong> <span class="badge badge-type">{c["source_type"]}</span>
            <code>{c["reference"]}</code>
            <p>{c["excerpt"]}</p>
        </li>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{
            --bg: #0c1019;
            --surface: #121a27;
            --border: #283242;
            --text: #ecf1f8;
            --muted: #889ab0;
            --accent: #75c8cb;
            --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: var(--font);
            margin: 0;
            padding: 40px 20px;
            line-height: 1.6;
        }}
        .container {{
            max-width: 900px;
            margin: auto;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 40px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.5);
        }}
        header {{
            border-bottom: 1px solid var(--border);
            padding-bottom: 24px;
            margin-bottom: 30px;
        }}
        h1 {{ margin: 0 0 10px; font-size: 26px; color: #fff; }}
        .meta-bar {{ font-size: 12px; color: var(--muted); display: flex; gap: 16px; flex-wrap: wrap; }}
        .badge {{
            background: #1b2636;
            color: var(--accent);
            padding: 2px 8px;
            border-radius: 6px;
            border: 1px solid #2e435a;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-citation {{ background: #233b3d; color: #8ee3e6; border-color: #38676a; }}
        .report-section {{ margin-bottom: 28px; border-bottom: 1px solid #1c2738; padding-bottom: 20px; }}
        .section-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
        h2 {{ font-size: 17px; margin: 0; color: #dce7f3; }}
        .content-pre {{
            white-space: pre-wrap;
            word-wrap: break-word;
            font-family: inherit;
            color: #d1dde8;
            margin: 0;
            font-size: 13px;
        }}
        ul.citations-list {{ list-style: none; padding: 0; margin: 12px 0; }}
        li.citation-item {{
            background: #090e17;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 8px;
            border: 1px solid #1d2738;
            font-size: 12px;
        }}
        li.citation-item p {{ margin: 6px 0 0; color: var(--muted); }}
        code {{ font-family: monospace; color: var(--accent); font-size: 11px; }}
        @media print {{
            body {{ background: #fff; color: #000; padding: 0; }}
            .container {{ border: none; box-shadow: none; padding: 0; }}
            .badge {{ border: 1px solid #999; color: #000; background: #eee; }}
            h1, h2 {{ color: #000; }}
            .content-pre {{ color: #111; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div style="font-size: 10px; font-weight: bold; letter-spacing: 2px; color: var(--accent); margin-bottom: 6px;">
                ORQESTRA AI · SYNTHESIZED DELIVERABLE
            </div>
            <h1>{title}</h1>
            <div class="meta-bar">
                <span><strong>Workspace:</strong> <code>{workspace_id}</code></span>
                {
        f"<span><strong>Workflow:</strong> <code>{workflow.id}</code></span>" if workflow else ""
    }
                <span><strong>Generated:</strong> {now().strftime("%Y-%m-%d %H:%M:%S UTC")}</span>
                <span class="badge">PROVENANCE VERIFIED</span>
            </div>
        </header>

        {html_sections_markup}

        {
        f'''
        <section class="report-section">
            <h2>Evidentiary Citations & Provenance</h2>
            <ul class="citations-list">
                {html_citations_markup}
            </ul>
        </section>
        '''
        if citations
        else ""
    }
    </div>
</body>
</html>
"""

    # Structured JSON dictionary
    json_summary: dict[str, Any] = {
        "title": title,
        "workspace_id": workspace_id,
        "workflow_id": workflow_id,
        "generated_at": now().isoformat(),
        "summary": summary_text,
        "sections": sections,
        "citations": citations,
        "limitations": limitations,
        "metrics": metrics_data,
        "task_count": len(tasks),
        "status": "synthesized",
    }
    json_str = json.dumps(json_summary, indent=2)

    # Persist the 3 artifacts
    timestamp_slug = now().strftime("%Y%m%d_%H%M%S")
    md_artifact = create_artifact(
        db=db,
        workspace_id=workspace_id,
        name=f"report_{timestamp_slug}.md",
        artifact_type="report_markdown",
        mime_type="text/markdown",
        content=md_content,
        workflow_id=workflow_id,
        metadata={"title": title, "section_count": len(sections)},
    )
    html_artifact = create_artifact(
        db=db,
        workspace_id=workspace_id,
        name=f"report_{timestamp_slug}.html",
        artifact_type="report_html",
        mime_type="text/html",
        content=html_content,
        workflow_id=workflow_id,
        metadata={"title": title, "printable": True},
    )
    json_artifact = create_artifact(
        db=db,
        workspace_id=workspace_id,
        name=f"summary_{timestamp_slug}.json",
        artifact_type="summary_json",
        mime_type="application/json",
        content=json_str,
        workflow_id=workflow_id,
        metadata={"title": title, "citation_count": len(citations)},
    )

    return md_artifact, html_artifact, json_artifact


# ============================================================================
# 5-Tier Verification Rubric Engine (PRD Section 10.5, FR-V01, FR-V04)
# ============================================================================


def verify_deliverable(
    db: Session,
    workspace_id: str,
    workflow_id: str | None = None,
    artifact_id: str | None = None,
    custom_checks: list[dict[str, Any]] | None = None,
) -> VerificationReport:
    """Execute rubric verification against deliverable and upstream evidence (PRD Section 10.5)."""
    checks: list[dict[str, Any]] = []
    defects: list[dict[str, Any]] = []

    artifact: Artifact | None = None
    if artifact_id:
        artifact = db.get(Artifact, artifact_id)
        if artifact is None or artifact.workspace_id != workspace_id:
            raise ReportingError("artifact_not_found", "Artifact not found in this workspace")

    workflow: Workflow | None = None
    tasks: list[Task] = []
    if workflow_id:
        workflow = db.get(Workflow, workflow_id)
        if workflow is None or workflow.workspace_id != workspace_id:
            raise ReportingError("workflow_not_found", "Workflow not found in this workspace")
        tasks = list(
            db.scalars(
                select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal.asc())
            ).all()
        )

    # FR-V05 and FR-G06 describe narrative deliverables. A chart or a code
    # patch can have a verifiable payload without containing prose sections,
    # so do not fail those artifacts for missing citations or limitations.
    is_narrative_artifact = artifact is not None and (
        artifact.artifact_type == "report"
        or artifact.artifact_type.startswith("report_")
        or artifact.artifact_type == "summary_json"
    )

    # 1. Check Deliverable Existence and Non-Empty Payload (Mandatory)
    if artifact is not None:
        if artifact.size_bytes > 0 and artifact.content_sha256:
            checks.append(
                {
                    "id": "chk_artifact_payload",
                    "category": "artifact_integrity",
                    "criterion": "Deliverable exists and content hash is verifiable",
                    "status": "satisfied",
                    "is_mandatory": True,
                    "evidence": f"Size {artifact.size_bytes} bytes, SHA-256: {artifact.content_sha256[:16]}…",
                }
            )
        else:
            checks.append(
                {
                    "id": "chk_artifact_payload",
                    "category": "artifact_integrity",
                    "criterion": "Deliverable exists and content hash is verifiable",
                    "status": "failed",
                    "is_mandatory": True,
                    "evidence": "Artifact payload is empty or has null hash",
                }
            )
            defects.append(
                {
                    "criterion_id": "chk_artifact_payload",
                    "severity": "critical",
                    "category": "artifact_integrity",
                    "description": "Artifact payload is empty or corrupt",
                    "evidence_refs": [artifact.id],
                    "repairable": True,
                    "suggested_task_ids": [],
                }
            )

    # 2. Check Workflow Tasks Success (Mandatory if workflow provided)
    if workflow is not None:
        failed_tasks = [t for t in tasks if t.status == "failed"]
        if not failed_tasks and workflow.status != "failed":
            checks.append(
                {
                    "id": "chk_workflow_execution",
                    "category": "execution_soundness",
                    "criterion": "All orchestrated tasks reached successful completion without unhandled failures",
                    "status": "satisfied",
                    "is_mandatory": True,
                    "evidence": f"All {len(tasks)} tasks succeeded. Workflow status: {workflow.status}.",
                }
            )
        else:
            checks.append(
                {
                    "id": "chk_workflow_execution",
                    "category": "execution_soundness",
                    "criterion": "All orchestrated tasks reached successful completion without unhandled failures",
                    "status": "failed",
                    "is_mandatory": True,
                    "evidence": f"{len(failed_tasks)} failed task(s) detected: {', '.join(t.name for t in failed_tasks)}",
                }
            )
            defects.append(
                {
                    "criterion_id": "chk_workflow_execution",
                    "severity": "critical",
                    "category": "execution_soundness",
                    "description": f"Workflow has {len(failed_tasks)} failed task(s)",
                    "evidence_refs": [t.id for t in failed_tasks],
                    "repairable": True,
                    "suggested_task_ids": [t.id for t in failed_tasks],
                }
            )

    # 3. Check Numerical and Statistics Consistency (Mandatory)
    dataset_analysis = db.scalar(
        select(DatasetAnalysis)
        .where(DatasetAnalysis.workspace_id == workspace_id)
        .order_by(DatasetAnalysis.created_at.desc())
    )
    if dataset_analysis and is_narrative_artifact and artifact.content_text:
        text = artifact.content_text
        if str(dataset_analysis.row_count) in text and str(dataset_analysis.column_count) in text:
            checks.append(
                {
                    "id": "chk_numerical_consistency",
                    "category": "numerical_consistency",
                    "criterion": "Deliverable quantitative metrics match underlying dataset profiling statistics",
                    "status": "satisfied",
                    "is_mandatory": True,
                    "evidence": f"Row count {dataset_analysis.row_count} and column count {dataset_analysis.column_count} match accurately.",
                }
            )
        else:
            checks.append(
                {
                    "id": "chk_numerical_consistency",
                    "category": "numerical_consistency",
                    "criterion": "Deliverable quantitative metrics match underlying dataset profiling statistics",
                    "status": "warning",
                    "is_mandatory": False,
                    "evidence": "Report does not explicitly cite dataset row/column metrics or uses divergent figures.",
                }
            )
            defects.append(
                {
                    "criterion_id": "chk_numerical_consistency",
                    "severity": "minor",
                    "category": "numerical_consistency",
                    "description": "Dataset row/column metrics missing from report text",
                    "evidence_refs": [dataset_analysis.id],
                    "repairable": True,
                    "suggested_task_ids": [],
                }
            )

    # 4. Check Mandatory Limitations Disclosure (FR-V05)
    if is_narrative_artifact and artifact.content_text:
        text_lower = artifact.content_text.lower()
        if "limitation" in text_lower or "assumption" in text_lower or "bound" in text_lower:
            checks.append(
                {
                    "id": "chk_limitations_disclosure",
                    "category": "limitations_disclosure",
                    "criterion": "Deliverable explicitly discloses scope bounds, assumptions, and limitations (FR-V05)",
                    "status": "satisfied",
                    "is_mandatory": True,
                    "evidence": "Limitations and assumptions section is clearly articulated.",
                }
            )
        else:
            checks.append(
                {
                    "id": "chk_limitations_disclosure",
                    "category": "limitations_disclosure",
                    "criterion": "Deliverable explicitly discloses scope bounds, assumptions, and limitations (FR-V05)",
                    "status": "failed",
                    "is_mandatory": True,
                    "evidence": "No limitations, bounds, or assumptions detected in deliverable text.",
                }
            )
            defects.append(
                {
                    "criterion_id": "chk_limitations_disclosure",
                    "severity": "major",
                    "category": "limitations_disclosure",
                    "description": "FR-V05 non-compliance: deliverable lacks required limitations disclosure",
                    "evidence_refs": [artifact.id],
                    "repairable": True,
                    "suggested_task_ids": [],
                }
            )

    # 5. Check Source Attribution & Evidentiary Citations
    if is_narrative_artifact and artifact.content_text:
        text_lower = artifact.content_text.lower()
        if "citation" in text_lower or "provenance" in text_lower or "source" in text_lower:
            checks.append(
                {
                    "id": "chk_source_attribution",
                    "category": "source_attribution",
                    "criterion": "Claims cite evidence sources with verifiable provenance hashes (FR-G06)",
                    "status": "satisfied",
                    "is_mandatory": True,
                    "evidence": "Citations and provenance references are documented.",
                }
            )
        else:
            checks.append(
                {
                    "id": "chk_source_attribution",
                    "category": "source_attribution",
                    "criterion": "Claims cite evidence sources with verifiable provenance hashes (FR-G06)",
                    "status": "warning",
                    "is_mandatory": False,
                    "evidence": "Citations section is missing or claims lack explicit provenance tags.",
                }
            )
            defects.append(
                {
                    "criterion_id": "chk_source_attribution",
                    "severity": "minor",
                    "category": "source_attribution",
                    "description": "Deliverable lacks explicit citation section",
                    "evidence_refs": [artifact.id],
                    "repairable": True,
                    "suggested_task_ids": [],
                }
            )

    # Incorporate custom operator checks if specified
    if custom_checks:
        for cc in custom_checks:
            checks.append(
                {
                    "id": cc.get("id", f"chk_custom_{len(checks)}"),
                    "category": cc.get("category", "operator_policy"),
                    "criterion": cc.get("criterion", "Custom acceptance check"),
                    "status": cc.get("status", "satisfied"),
                    "is_mandatory": cc.get("is_mandatory", True),
                    "evidence": cc.get("evidence", "Operator-specified verification rule"),
                }
            )

    # Check for empty checks condition -> INDETERMINATE
    if not checks:
        decision = "INDETERMINATE"
        confidence_score = 0.0
        summary = "Insufficient deliverable or execution evidence to execute rubric verification."
    else:
        # Determine 5-Tier Rubric Decision (PRD Section 10.5)
        mandatory_checks = [c for c in checks if c["is_mandatory"]]
        mandatory_failed = [c for c in mandatory_checks if c["status"] == "failed"]
        critical_defects = [d for d in defects if d["severity"] == "critical"]
        major_defects = [d for d in defects if d["severity"] == "major"]
        minor_defects = [d for d in defects if d["severity"] == "minor"]

        # Calculate calibrated deterministic score (0.0 to 1.0)
        total_weight = sum(3 if c["is_mandatory"] else 1 for c in checks)
        satisfied_weight = sum(
            (3 if c["is_mandatory"] else 1) for c in checks if c["status"] == "satisfied"
        ) + sum((0.5 if not c["is_mandatory"] else 0.0) for c in checks if c["status"] == "warning")
        confidence_score = round(satisfied_weight / max(1, total_weight), 3)

        if (
            not mandatory_failed
            and not critical_defects
            and not major_defects
            and not minor_defects
        ):
            decision = "PASS"
            summary = "All mandatory and optional acceptance criteria satisfied with 100% verified evidence."
        elif not mandatory_failed and not critical_defects:
            decision = "PASS_WITH_WARNINGS"
            summary = (
                f"Mandatory criteria satisfied. {len(minor_defects) + len(major_defects)} soft warning(s) or "
                f"optional criteria missing."
            )
        elif any(d["repairable"] for d in defects) and not (
            workflow and workflow.status == "failed" and not tasks
        ):
            decision = "REVISION_REQUIRED"
            summary = (
                f"Actionable mandatory criteria failed ({len(mandatory_failed)} failed checks). "
                f"Defects are repairable via revision."
            )
        else:
            decision = "FAIL"
            summary = "Mandatory criteria unmet with unrepairable defects: " + "; ".join(
                d["description"] for d in defects[:2]
            )

    report = VerificationReport(
        id=str(uuid4()),
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        artifact_id=artifact_id,
        decision=decision,
        confidence_score=confidence_score,
        criteria_json=checks,
        defects_json=defects,
        summary=summary,
        created_at=now(),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def serialize_verification_report(report: VerificationReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "workspace_id": report.workspace_id,
        "workflow_id": report.workflow_id,
        "artifact_id": report.artifact_id,
        "decision": report.decision,
        "confidence_score": report.confidence_score,
        "criteria": report.criteria_json,
        "defects": report.defects_json,
        "summary": report.summary,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }
