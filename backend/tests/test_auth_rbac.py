import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import worker
from app.auth import COOKIE, hash_password, token_hash, verify_password
from app.models import LoginAttempt, LoginSession, Membership, User, Workflow, now

AUTH = "/api/v1/auth"
WORKSPACES = "/api/v1/workspaces"
WORKFLOWS = "/api/v1/workflows"
PASSWORD = "correct-horse-battery"


def workspace(client):
    response = client.post(WORKSPACES, json={"name": "Private project"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def submit(client, wid):
    return client.post(
        WORKFLOWS,
        json={"workspace_id": wid, "prompt": "Analyze sales", "csv_text": "item,sales\nA,10\nB,20"},
    )


def test_passwords_have_unique_salts():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong-password", first)
    assert not verify_password(PASSWORD, "invalid-hash")


def test_registration_cookie_and_session_storage(accounts, migrated_database):
    client = accounts("Mixed@Example.com")
    me = client.get(f"{AUTH}/me")
    assert me.json()["user"]["email"] == "mixed@example.com"
    assert me.headers["cache-control"] == "no-store"
    assert "password" not in me.text
    _, sessions, _ = migrated_database
    with sessions() as db:
        user = db.scalar(select(User))
        assert user.password_hash != PASSWORD
        session = db.scalar(select(LoginSession))
        assert session.token_hash == token_hash(client.cookies.get(COOKIE))
        assert session.token_hash != client.cookies.get(COOKIE)
    login = client.post(f"{AUTH}/login", json={"email": "mixed@example.com", "password": PASSWORD})
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api/v1" in cookie


def test_duplicate_and_invalid_registration(accounts, api_client):
    accounts("owner@example.com")
    body = {"email": "OWNER@example.com", "password": PASSWORD}
    assert api_client.post(f"{AUTH}/register", json=body).status_code == 409
    assert (
        api_client.post(f"{AUTH}/register", json={**body, "password": "short"}).status_code == 422
    )
    assert api_client.post(f"{AUTH}/register", json={**body, "role": "owner"}).status_code == 422
    assert api_client.post(f"{AUTH}/register", json={**body, "email": "invalid"}).status_code == 422


def test_wrong_credentials_and_session_rotation(accounts, api_client):
    client = accounts("owner@example.com")
    for email in ["owner@example.com", "missing@example.com"]:
        response = api_client.post(
            f"{AUTH}/login", json={"email": email, "password": "wrong-password"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password"
    old_token = client.cookies.get(COOKIE)
    response = client.post(
        f"{AUTH}/login", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert client.cookies.get(COOKIE) != old_token
    api_client.cookies.set(COOKIE, old_token, path="/api/v1")
    assert api_client.get(f"{AUTH}/me").status_code == 401
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    new_token = client.cookies.get(COOKIE)
    assert client.post(f"{AUTH}/logout").status_code == 200
    api_client.cookies.set(COOKIE, new_token, path="/api/v1")
    assert api_client.get(f"{AUTH}/me").status_code == 401


def test_login_attempts_are_database_throttled_and_cleared(accounts, api_client, migrated_database):
    client = accounts("throttle@example.com")
    payload = {"email": "throttle@example.com", "password": "wrong-password"}
    for _ in range(5):
        assert api_client.post(f"{AUTH}/login", json=payload).status_code == 401

    limited = api_client.post(f"{AUTH}/login", json=payload)
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 15 * 60

    _, sessions, _ = migrated_database
    email_hash = hashlib.sha256(payload["email"].encode()).hexdigest()
    with sessions.begin() as db:
        attempt = db.get(LoginAttempt, email_hash)
        assert attempt is not None
        assert attempt.attempts == 6
        attempt.window_started_at -= 15 * 60 + 1

    # An expired fixed window starts over, and a successful login removes its digest.
    assert api_client.post(f"{AUTH}/login", json=payload).status_code == 401
    successful = client.post(
        f"{AUTH}/login", json={"email": payload["email"], "password": PASSWORD}
    )
    assert successful.status_code == 200
    with sessions() as db:
        assert db.get(LoginAttempt, email_hash) is None


@pytest.mark.parametrize("invalid", ["expired", "disabled"])
def test_expired_sessions_and_disabled_users(accounts, migrated_database, invalid):
    client = accounts("owner@example.com")
    _, sessions, _ = migrated_database
    with sessions.begin() as db:
        if invalid == "expired":
            db.scalar(select(LoginSession)).expires_at = now() - timedelta(seconds=1)
        else:
            db.scalar(select(User)).is_active = False
    assert client.get(f"{AUTH}/me").status_code == 401
    assert client.post(WORKSPACES, json={"name": "Denied"}).status_code == 401


def test_csrf_and_origin_enforced(accounts, api_client):
    client = accounts("owner@example.com")
    for headers in [
        {"X-CSRF-Token": ""},
        {"X-CSRF-Token": "wrong"},
        {"Origin": "https://evil.example"},
        {"Origin": ""},
    ]:
        assert client.post(WORKSPACES, json={"name": "Denied"}, headers=headers).status_code == 403
    for route in ["login", "register"]:
        assert (
            api_client.post(
                f"{AUTH}/{route}",
                headers={"Origin": "https://evil.example"},
                json={"email": "owner@example.com", "password": PASSWORD},
            ).status_code
            == 403
        )
    assert (
        api_client.get(
            WORKSPACES, headers={"X-API-Key": "local-dev-key-change-before-exposure"}
        ).status_code
        == 401
    )


def test_cross_workspace_workflows_and_events_are_hidden(accounts):
    owner = accounts("owner@example.com")
    outsider = accounts("outsider@example.com")
    own_id, other_id = workspace(owner), workspace(outsider)
    response = submit(owner, own_id)
    assert response.status_code == 202
    wid = response.json()["workflow_id"]
    assert outsider.get(f"{WORKFLOWS}?workspace_id={other_id}").json() == []
    assert outsider.get(f"{WORKFLOWS}?workspace_id={own_id}").status_code == 404
    assert submit(outsider, own_id).status_code == 404
    for suffix in ["", "/events", "/stream"]:
        denied = outsider.get(f"{WORKFLOWS}/{wid}{suffix}")
        assert denied.status_code == 404
        assert denied.json()["detail"] == "Workflow not found"
    assert outsider.post(f"{WORKFLOWS}/{wid}/cancel").status_code == 404
    assert outsider.get(f"{WORKSPACES}/{own_id}/members").status_code == 404
    assert outsider.get(WORKSPACES).json()[0]["id"] == other_id


@pytest.mark.parametrize("role", ["viewer", "member"])
def test_roles_and_owner_protection(accounts, role):
    owner = accounts("owner@example.com")
    collaborator = accounts("collaborator@example.com")
    own_id = workspace(owner)
    member = owner.post(
        f"{WORKSPACES}/{own_id}/members", json={"email": "collaborator@example.com", "role": role}
    )
    assert member.status_code == 200
    assert collaborator.get(WORKSPACES).json()[0]["role"] == role
    wid = submit(owner, own_id).json()["workflow_id"]
    assert collaborator.get(f"{WORKFLOWS}/{wid}").status_code == 200
    assert collaborator.get(f"{WORKFLOWS}/{wid}/events").status_code == 200
    assert collaborator.get(f"{WORKSPACES}/{own_id}/members").status_code == 200
    expected = 202 if role == "member" else 403
    assert submit(collaborator, own_id).status_code == expected
    assert collaborator.post(f"{WORKFLOWS}/{wid}/cancel").status_code == (
        200 if role == "member" else 403
    )
    assert (
        collaborator.post(
            f"{WORKSPACES}/{own_id}/members",
            json={"email": "collaborator@example.com", "role": "member"},
        ).status_code
        == 403
    )
    assert (
        owner.post(
            f"{WORKSPACES}/{own_id}/members",
            json={"email": "collaborator@example.com", "role": "owner"},
        ).status_code
        == 422
    )
    owner_id = owner.get(f"{AUTH}/me").json()["user"]["id"]
    assert owner.delete(f"{WORKSPACES}/{own_id}/members/{owner_id}").status_code == 409
    assert (
        owner.post(
            f"{WORKSPACES}/{own_id}/members", json={"email": "owner@example.com", "role": "viewer"}
        ).status_code
        == 409
    )
    assert collaborator.delete(f"{WORKSPACES}/{own_id}/members/{owner_id}").status_code == 403


def test_revoked_membership_takes_effect(accounts):
    owner = accounts("owner@example.com")
    collaborator = accounts("collaborator@example.com")
    own_id = workspace(owner)
    member = owner.post(
        f"{WORKSPACES}/{own_id}/members",
        json={"email": "collaborator@example.com", "role": "member"},
    ).json()
    wid = submit(owner, own_id).json()["workflow_id"]
    assert owner.delete(f"{WORKSPACES}/{own_id}/members/{member['user_id']}").status_code == 200
    assert collaborator.get(f"{WORKFLOWS}/{wid}").status_code == 404
    assert collaborator.get(f"{WORKFLOWS}/{wid}/stream").status_code == 404
    assert collaborator.get(WORKSPACES).json() == []


def test_stream_replays_with_cookie_session(accounts):
    owner = accounts("owner@example.com")
    own_id = workspace(owner)
    wid = submit(owner, own_id).json()["workflow_id"]
    for _ in range(4):
        worker.tick()
    events = owner.get(f"{WORKFLOWS}/{wid}/events").json()
    response = owner.get(
        f"{WORKFLOWS}/{wid}/stream", headers={"Last-Event-ID": str(events[-2]["id"])}
    )
    assert response.status_code == 200
    assert "workflow_succeeded" in response.text
    assert "workflow_created" not in response.text
    assert (
        owner.get(f"{WORKFLOWS}/{wid}/stream", headers={"Last-Event-ID": "invalid"}).status_code
        == 400
    )


def test_unassigned_legacy_workflows_are_hidden(accounts, migrated_database):
    owner = accounts("owner@example.com")
    own_id = workspace(owner)
    _, sessions, _ = migrated_database
    with sessions.begin() as db:
        legacy = Workflow(prompt="Unassigned legacy data")
        db.add(legacy)
    assert owner.get(f"{WORKFLOWS}/{legacy.id}").status_code == 404
    assert owner.get(f"{WORKFLOWS}?workspace_id={own_id}").json() == []


def test_live_stream_stops_when_membership_is_revoked(accounts, migrated_database, monkeypatch):
    from app import main

    owner = accounts("owner@example.com")
    collaborator = accounts("collaborator@example.com")
    own_id = workspace(owner)
    member = owner.post(
        f"{WORKSPACES}/{own_id}/members",
        json={"email": "collaborator@example.com", "role": "viewer"},
    ).json()
    wid = submit(owner, own_id).json()["workflow_id"]
    _, sessions, _ = migrated_database

    async def revoke(_):
        with sessions.begin() as db:
            db.delete(db.get(Membership, (own_id, member["user_id"])))

    monkeypatch.setattr(main.asyncio, "sleep", revoke)
    response = collaborator.get(f"{WORKFLOWS}/{wid}/stream")
    assert response.status_code == 200
    assert response.text.count("workflow_created") == 1
    assert collaborator.get(f"{WORKFLOWS}/{wid}").status_code == 404


def test_legacy_assignment_is_explicit_and_cannot_move_scoped_workflows(
    accounts, migrated_database
):
    from app.assign_legacy import assign_legacy

    owner = accounts("owner@example.com")
    outsider = accounts("outsider@example.com")
    own_id, other_id = workspace(owner), workspace(outsider)
    _, sessions, _ = migrated_database
    with sessions.begin() as db:
        legacy = Workflow(
            prompt="Preserved legacy result", status="succeeded", output={"saved": True}
        )
        db.add(legacy)
    with sessions.begin() as db:
        assign_legacy(db, legacy.id, own_id)
    assert owner.get(f"{WORKFLOWS}/{legacy.id}").json()["output"] == {"saved": True}
    assert outsider.get(f"{WORKFLOWS}/{legacy.id}").status_code == 404
    with sessions.begin() as db:
        with pytest.raises(ValueError, match="reassignment refused"):
            assign_legacy(db, legacy.id, other_id)


def test_registration_can_be_disabled(api_client, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    response = api_client.post(
        f"{AUTH}/register", json={"email": "blocked@example.com", "password": PASSWORD}
    )
    assert response.status_code == 403


def test_secure_cookie_setting(api_client, monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    response = api_client.post(
        f"{AUTH}/register", json={"email": "secure@example.com", "password": PASSWORD}
    )
    assert response.status_code == 201
    assert "secure" in response.headers["set-cookie"].lower().split("; ")


def test_cors_allows_credentials_only_for_configured_origin(api_client):
    path = f"{AUTH}/login"
    good = api_client.options(
        path,
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
        },
    )
    assert good.status_code == 200
    assert good.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert good.headers["access-control-allow-credentials"] == "true"
    bad = api_client.options(
        path, headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert bad.status_code == 400
    assert "access-control-allow-origin" not in bad.headers
