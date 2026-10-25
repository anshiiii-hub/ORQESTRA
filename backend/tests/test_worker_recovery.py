import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.db import Base
from app.models import Event, Task, Workflow


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    yield sessions
    engine.dispose()


@pytest.mark.parametrize("status", ["queued", "running", "cancelled", "failed", "succeeded"])
def test_restart_only_requeues_active_workflows(sessions, status):
    with sessions.begin() as db:
        workflow = Workflow(prompt="Recovery test", status=status)
        db.add(workflow)
        db.flush()
        task = Task(
            workflow_id=workflow.id,
            name="inspect",
            agent="csv_inspector",
            status="running",
            attempts=1,
            ordinal=0,
        )
        completed = Task(
            workflow_id=workflow.id,
            name="completed",
            agent="csv_inspector",
            status="succeeded",
            result={"saved": True},
            attempts=1,
            ordinal=1,
        )
        db.add_all([task, completed])
    worker.recover_running_tasks()
    worker.recover_running_tasks()
    active = status in ("queued", "running")
    with sessions() as db:
        assert db.get(Workflow, workflow.id).status == status
        restored = db.get(Task, task.id)
        assert restored.status == ("pending" if active else "cancelled")
        assert restored.attempts == 1
        assert db.get(Task, completed.id).result == {"saved": True}
        assert db.get(Task, completed.id).status == "succeeded"
        events = db.scalars(select(Event)).all()
        assert len(events) == 1
        assert events[0].kind == ("task_requeued_after_restart" if active else "task_cancelled")
    if not active:
        assert worker.tick() is False


def test_recovered_active_workflow_completes(sessions):
    from app.planner import demo_plan

    with sessions.begin() as db:
        workflow = Workflow(
            prompt="Analyze sales", input_csv="item,sales\nA,10\nB,20", status="running"
        )
        db.add(workflow)
        db.flush()
        for ordinal, planned in enumerate(demo_plan().tasks):
            db.add(
                Task(
                    workflow_id=workflow.id,
                    name=planned.id,
                    agent=planned.agent,
                    dependencies=planned.dependencies,
                    capability=planned.capability,
                    input_bindings=planned.input_bindings,
                    ordinal=ordinal,
                    status="running" if ordinal == 0 else "pending",
                )
            )
    worker.recover_running_tasks()
    for _ in range(4):
        assert worker.tick() is True
    with sessions() as db:
        restored = db.get(Workflow, workflow.id)
        assert restored.status == "succeeded"
        assert restored.output["summary"]["numeric_columns"]["sales"]["sum"] == 30
