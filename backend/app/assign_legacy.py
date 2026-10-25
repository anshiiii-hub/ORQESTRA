"""Explicit local-operator assignment of a previously unscoped workflow."""

import argparse

from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Event, Membership, Workflow, Workspace


def assign_legacy(db: Session, workflow_id: str, workspace_id: str) -> None:
    workflow = db.get(Workflow, workflow_id, with_for_update=True)
    workspace = db.get(Workspace, workspace_id)
    if workflow is None or workspace is None:
        raise ValueError("Workflow or destination workspace not found")
    owner = db.get(Membership, (workspace_id, workspace.owner_id))
    if owner is None or owner.role != "owner":
        raise ValueError("Destination has no valid owner membership")
    if workflow.workspace_id is not None:
        raise ValueError("Workflow already belongs to a workspace; reassignment refused")
    workflow.workspace_id = workspace_id
    workflow.created_by = workspace.owner_id
    db.add(
        Event(
            workflow_id=workflow.id,
            kind="legacy_workflow_assigned",
            details={"workspace_id": workspace_id, "assigned_by": "local_operator"},
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        assign_legacy(db, args.workflow_id, args.workspace_id)


if __name__ == "__main__":
    main()
