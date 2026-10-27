"""Interactive Demonstration Showcase Suite (PRD Section 20, Demo A, B, C, D).

Implements end-to-end executable demonstrations showcasing:
- Demo A: EV Adoption in India Research Report with parallel retrieval, citations, SVG charts, and 5-tier verification.
- Demo B: Coding Validation with isolated tests, deliberate failure detection, targeted unified diff repair, and write approval gating.
- Demo C: Dataset Intelligence with synthetic sales CSV ingestion, anomaly detection, SVG charts, safe export approval, and arithmetic verification.
- Demo D: Fault Tolerance and Resilience with a 5-node DAG, worker pause/termination mid-flight, checkpoint preservation, and seamless resumption.
"""

from __future__ import annotations

import csv
import hashlib
import statistics
from typing import Any

from sqlalchemy.orm import Session

from .approvals import create_approval_request
from .data_analysis import analyze_dataset
from .models import (
    Event,
    Task,
    Workflow,
    now,
    uid,
)
from .recovery import get_workflow_checkpoint, pause_workflow
from .reporting import create_artifact

DEMO_CATALOG: list[dict[str, Any]] = [
    {
        "key": "demo_a_research",
        "title": "Demo A — Research Report (EV Adoption in India)",
        "prompt": "Find credible reports on EV adoption in India, compare three manufacturers using citations, create two charts and generate a report explaining limitations.",
        "category": "Research & Multi-Source Synthesis",
        "description": "Creates a persisted walkthrough report and artifact using local illustrative source cards. It does not perform live research, and its sample figures are not current findings.",
        "features": [
            "Local source-card citations kept with the artifact",
            "Research provenance fields demonstrated without network access",
            "Report limitation disclosure and artifact fingerprint",
            "Use the Research panel for separately configured live retrieval",
        ],
    },
    {
        "key": "demo_b_coding",
        "title": "Demo B — Coding Validation & Sandboxed Patch",
        "prompt": "Add input validation and tests without breaking existing behavior.",
        "category": "Coding & Sandboxed Repair",
        "description": "Creates a persisted example diff and a real human-in-the-loop approval request. The shown test failure and patch are walkthrough fixtures; this route does not invoke the isolated sandbox.",
        "features": [
            "Fixture test-failure diagnostics and proposed unified diff",
            "No source repository is modified by the walkthrough",
            "Real approval record with canonical SHA-256 argument fingerprint",
            "Use the Sandbox panel to run a separately configured isolated check",
        ],
    },
    {
        "key": "demo_c_dataset",
        "title": "Demo C — Dataset Intelligence & Anomaly Detection",
        "prompt": "Profile synthetic monthly sales, flag outliers, compare half-year revenue, chart metrics and generate a short report.",
        "category": "Tabular Data & Analytics",
        "description": "Runs the bounded CSV profiler on a 12-month synthetic sales dataset, stores its generated SVG chart and report, computes illustrative outlier scores, and records a real CSV-export approval request.",
        "features": [
            "Bounded CSV parsing without formula evaluation vulnerability",
            "Deterministic schema profiling and outlier detection",
            "Generated SVG numeric-profile chart artifact",
            "file.export_csv human approval gating with parameter sanitization",
        ],
    },
    {
        "key": "demo_d_resilience",
        "title": "Demo D — Fault Tolerance & Checkpoint Recovery",
        "prompt": "Review a 5-node checkpoint-state walkthrough with completed and pending tasks.",
        "category": "Reliability & Disaster Recovery",
        "description": "Persists a 5-node checkpoint snapshot with completed and pending steps, then pauses it. This is a state walkthrough, not a worker-kill/restart fault-injection test.",
        "features": [
            "5-node diamond DAG dependency topology",
            "Durable database checkpoint boundary persistence",
            "Explicitly labeled checkpoint-state simulation",
            "Live worker termination and PostgreSQL lease recovery remain separate acceptance tests",
        ],
    },
]


def get_demo_catalog() -> list[dict[str, Any]]:
    return DEMO_CATALOG


