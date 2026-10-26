"""Tool policy gateway, 100% write approval enforcement and human-in-the-loop state transitions."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Approval, Event, Task, ToolCall, Workflow, now

APPROVAL_EXPIRY_HOURS = 24


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# Explicit tool definitions with declared effects and approval requirements (PRD Section 11)
REGISTERED_TOOLS: dict[str, dict[str, Any]] = {
    "file.export_csv": {
        "tool": "file.export_csv",
        "effect": "external_write",
        "requires_approval": True,
        "required_permissions": ["tool:file_export"],
        "description": "Export structured CSV artifact to workspace storage",
    },
    "code.apply_patch": {
        "tool": "code.apply_patch",
        "effect": "external_write",
        "requires_approval": True,
        "required_permissions": ["tool:code_patch"],
        "description": "Apply code patch proposal to workspace files",
    },
    "workspace.config_write": {
        "tool": "workspace.config_write",
        "effect": "external_write",
        "requires_approval": True,
        "required_permissions": ["tool:workspace_admin"],
        "description": "Modify workspace budget or security policy configuration",
    },
    "web.search": {
        "tool": "web.search",
        "effect": "read_only",
        "requires_approval": False,
        "required_permissions": ["tool:web_search"],
        "description": "Search allowlisted public web destinations",
    },
    "dataset.analyze": {
        "tool": "dataset.analyze",
        "effect": "read_only",
        "requires_approval": False,
        "required_permissions": ["tool:data_analysis"],
        "description": "Compute deterministic profiles and chart artifacts",
    },
    "knowledge.retrieve": {
        "tool": "knowledge.retrieve",
        "effect": "read_only",
        "requires_approval": False,
        "required_permissions": ["tool:knowledge_search"],
        "description": "Search indexed document memory chunks",
    },
    "sandbox.python.execute": {
        "tool": "sandbox.python.execute",
        "effect": "isolated_compute",
        "requires_approval": False,
        "required_permissions": ["tool:sandbox_execute"],
        "description": "Run isolated tests in disposable container",
    },
}

SENSITIVE_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "key",
    "authorization",
    "auth",
    "cookie",
}


class ApprovalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=500)


class ToolExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_id: str = Field(min_length=1, max_length=36)
    tool_name: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = Field(default=None, max_length=36)
    workflow_id: str | None = Field(default=None, max_length=36)
    task_id: str | None = Field(default=None, max_length=36)
    idempotency_key: str | None = Field(default=None, max_length=128)


def compute_arguments_hash(args: dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sanitize_arguments(args: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for k, v in args.items():
        if any(sensitive in k.lower() for sensitive in SENSITIVE_KEYS):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_arguments(v)
        elif isinstance(v, list):
            sanitized[k] = [
                sanitize_arguments(item) if isinstance(item, dict) else item for item in v
            ]
        else:
            sanitized[k] = v
    return sanitized


def classify_tool(tool_name: str) -> dict[str, Any]:
    tool = REGISTERED_TOOLS.get(tool_name)
    if tool is None:
        raise ApprovalError("unregistered_tool", f"Tool '{tool_name}' is not registered")
    return tool


def create_approval_request(
    db: Session,
    workspace_id: str,
    requested_by: str,
    tool_name: str,
    parameters: dict[str, Any],
    workflow_id: str | None = None,
    task_id: str | None = None,
    description: str | None = None,
    expiry_hours: int = APPROVAL_EXPIRY_HOURS,
) -> tuple[Approval, ToolCall]:
    tool = classify_tool(tool_name)
    args_hash = compute_arguments_hash(parameters)
    redacted_args = sanitize_arguments(parameters)
    expiry = now() + timedelta(hours=expiry_hours)
    approval_id = str(uuid4())
    tool_call_id = str(uuid4())

    desc = description or tool.get("description") or f"Execute {tool_name}"
    details = {
        "tool_name": tool_name,
        "effect": tool["effect"],
        "required_permissions": tool["required_permissions"],
        "arguments_hash": args_hash,
        "parameters": redacted_args,
    }

    approval = Approval(
        id=approval_id,
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        task_id=task_id,
        tool_call_id=tool_call_id,
        action_type=f"tool:{tool_name}",
        description=desc,
        details_json=details,
        status="pending",
        requested_by=requested_by,
        expires_at=expiry,
    )
    db.add(approval)

    idempotency_key = f"{tool_name}:{args_hash[:16]}:{tool_call_id[:8]}"
    tool_call = ToolCall(
        id=tool_call_id,
        workspace_id=workspace_id,
        task_id=task_id,
        tool_name=tool_name,
        effect=tool["effect"],
        requires_approval=True,
        status="waiting_approval",
        approval_id=approval_id,
        idempotency_key=idempotency_key,
        arguments_hash=args_hash,
        args_redacted_json=redacted_args,
    )
    db.add(tool_call)

    # Suspend workflow into WAITING_APPROVAL state if associated with a run
    if workflow_id:
        workflow = db.get(Workflow, workflow_id)
        if workflow and workflow.status in ("queued", "running"):
            workflow.status = "waiting_approval"
            db.add(
                Event(
                    workflow_id=workflow.id,
                    kind="workflow_waiting_approval",
                    details={
                        "approval_id": approval_id,
                        "tool_name": tool_name,
                        "action_type": approval.action_type,
                    },
                )
            )

    if task_id:
        task = db.get(Task, task_id)
        if task and task.status in ("pending", "running"):
            task.status = "waiting_approval"

    db.flush()
    return approval, tool_call


def check_and_expire_approvals(db: Session, workspace_id: str) -> None:
    current_time = now()
    expired = db.scalars(
        select(Approval).where(
            Approval.workspace_id == workspace_id,
            Approval.status == "pending",
            Approval.expires_at <= current_time,
        )
    ).all()
    for app in expired:
        app.status = "expired"
        app.decided_at = current_time
        # Fail associated tool calls and workflows
        tool_calls = db.scalars(select(ToolCall).where(ToolCall.approval_id == app.id)).all()
        for tc in tool_calls:
            tc.status = "failed"
            tc.result_ref = "Approval request expired"
        if app.workflow_id:
            wf = db.get(Workflow, app.workflow_id)
            if wf and wf.status == "waiting_approval":
                wf.status = "failed"
                wf.error = "approval_expired: Approval request expired without decision"
                db.add(
                    Event(
                        workflow_id=wf.id,
                        kind="workflow_failed",
                        details={"error_code": "approval_expired"},
                    )
                )


def decide_approval(
    db: Session,
    approval_id: str,
    decision: Literal["approved", "rejected"],
    decider_id: str,
    reason: str | None = None,
) -> Approval:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise ApprovalError("approval_not_found", "Approval request not found")

    if approval.status != "pending":
        raise ApprovalError("already_decided", f"Approval is already in '{approval.status}' status")

    current_time = now()
    if ensure_utc(approval.expires_at) <= current_time:
        approval.status = "expired"
        approval.decided_at = current_time
        db.commit()
        raise ApprovalError("approval_expired", "Approval request has expired")

    approval.decision = decision
    approval.decision_by = decider_id
    approval.reason = reason
    approval.decided_at = current_time
    approval.status = decision

    # Update associated tool calls
    tool_calls = db.scalars(select(ToolCall).where(ToolCall.approval_id == approval.id)).all()
    for tc in tool_calls:
        tc.status = "approved" if decision == "approved" else "rejected"

    # Transition workflow lifecycle state
    if approval.workflow_id:
        workflow = db.get(Workflow, approval.workflow_id)
        if workflow and workflow.status == "waiting_approval":
            if decision == "approved":
                workflow.status = "running"
                db.add(
                    Event(
                        workflow_id=workflow.id,
                        kind="workflow_resumed",
                        details={"approval_id": approval.id, "decision": "approved"},
                    )
                )
            else:
                workflow.status = "failed"
                workflow.error = f"approval_rejected: {reason or 'External write action rejected'}"
                db.add(
                    Event(
                        workflow_id=workflow.id,
                        kind="workflow_failed",
                        details={"error_code": "approval_rejected", "reason": reason},
                    )
                )

    if approval.task_id:
        task = db.get(Task, approval.task_id)
        if task and task.status == "waiting_approval":
            task.status = "ready" if decision == "approved" else "failed"
            if decision == "rejected":
                task.error = f"approval_rejected: {reason or 'Rejected by user'}"

    db.commit()
    db.refresh(approval)
    return approval


def execute_gated_tool(
    db: Session,
    workspace_id: str,
    user_id: str,
    tool_name: str,
    parameters: dict[str, Any],
    approval_id: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    tool = classify_tool(tool_name)
    args_hash = compute_arguments_hash(parameters)

    # NFR-08: 100% approval enforcement for classified write tools
    if tool["effect"] == "external_write" or tool["requires_approval"]:
        if approval_id is None:
            # Create pending approval and suspend
            approval, tool_call = create_approval_request(
                db,
                workspace_id=workspace_id,
                requested_by=user_id,
                tool_name=tool_name,
                parameters=parameters,
                workflow_id=workflow_id,
                task_id=task_id,
            )
            db.commit()
            return {
                "status": "waiting_approval",
                "approval_id": approval.id,
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "effect": tool["effect"],
                "requires_approval": True,
                "arguments_hash": args_hash,
                "message": "Action classified as external_write; approval is required to proceed",
            }

        # Validate provided approval
        existing_approval = db.get(Approval, approval_id)
        if existing_approval is None or existing_approval.workspace_id != workspace_id:
            raise ApprovalError("invalid_approval", "Approval record not found for this workspace")

        if existing_approval.status != "approved":
            raise ApprovalError(
                "unapproved_action",
                f"Action cannot execute: approval status is '{existing_approval.status}'",
            )

        if ensure_utc(existing_approval.expires_at) <= now():
            existing_approval.status = "expired"
            db.commit()
            raise ApprovalError("approval_expired", "Approval request has expired")

        # Fingerprint check: Ensure exact arguments match approved hash
        approved_hash = existing_approval.details_json.get("arguments_hash")
        if approved_hash != args_hash:
            raise ApprovalError(
                "fingerprint_mismatch",
                "Tool parameters do not match the fingerprint approved by human review",
            )

    # Execute deterministic action safely
    result = perform_deterministic_tool_action(tool_name, parameters)

    # Record completed tool call
    key = idempotency_key or f"{tool_name}:{args_hash[:16]}:{uuid4().hex[:8]}"
    existing = db.scalar(select(ToolCall).where(ToolCall.idempotency_key == key))
    if existing:
        existing.status = "executed"
        existing.result_ref = json.dumps(result)
    else:
        call = ToolCall(
            workspace_id=workspace_id,
            task_id=task_id,
            tool_name=tool_name,
            effect=tool["effect"],
            requires_approval=tool["requires_approval"],
            status="executed",
            approval_id=approval_id,
            idempotency_key=key,
            arguments_hash=args_hash,
            args_redacted_json=sanitize_arguments(parameters),
            result_ref=json.dumps(result),
        )
        db.add(call)

    db.commit()
    return {
        "status": "completed",
        "tool_name": tool_name,
        "effect": tool["effect"],
        "result": result,
    }


def perform_deterministic_tool_action(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "file.export_csv":
        data = parameters.get("csv_content", "")
        return {
            "bytes_exported": len(data.encode("utf-8")),
            "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
            "destination": parameters.get("filename", "export.csv"),
            "written_at": now().isoformat(),
        }
    if tool_name == "code.apply_patch":
        files_modified = list(parameters.get("files", {}).keys())
        return {
            "applied_files": files_modified,
            "patch_sha256": compute_arguments_hash(parameters),
            "status": "patch_applied",
            "applied_at": now().isoformat(),
        }
    if tool_name == "workspace.config_write":
        return {
            "config_updated": True,
            "keys_modified": list(parameters.keys()),
            "updated_at": now().isoformat(),
        }
    return {
        "status": "success",
        "output": f"Executed {tool_name} successfully",
        "timestamp": now().isoformat(),
    }


def serialize_approval(
    approval: Approval,
    decider_email: str | None = None,
    requester_email: str | None = None,
) -> dict[str, Any]:
    return {
        "id": approval.id,
        "workspace_id": approval.workspace_id,
        "workflow_id": approval.workflow_id,
        "task_id": approval.task_id,
        "tool_call_id": approval.tool_call_id,
        "action_type": approval.action_type,
        "description": approval.description,
        "details": approval.details_json,
        "status": approval.status,
        "requested_by": approval.requested_by,
        "requester_email": requester_email,
        "decision_by": approval.decision_by,
        "decider_email": decider_email,
        "decision": approval.decision,
        "reason": approval.reason,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
    }


def serialize_tool_call(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "workspace_id": tool_call.workspace_id,
        "task_id": tool_call.task_id,
        "tool_name": tool_call.tool_name,
        "effect": tool_call.effect,
        "requires_approval": tool_call.requires_approval,
        "status": tool_call.status,
        "approval_id": tool_call.approval_id,
        "idempotency_key": tool_call.idempotency_key,
        "arguments_hash": tool_call.arguments_hash,
        "parameters": tool_call.args_redacted_json,
        "result": tool_call.result_ref,
        "created_at": tool_call.created_at.isoformat() if tool_call.created_at else None,
    }
