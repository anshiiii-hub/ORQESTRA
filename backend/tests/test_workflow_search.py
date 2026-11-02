from datetime import timedelta

from app.models import Workflow, now


def test_search_finds_older_runs_and_preserves_workspace_scope(accounts, migrated_database):
    owner = accounts("search@example.com")
    stranger = accounts("stranger@example.com")
    workspace_id = owner.post("/api/v1/workspaces", json={"name": "Search"}).json()["id"]
    _, sessions, _ = migrated_database
    with sessions.begin() as db:
        for index in range(65):
            db.add(
                Workflow(
                    workspace_id=workspace_id,
                    prompt=f"Sales record {index}",
                    status="succeeded",
                    created_at=now() + timedelta(seconds=index),
                )
            )
        db.add(Workflow(workspace_id=workspace_id, prompt="Literal 100%_match", status="failed"))
    path = f"/api/v1/workflows?workspace_id={workspace_id}"
    assert len(owner.get(path).json()) == 50
    pages = [owner.get(path + f"&offset={offset}&limit=20").json() for offset in (0, 20, 40, 60)]
    ids = [run["id"] for page in pages for run in page]
    assert len(ids) == len(set(ids)) == 66
    assert owner.get(path + "&q=Sales%20record%200").json()[0]["prompt"] == "Sales record 0"
    assert len(owner.get(path + "&q=%25_").json()) == 1
    assert len(owner.get(path + "&status=failed").json()) == 1
    assert stranger.get(path + "&q=Sales").status_code == 404
    assert owner.get(path + "&limit=101").status_code == 422
    assert owner.get(path + "&offset=-1").status_code == 422