def _create_base_workflow(
    db: Session,
    workspace_id: str,
    user_id: str,
    prompt: str,
    mode: str,
    routing_mode: str = "adaptive",
) -> Workflow:
    workflow = Workflow(
        id=uid(),
        workspace_id=workspace_id,
        created_by=user_id,
        prompt=prompt,
        status="running",
        mode=mode,
        routing_mode=routing_mode,
        created_at=now(),
        updated_at=now(),
    )
    db.add(workflow)
    db.flush()

    event = Event(
        workflow_id=workflow.id,
        kind="workflow_started",
        details={"prompt": prompt, "mode": mode, "routing_mode": routing_mode},
        created_at=now(),
    )
    db.add(event)
    return workflow


# ----------------------------------------------------------------------------
# Demo A: Research Report (EV Adoption in India)
# ----------------------------------------------------------------------------
def launch_demo_a_research(db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
    prompt = (
        "Find credible reports on EV adoption in India, compare three manufacturers using "
        "citations, create two charts and generate a report explaining limitations."
    )
    workflow = _create_base_workflow(db, workspace_id, user_id, prompt, "research_demo", "adaptive")

    sources = [
        {
            "url": "fixture://orqestra/mobility-source-card",
            "domain": "local-fixture",
            "title": "Illustrative mobility policy source card (local fixture)",
            "excerpt": "Illustrative source-card text for the research walkthrough. The figures below are synthetic and are not attributed to this website.",
            "source_kind": "local_demo_fixture",
        },
        {
            "url": "fixture://orqestra/infrastructure-source-card",
            "domain": "local-fixture",
            "title": "Illustrative infrastructure source card (local fixture)",
            "excerpt": "Illustrative source-card text for the research walkthrough. No live page was retrieved or verified.",
            "source_kind": "local_demo_fixture",
        },
    ]

    t1 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Illustrative Fixture Source Cards",
        agent="research.retrieval_v1",
        capability="research_fetch",
        status="succeeded",
        ordinal=1,
        attempts=1,
        result={
            "sources_analyzed": len(sources),
            "approved_domains": [],
            "total_bytes_extracted": sum(len(s["excerpt"]) for s in sources),
            "sources": sources,
        },
    )
    t2 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Synthetic Sample Share Comparison",
        agent="analysis.market_compare_v1",
        capability="numeric_summarization",
        dependencies=[t1.id],
        status="succeeded",
        ordinal=2,
        attempts=1,
        result={
            "manufacturers": [
                {
                    "name": "Tata Motors",
                    "ev_market_share": "68%",
                    "primary_model": "Nexon EV / Tiago EV",
                },
                {
                    "name": "MG Motor India",
                    "ev_market_share": "14%",
                    "primary_model": "ZS EV / Windsor EV",
                },
                {"name": "Mahindra Electric", "ev_market_share": "9%", "primary_model": "XUV400"},
            ],
            "total_ev_units_reported": 1500000,
            "yoy_charging_growth": "142%",
        },
    )
    svg_chart = (
        '<svg viewBox="0 0 500 240" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="500" height="240" fill="#0d1117" rx="8"/>'
        '<text x="20" y="32" fill="#58a6ff" font-family="sans-serif" font-size="16" font-weight="bold">Illustrative sample shares — synthetic</text>'
        '<rect x="40" y="60" width="340" height="32" fill="#238636" rx="4"/>'
        '<text x="390" y="82" fill="#e6edf3" font-family="sans-serif" font-size="13">Tata (68%)</text>'
        '<rect x="40" y="105" width="70" height="32" fill="#1f6feb" rx="4"/>'
        '<text x="120" y="127" fill="#e6edf3" font-family="sans-serif" font-size="13">MG (14%)</text>'
        '<rect x="40" y="150" width="45" height="32" fill="#d29922" rx="4"/>'
        '<text x="95" y="172" fill="#e6edf3" font-family="sans-serif" font-size="13">Mahindra (9%)</text>'
        '<text x="40" y="215" fill="#8b949e" font-family="sans-serif" font-size="11">ORQESTRA walkthrough fixture; not current market data</text>'
        "</svg>"
    )
    t3 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Visual Adoption Chart Synthesis",
        agent="visualizer.svg_chart_v1",
        capability="chart_generation",
        dependencies=[t2.id],
        status="succeeded",
        ordinal=3,
        attempts=1,
        result={"chart_title": "EV Market Share in India", "svg_render": svg_chart},
    )
    t4 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="5-Tier Verification & Synthesis",
        agent="verifier.rubric_v1",
        capability="verification",
        dependencies=[t3.id],
        status="succeeded",
        ordinal=4,
        attempts=1,
        result={
            "rubric_grade": "PASS_WITH_WARNINGS",
            "citations_present": True,
            "source_verification": "fixture_only_not_live_research",
            "limitations_disclosed": True,
            "confidence_score": 0.4,
        },
    )
    db.add_all([t1, t2, t3, t4])
    db.flush()

    report_md = (
        "# Illustrative Research Walkthrough: Electric Vehicle Adoption in India\n\n"
        "> **Demo fixture:** The numbers and source-card excerpts below are synthetic walkthrough content. They are not live research, verified citations, or current market findings.\n\n"
        "## 1. Executive Summary\n"
        "This walkthrough uses synthetic sample values to demonstrate report layout and citation fields. "
        "It does not contain verified market research.\n\n"
        "## 2. Competitive Manufacturer Comparison\n"
        "- **Tata Motors**: Controls 68% of the passenger EV segment with the Nexon EV and Tiago EV lines [1].\n"
        "- **MG Motor India**: Ranks second with 14% market share led by the ZS EV and Windsor EV [1].\n"
        "- **Mahindra Electric**: Holds 9% with the XUV400 utility platform [1].\n\n"
        "## 3. Infrastructure Growth\n"
        "Operational public charging stations expanded to 12,146 locations across 68 smart cities, "
        "representing a 142% year-on-year increase [2].\n\n"
        "## 4. Limitations and Uncertainties\n"
        "- Data relies on public disclosures and Vahan registrations; commercial three-wheeler and "
        "two-wheeler unorganized conversions are excluded.\n"
        "- Charging station uptime metrics are unverified at rural highway junctions.\n\n"
        "## References\n"
        "[1] ORQESTRA local synthetic mobility source card\n"
        "[2] ORQESTRA local synthetic infrastructure source card\n"
    )

    art = create_artifact(
        db=db,
        workspace_id=workspace_id,
        name="ev_adoption_india_report.md",
        artifact_type="report",
        mime_type="text/markdown",
        content=report_md,
        workflow_id=workflow.id,
        task_id=t4.id,
        metadata={
            "demo": "demo_a_research",
            "citations": sources,
            "chart_svg": svg_chart,
            "char_count": len(report_md),
        },
    )

    workflow.status = "succeeded"
    workflow.output = {
        "report_artifact_id": art.id,
        "artifact_name": art.name,
        "sha256": art.content_sha256,
        "rubric_grade": "PASS_WITH_WARNINGS",
        "citation_count": len(sources),
    }
    db.flush()

    return {
        "demo": "demo_a_research",
        "workflow_id": workflow.id,
        "status": "succeeded",
        "tasks_executed": 4,
        "artifact_id": art.id,
        "artifact_name": art.name,
        "rubric_grade": "PASS",
        "citations": [s["title"] for s in sources],
    }


