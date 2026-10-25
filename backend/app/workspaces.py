from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import Identity, current_identity, require_role
from .db import get_db
from .models import Membership, User, Workspace

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254)
    role: Literal["member", "viewer"]


@router.get("")
def list_workspaces(identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership)
        .where(Membership.user_id == identity.user.id)
        .order_by(Workspace.created_at)
    ).all()
    return [{"id": workspace.id, "name": workspace.name, "role": role} for workspace, role in rows]


@router.post("", status_code=201)
def create_workspace(
    body: WorkspaceInput,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    workspace = Workspace(name=body.name, owner_id=identity.user.id)
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=identity.user.id, role="owner"))
    db.commit()
    return {"id": workspace.id, "name": workspace.name, "role": "owner"}


@router.get("/{workspace_id}/members")
def list_members(
    workspace_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)
):
    require_role(db, workspace_id, identity)
    rows = db.execute(
        select(User, Membership.role)
        .join(Membership)
        .where(Membership.workspace_id == workspace_id)
        .order_by(User.email)
    ).all()
    return [{"user_id": user.id, "email": user.email, "role": role} for user, role in rows]


@router.post("/{workspace_id}/members")
def set_member(
    workspace_id: str,
    body: MemberInput,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    require_role(db, workspace_id, identity, ("owner",))
    user = db.scalar(select(User).where(User.email == body.email.lower(), User.is_active.is_(True)))
    if user is None:
        raise HTTPException(404, "Registered user not found")
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or user.id == workspace.owner_id:
        raise HTTPException(409, "The workspace owner role cannot be changed")
    membership = db.get(Membership, (workspace_id, user.id))
    if membership:
        membership.role = body.role
    else:
        db.add(Membership(workspace_id=workspace_id, user_id=user.id, role=body.role))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Membership changed; refresh and retry") from None
    return {"user_id": user.id, "email": user.email, "role": body.role}


@router.delete("/{workspace_id}/members/{user_id}")
def remove_member(
    workspace_id: str,
    user_id: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    require_role(db, workspace_id, identity, ("owner",))
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or user_id == workspace.owner_id:
        raise HTTPException(409, "The workspace owner cannot be removed")
    membership = db.get(Membership, (workspace_id, user_id))
    if membership is None:
        raise HTTPException(404, "Member not found")
    db.delete(membership)
    db.commit()
    return {"status": "removed"}
