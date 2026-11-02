"""Isolated PostgreSQL trials. Run from the repository root; see README.md."""

# ruff: noqa: E402 -- add the repository backend before importing application modules
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import Event, Task, TaskAttempt, TaskDependency, Workflow
from app.planner import demo_plan

CSV = "item,sales\nA,10\nB,20"
EXPECTED = json.loads((ROOT / "experiments/expected_output.json").read_text())


def utc():
    return datetime.now(timezone.utc)


def rows(db, cls):
    return [
        {col.name: getattr(row, col.name) for col in cls.__table__.columns}
        for row in db.scalars(select(cls)).all()
    ]


def trial(base_url, design, fault, lease, seed, log_dir):
    record = dict(
        design=design,
        fault=fault,
        lease_seconds=lease,
        seed=seed,
        started_at=utc(),
        fault_at=None,
        resumed_at=None,
        harness_error=None,
    )
    schema = "study_" + uuid4().hex
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    engine = None
    processes = {}
    handles = []
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
        url = make_url(base_url).update_query_dict(
            {"options": f"-csearch_path={schema}"}
        )
        engine = create_engine(url)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions.begin() as db:
            wf = Workflow(prompt="Analyze sales", input_csv=CSV, status="queued")
            db.add(wf)
            db.flush()
            tasks = {}
            for i, spec in enumerate(demo_plan().tasks):
                task = Task(
                    workflow_id=wf.id,
                    name=spec.id,
                    agent=spec.agent,
                    capability=spec.capability,
                    dependencies=spec.dependencies,
                    input_bindings=spec.input_bindings,
                    ordinal=i,
                )
                db.add(task)
                db.flush()
                tasks[spec.id] = task
            for spec in demo_plan().tasks:
                for parent in spec.dependencies:
                    db.add(
                        TaskDependency(
                            workflow_id=wf.id,
                            task_id=tasks[spec.id].id,
                            dependency_task_id=tasks[parent].id,
                        )
                    )
            wf.execution_plan = {"final_task_id": "verify"}
            wid = wf.id
        delay_ms = round(lease * 400)
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "DATABASE_URL": url.render_as_string(hide_password=False),
            "DATABASE_PASSWORD_FILE": "",
            "PGOPTIONS": "",
            "TASK_LEASE_SECONDS": str(lease),
            "EXPERIMENT_DESIGN": design,
            "EXPERIMENT_RECORD": "1",
            "EXPERIMENT_AGENT_DELAY_MS": str(delay_ms),
            "LLM_PLANNER_ENABLED": "false",
            "OPENAI_API_KEY": "",
        }
        record.update(
            schema=schema,
            delay_ms=delay_ms,
            workflow_id=wid,
            fault_offset_seconds=random.Random(seed).uniform(0.05, 0.15) * lease,
        )
        start = time.monotonic()
        deadline = start + 10 * lease + 30
        for name in ("W1", "W2"):
            handle = (log_dir / f"{design}_{fault}_{lease}_{seed}_{name}.log").open("w")
            handles.append(handle)
            processes[name] = subprocess.Popen(
                [sys.executable, str(ROOT / "experiments/worker_process.py"), name],
                cwd=ROOT / "backend",
                env=env,
                stdout=handle,
                stderr=handle,
            )
        target = None
        signal_due = None
        resume_due = None
        terminal_seen = None
        while time.monotonic() < deadline:
            with sessions() as db:
                wf = db.get(Workflow, wid)
                events = db.scalars(
                    select(Event)
                    .where(Event.kind == "experiment_execution")
                    .order_by(Event.id)
                ).all()
                if target is None and events:
                    attempt = db.scalar(
                        select(TaskAttempt).where(
                            TaskAttempt.request_id == events[0].details["request_id"]
                        )
                    )
                    target = processes[attempt.worker_id]
                    record["target_worker"] = attempt.worker_id
                    record["target_attempt_id"] = attempt.id
                    record["target_lease_expires_at"] = attempt.lease_expires_at
                    signal_due = time.monotonic() + record["fault_offset_seconds"]
                    resume_due = attempt.lease_expires_at.timestamp() + 0.25 * lease
                if (
                    fault != "F3"
                    and target is not None
                    and record["fault_at"] is None
                    and time.monotonic() >= signal_due
                ):
                    os.kill(
                        target.pid, signal.SIGKILL if fault == "F1" else signal.SIGSTOP
                    )
                    record["fault_at"] = utc()
                if (
                    fault == "F2"
                    and record["fault_at"] is not None
                    and record["resumed_at"] is None
                    and time.time() >= resume_due
                ):
                    os.kill(target.pid, signal.SIGCONT)
                    record["resumed_at"] = utc()
                if wf.status in ("succeeded", "failed", "cancelled"):
                    if terminal_seen is None:
                        terminal_seen = time.monotonic()
                    # Observe the late result even if another worker finished the DAG.
                    running = db.scalar(
                        select(TaskAttempt).where(TaskAttempt.status == "running")
                    )
                    pubs = db.scalars(
                        select(Event).where(Event.kind == "experiment_publish")
                    ).all()
                    target_published = any(
                        e.details["attempt_id"] == record.get("target_attempt_id")
                        for e in pubs
                    )
                    needs_late = fault == "F2" and not target_published
                    if running is None and not needs_late:
                        break
            time.sleep(0.01)
        record["deadline_reached"] = time.monotonic() >= deadline
        record["observation_seconds"] = time.monotonic() - start
    except Exception:
        record["harness_error"] = traceback.format_exc()
    finally:
        for process in processes.values():
            if process.poll() is None:
                # SIGCONT ensures stopped workers can be reaped on every exit path.
                process.send_signal(signal.SIGCONT)
                process.kill()
            process.wait()
        record["process_exit_codes"] = {k: p.returncode for k, p in processes.items()}
        for handle in handles:
            handle.close()
        if engine is not None:
            try:
                with sessionmaker(engine)() as db:
                    record.update(
                        tasks=rows(db, Task),
                        attempts=rows(db, TaskAttempt),
                        events=rows(db, Event),
                        workflows=rows(db, Workflow),
                    )
                    wf = db.get(Workflow, record.get("workflow_id", ""))
                    record["final_status"] = wf.status if wf else None
                    record["output_correct"] = bool(
                        wf and wf.status == "succeeded" and wf.output == EXPECTED
                    )
                    record["execution_counts"] = {
                        task["id"]: sum(
                            e["kind"] == "experiment_execution"
                            and e["details"]["task_id"] == task["id"]
                            for e in record["events"]
                        )
                        for task in record["tasks"]
                    }
            except Exception:
                record["snapshot_error"] = traceback.format_exc()
            engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.dispose()
    record["finished_at"] = utc()
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("EXPERIMENT_DATABASE_URL"))
    parser.add_argument(
        "--designs",
        nargs="+",
        default=["full", "no_fencing", "no_lease"],
        choices=["full", "no_fencing", "no_lease"],
    )
    parser.add_argument(
        "--faults", nargs="+", default=["F1", "F2", "F3"], choices=["F1", "F2", "F3"]
    )
    parser.add_argument("--leases", nargs="+", type=int, default=[2, 5])
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--seed-start", type=int, default=1000)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "experiments/results/raw.jsonl"
    )
    args = parser.parse_args()
    if (
        not args.database_url
        or make_url(args.database_url).get_backend_name() != "postgresql"
    ):
        parser.error(
            "A PostgreSQL --database-url or EXPERIMENT_DATABASE_URL is required"
        )
    if args.output.exists():
        parser.error("Output exists; choose a new path so no trials are overwritten")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logs = args.output.parent / (args.output.stem + "_workers")
    logs.mkdir(exist_ok=True)
    cells = [
        (d, f, lease, s)
        for d in args.designs
        for f in args.faults
        for lease in args.leases
        for s in range(args.seed_start, args.seed_start + args.trials)
    ]
    random.Random(20261102).shuffle(cells)
    with args.output.open("x") as out, ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(trial, args.database_url, *cell, logs) for cell in cells]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            out.write(
                json.dumps(result, default=lambda x: x.isoformat(), sort_keys=True)
                + "\n"
            )
            out.flush()
            print(
                f"{i}/{len(cells)} {result['design']}/{result['fault']}/"
                f"{result['lease_seconds']} seed={result['seed']} "
                f"{result.get('final_status')} error={bool(result['harness_error'])}",
                flush=True,
            )


if __name__ == "__main__":
    main()