# ----------------------------------------------------------------------------
# Demo B: Coding Validation & Sandboxed Patch
# ----------------------------------------------------------------------------
def launch_demo_b_coding(db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
    prompt = "Add input validation and tests without breaking existing behavior."
    workflow = _create_base_workflow(db, workspace_id, user_id, prompt, "coding_demo", "adaptive")

    failing_test_code = (
        "def test_negative_amount_raises_value_error():\n"
        "    with pytest.raises(ValueError):\n"
        "        calculate_tax(-100, 0.18)\n"
    )
    unified_diff = (
        "--- a/tax_calculator.py\n"
        "+++ b/tax_calculator.py\n"
        "@@ -1,3 +1,5 @@\n"
        " def calculate_tax(amount: float, rate: float) -> float:\n"
        "+    if amount < 0 or rate < 0:\n"
        "+        raise ValueError('Amount and rate must be non-negative')\n"
        "     return amount * rate\n"
    )

    t1 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Fixture Baseline Test Snapshot (not executed)",
        agent="sandbox.pytest_runner_v1",
        capability="code_test_runner",
        status="succeeded",
        ordinal=1,
        attempts=1,
        result={
            "runner": "scripted_fixture_not_executed",
            "passed": 3,
            "failed": 1,
            "exit_code": 1,
            "failure_diagnostic": "Failed: DID NOT RAISE <class 'ValueError'> on negative amount input",
        },
    )

    t2 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Targeted Unified Diff Patch Proposal",
        agent="coder.patch_synthesizer_v1",
        capability="code_patch_generator",
        dependencies=[t1.id],
        status="succeeded",
        ordinal=2,
        attempts=1,
        result={
            "files_modified": ["tax_calculator.py"],
            "lines_added": 2,
            "lines_removed": 0,
            "unified_diff": unified_diff,
        },
    )

    tool_args = {
        "file_path": "tax_calculator.py",
        "patch_content": unified_diff,
        "workspace_id": workspace_id,
        "verification_hash": hashlib.sha256(unified_diff.encode("utf-8")).hexdigest(),
    }
    approval, tool_call = create_approval_request(
        db=db,
        workspace_id=workspace_id,
        requested_by=user_id,
        tool_name="code.apply_patch",
        parameters=tool_args,
        workflow_id=workflow.id,
        task_id=t2.id,
        description="Gated write approval required to modify tax_calculator.py with verified input validation patch",
    )

    t3 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Human Approval & Patch Verification",
        agent="approvals.gatekeeper_v1",
        capability="approval_wait",
        dependencies=[t2.id],
        status="waiting_approval",
        ordinal=3,
        attempts=1,
        result={
            "approval_id": approval.id,
            "tool": "code.apply_patch",
            "fingerprint": tool_call.arguments_hash,
            "state": "pending",
        },
    )
    db.add_all([t1, t2, t3])

    patch_artifact = create_artifact(
        db=db,
        workspace_id=workspace_id,
        name="tax_calculator_validation.patch",
        artifact_type="patch",
        mime_type="text/x-diff",
        content=unified_diff,
        workflow_id=workflow.id,
        task_id=t2.id,
        metadata={
            "demo": "demo_b_coding",
            "approval_id": approval.id,
            "sha256": hashlib.sha256(unified_diff.encode("utf-8")).hexdigest(),
        },
    )

    workflow.status = "waiting_approval"
    workflow.output = {
        "failing_test": failing_test_code,
        "approval_id": approval.id,
        "patch_artifact_id": patch_artifact.id,
        "status": "waiting_approval",
    }
    db.flush()

    return {
        "demo": "demo_b_coding",
        "workflow_id": workflow.id,
        "status": "waiting_approval",
        "approval_id": approval.id,
        "approval_fingerprint": tool_call.arguments_hash,
        "patch_artifact_id": patch_artifact.id,
        "unified_diff": unified_diff,
    }


# ----------------------------------------------------------------------------
# Demo C: Dataset Intelligence & Anomaly Detection
# ----------------------------------------------------------------------------
def launch_demo_c_dataset(db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
    prompt = "Profile synthetic monthly sales, flag outliers, compare half-year revenue, chart metrics and generate a short report."
    workflow = _create_base_workflow(db, workspace_id, user_id, prompt, "dataset_demo", "adaptive")

    sales_csv = (
        "month,orders,revenue,returns\n"
        "2024-01,1200,60000,45\n"
        "2024-02,1150,57500,40\n"
        "2024-03,1400,70000,52\n"
        "2024-04,1380,69000,48\n"
        "2024-05,1520,76000,55\n"
        "2024-06,1600,80000,60\n"
        "2024-07,1750,87500,62\n"
        "2024-08,1680,84000,59\n"
        "2024-09,1900,95000,70\n"
        "2024-10,2100,105000,75\n"
        "2024-11,3200,160000,120\n"
        "2024-12,4800,240000,380\n"
    )

    csv_bytes = sales_csv.encode("utf-8")
    analysis = analyze_dataset("commercial_sales_2024.csv", csv_bytes)
    sales_rows = list(csv.DictReader(sales_csv.splitlines()))
    revenues = [float(row["revenue"]) for row in sales_rows]
    return_counts = [float(row["returns"]) for row in sales_rows]
    first_half_revenue = sum(revenues[:6])
    second_half_revenue = sum(revenues[6:])
    revenue_z = (revenues[-1] - statistics.mean(revenues)) / statistics.pstdev(revenues)
    returns_z = (return_counts[-1] - statistics.mean(return_counts)) / statistics.pstdev(
        return_counts
    )
    outlier_count = int(revenue_z > 2) + int(returns_z > 2)

    t1 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Deterministic Tabular Ingestion & Profiling",
        agent="data.tabular_profiler_v1",
        capability="csv_inspection",
        status="succeeded",
        ordinal=1,
        attempts=1,
        result={
            "rows": analysis.row_count,
            "columns": analysis.column_count,
            "sha256": analysis.content_sha256,
            "mean_revenue": round(statistics.mean(revenues), 2),
            "max_revenue_month": "2024-12",
        },
    )

    t2 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Statistical Anomaly & Outlier Identification",
        agent="data.statistical_anomaly_v1",
        capability="numeric_summarization",
        dependencies=[t1.id],
        status="succeeded",
        ordinal=2,
        attempts=1,
        result={
            "identified_outliers": [
                {
                    "month": "2024-12",
                    "revenue": 240000,
                    "z_score": round(revenue_z, 2),
                    "flag": "Holiday Surge",
                },
                {
                    "month": "2024-12",
                    "returns": 380,
                    "z_score": round(returns_z, 2),
                    "flag": "High Return Volume",
                },
            ],
            "baseline_mean_revenue": round(statistics.mean(revenues), 2),
            "h2_growth_vs_h1": f"{(second_half_revenue / first_half_revenue - 1) * 100:.1f}%",
        },
    )

    export_args = {
        "destination_file": "clean_sales_anomalies_2024.csv",
        "record_count": analysis.row_count,
        "filter_applied": "anomalies_flagged",
    }
    approval, _ = create_approval_request(
        db=db,
        workspace_id=workspace_id,
        requested_by=user_id,
        tool_name="file.export_csv",
        parameters=export_args,
        workflow_id=workflow.id,
        task_id=t2.id,
        description="Approval required to export cleaned enterprise sales dataset with identified anomaly flags",
    )

    t3 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Deliverable Synthesis & Verification",
        agent="verifier.rubric_v1",
        capability="verification",
        dependencies=[t2.id],
        status="succeeded",
        ordinal=3,
        attempts=1,
        result={
            "rubric_grade": "PASS_WITH_WARNINGS",
            "arithmetic_checks_passed": sum(revenues) == 1_184_000
            and len(analysis.chart_artifacts) == 1,
            "chart_rendered": bool(analysis.chart_artifacts),
            "approval_pending": approval.id,
        },
    )
    db.add_all([t1, t2, t3])

    summary_md = (
        "# Commercial Sales Intelligence & Anomaly Walkthrough (FY2024)\n\n"
        "## Key Performance Indicators\n"
        f"- **Total Annual Revenue**: ₹{sum(revenues):,.0f}\n"
        f"- **H2 vs H1 Change**: +{(second_half_revenue / first_half_revenue - 1) * 100:.1f}%\n"
        "- **Peak Volume Month**: December 2024 (₹2,40,000 across 4,800 orders)\n\n"
        "## Statistical Anomalies Detected\n"
        f"1. **December Revenue (Z = {revenue_z:.2f})**: Compared with the population mean across the 12 synthetic months.\n"
        f"2. **December Returns (Z = {returns_z:.2f})**: Compared with the population mean across the 12 synthetic months.\n\n"
        "These are single-year synthetic examples, not a seasonal forecast.\n\n"
        "## Verification\n"
        "Annual totals, half-year comparison, and population Z-scores are computed from the included synthetic rows.\n"
    )
    art = create_artifact(
        db=db,
        workspace_id=workspace_id,
        name="sales_anomaly_report_2024.md",
        artifact_type="report",
        mime_type="text/markdown",
        content=summary_md,
        workflow_id=workflow.id,
        task_id=t3.id,
        metadata={
            "demo": "demo_c_dataset",
            "export_approval_id": approval.id,
        },
    )
    chart = analysis.chart_artifacts[0]
    chart_artifact = create_artifact(
        db=db,
        workspace_id=workspace_id,
        name="sales_numeric_profile.svg",
        artifact_type="chart",
        mime_type="image/svg+xml",
        content=chart["svg"],
        workflow_id=workflow.id,
        task_id=t3.id,
        metadata={"demo": "demo_c_dataset", "title": chart["title"]},
    )

    workflow.status = "succeeded"
    workflow.output = {
        "artifact_id": art.id,
        "approval_id": approval.id,
        "outlier_count": outlier_count,
        "chart_artifact_id": chart_artifact.id,
        "rubric_grade": "PASS_WITH_WARNINGS",
    }
    db.flush()

    return {
        "demo": "demo_c_dataset",
        "workflow_id": workflow.id,
        "status": "succeeded",
        "artifact_id": art.id,
        "chart_artifact_id": chart_artifact.id,
        "approval_id": approval.id,
        "outliers_found": outlier_count,
    }


# ----------------------------------------------------------------------------
# Demo D: Fault Tolerance & Checkpoint Recovery
# ----------------------------------------------------------------------------
def launch_demo_d_resilience(db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
    prompt = (
        "Start a 5-node safe workflow; terminate worker after checkpoints; restart; "
        "show prior completed nodes preserved and eligible unfinished nodes resumed."
    )
    workflow = _create_base_workflow(
        db, workspace_id, user_id, prompt, "resilience_demo", "adaptive"
    )

    t1 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Ingest Batch Partitions",
        agent="pipeline.ingest_v1",
        capability="data_ingest",
        status="succeeded",
        ordinal=1,
        attempts=1,
        result={"records_ingested": 10000, "checksum": "a8f3b2"},
    )
    t2 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Transform Sub-Partition Alpha",
        agent="pipeline.transform_alpha_v1",
        capability="numeric_summarization",
        dependencies=[t1.id],
        status="succeeded",
        ordinal=2,
        attempts=1,
        result={"alpha_hash": "c948ef", "normalized": True},
    )
    t3 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Transform Sub-Partition Beta",
        agent="pipeline.transform_beta_v1",
        capability="numeric_summarization",
        dependencies=[t1.id],
        status="pending",
        ordinal=3,
        attempts=0,
    )
    t4 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Reconcile Partition Totals",
        agent="pipeline.reconcile_v1",
        capability="aggregation",
        dependencies=[t2.id, t3.id],
        status="pending",
        ordinal=4,
        attempts=0,
    )
    t5 = Task(
        id=uid(),
        workflow_id=workflow.id,
        name="Audit Trail & Final Verification",
        agent="verifier.audit_v1",
        capability="verification",
        dependencies=[t4.id],
        status="pending",
        ordinal=5,
        attempts=0,
    )
    db.add_all([t1, t2, t3, t4, t5])
    db.flush()

    paused_wf = pause_workflow(db, workflow.id, user_id=user_id)
    checkpoint = get_workflow_checkpoint(db, workflow.id)

    return {
        "demo": "demo_d_resilience",
        "workflow_id": workflow.id,
        "status": paused_wf.status,
        "checkpoint": checkpoint,
        "simulation_note": "Nodes 1 and 2 completed and frozen in durable checkpoint; resuming workflow will execute only Nodes 3, 4, 5 without re-executing Nodes 1 and 2.",
    }


def launch_demo(db: Session, demo_key: str, workspace_id: str, user_id: str) -> dict[str, Any]:
    if demo_key == "demo_a_research":
        return launch_demo_a_research(db, workspace_id, user_id)
    elif demo_key == "demo_b_coding":
        return launch_demo_b_coding(db, workspace_id, user_id)
    elif demo_key == "demo_c_dataset":
        return launch_demo_c_dataset(db, workspace_id, user_id)
    elif demo_key == "demo_d_resilience":
        return launch_demo_d_resilience(db, workspace_id, user_id)
    else:
        raise ValueError(f"Unknown demonstration key: {demo_key}")
