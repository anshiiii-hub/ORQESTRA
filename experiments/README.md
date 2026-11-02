# Lease and fencing study

This is a descriptive fault-injection study of the production worker `tick()` on PostgreSQL. Read [PLAN.md](PLAN.md) for the protocol fixed before the primary data was collected and [FINDINGS.md](FINDINGS.md) for computed results. The 78-case benchmark is outside this study.

## Files a student needs to explain

- `run_trials.py`: creates a fresh PostgreSQL schema per trial, creates the existing three-task demo DAG, launches two processes, injects one signal, snapshots tables, then removes only its own schema. It refuses SQLite and refuses to overwrite raw files.
- `worker_process.py`: calls the production startup recovery and `tick()` in a loop, polling every 20 ms when idle.
- `analyze.py`: reads raw JSONL, computes all estimates and intervals, compares repeat outcomes, and writes the tables, plots and findings. No database access; exploratory comparison tests are disclosed separately.
- `verify_results.py`: checks all 18 cells have 30 unique trials, independently verifies Wilson intervals with SciPy, and checks exact regeneration of the saved summaries and plots.
- `expected_output.json`: hand-checked expected verification output for `item,sales\nA,10\nB,20` (sales sum 30, mean 15, minimum 10, maximum 20).
- `results/`: primary raw JSONL, separate pilot/repeat JSONL, per-worker logs archived in `worker_logs.tar.gz`, environment/package records, test results, generated summary, plots and reproducibility comparison.

## Flags and defaults

`TASK_LEASE_SECONDS=360`, `EXPERIMENT_DESIGN=full`, `EXPERIMENT_AGENT_DELAY_MS=0`, `EXPERIMENT_RECORD=0` preserve current behavior. Experiment variables are passed directly to each child worker by the runner; no change to deployment configuration is needed.

`full` keeps the original ownership predicate and lease recovery. `no_fencing` bypasses that predicate at publication, retaining the workflow lifecycle gate and lease recovery. `no_lease` disables expiry requeue in both `tick()` and startup recovery, retaining fencing. There is no lease renewal or automatic retry after an agent error.

Recording adds a durable event after each handler successfully returns, before the injected delay. Thus counts mean actual **completed handler invocations**, including computations killed before publication. In these trials faults are scheduled only after that event, so none can kill a partially executed handler. This deliberately gives deterministic millisecond agents a window for the stale-result scenario; it is not a test of external side-effect idempotency. Publication events record whether the original ownership predicate failed and whether the result was accepted, with request/attempt provenance. Identical output values cannot identify stale writes by themselves.

## Reproduce from a clean clone

Use a dedicated local PostgreSQL instance/account that permits CREATE SCHEMA. The runner creates all current application tables from SQLAlchemy metadata inside fresh schemas; it does not reset or drop existing database tables. The examples below use PostgreSQL tools installed on PATH. Run from the repository root on macOS or Linux (POSIX signals required).

```sh
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
backend/.venv/bin/python -m pip install -r experiments/requirements.txt

# Use a new directory and unused port. Trust auth is local and disposable.
initdb -D /tmp/orqestra-study-pg -A trust
pg_ctl -D /tmp/orqestra-study-pg -l /tmp/orqestra-study-pg.log \
  -o '-p 55439 -h 127.0.0.1 -k /tmp -c max_connections=200' start
export EXPERIMENT_DATABASE_URL="postgresql+psycopg://$(whoami)@127.0.0.1:55439/postgres"

# Required baseline. Docker route requires a running daemon and services.
docker compose exec api pytest -q
# Local equivalent used in this run:
backend/.venv/bin/python -m pytest -q backend/tests

# Use new paths, preserving the checked-in observations.
backend/.venv/bin/python experiments/run_trials.py \
  --output experiments/reproduction/raw.jsonl
backend/.venv/bin/python experiments/run_trials.py \
  --designs full --faults F2 --leases 2 --trials 30 --seed-start 1000 --jobs 8 \
  --output experiments/reproduction/repeat.jsonl
export MPLCONFIGDIR=/tmp/orqestra-study-matplotlib
backend/.venv/bin/python experiments/analyze.py \
  --raw experiments/reproduction/raw.jsonl \
  --repeat experiments/reproduction/repeat.jsonl \
  --out experiments/reproduction

backend/.venv/bin/python -m pytest -q backend/tests
# Separate disposable database: test fixtures downgrade its schema after each test.
createdb -h 127.0.0.1 -p 55439 orqestra_study_tests
TEST_DATABASE_URL="postgresql+psycopg://$(whoami)@127.0.0.1:55439/orqestra_study_tests" \
  backend/.venv/bin/python -m pytest -q backend/tests
pg_ctl -D /tmp/orqestra-study-pg stop
```

To regenerate the committed summaries without experiments:

```sh
MPLCONFIGDIR=/tmp/orqestra-study-matplotlib backend/.venv/bin/python experiments/analyze.py
MPLCONFIGDIR=/tmp/orqestra-study-matplotlib backend/.venv/bin/python experiments/verify_results.py
```

The pilot command used all three designs, `--faults F2 --leases 2 --trials 1 --seed-start 9000 --jobs 3 --output experiments/results/pilot.jsonl`. Pilot records are retained, but are not primary observations. The primary command used defaults: all designs/faults, leases 2 and 5, 30 seeds, eight concurrent isolated trials. An additional concurrent validation used the same repeat cell with two jobs; it is retained as `concurrent_repeat.jsonl` and excluded from primary estimates. It overlapped the primary run, so peak concurrent trials were ten. The final sequential repeat used eight jobs and `experiments/results/repeat.jsonl`, after the primary run completed. Trial ordering uses seed 20261102. Each cell uses seeds 1000–1029. Fault offsets use an independent `random.Random(seed)`. Bootstrap seed is 20261102 with 5000 trial-cluster replicates.

The timeout is `10 * lease + 30` seconds from launch (50 or 80 seconds), including worker startup. Logs also include startup timestamps, declared lease deadlines, expiry/requeue event timestamps and attempt start/finish times. Terminal workflows are observed until their in-flight publication attempts finish, within that same deadline. Full raw records will differ on rerun due to UUIDs, wall times, process scheduling and durations. `reproducibility.json` reports exact raw equality and normalized outcome equality separately; seeds do not make concurrency deterministic.

## Analysis definitions

Completion means `succeeded`, with denominator all primary trials in that cell. Output correctness also uses all trials, and requires exact equality with the expected fixture. Stale acceptance means at least one successful publication whose original ownership predicate failed, even if another result later overwrote it. This records a stale result becoming a task's published result at any time, not necessarily remaining the final workflow output. Stuck means the workflow has not reached succeeded/failed/cancelled by its deadline. Harness errors are kept and counted separately.

Wilson 95% intervals describe proportions. Executions minus one is averaged over all three tasks; never-executed tasks contribute -1. A separate nonnegative extra-execution metric and missing-task count make that distinction explicit. Bootstrap intervals resample complete trials rather than treating the three dependent tasks as independent samples. Recovery is fault → next attempt for the affected task, or fault → workflow success. Medians and quartiles use observed recoveries only; the tables show their sample sizes. F3 has no recovery time. Missing values use placeholders only when no observation exists. The initial plan omitted tests. During the run, after inspecting early outcomes, an explicit analysis amendment added the requested exploratory two-sided Fisher exact tests for proportions and Mann–Whitney U tests for observed times. All 72 requested within-cell metric comparisons are reported in `comparisons.csv`/`comparisons.md`, with sample sizes and unadjusted p-values. Empty time groups are not tested. Multiple comparisons and time censoring limit interpretation.

## Observed environment

Apple M4, 10 logical CPUs, 16 GiB RAM; macOS 15.5 arm64; Python 3.12.15; local Homebrew PostgreSQL 18.3. Docker client 29.9.2 was installed but the daemon was unavailable, so the equivalent local test command and local PostgreSQL were used. This is a version deviation from Compose's PostgreSQL 16 image. Exact library versions are in `results/environment.json` and `results/python_packages.txt`. Baseline commit: `8f4ce212786bc37ae9ad6b694e16b1f7847329e2`; branch: `lease-fencing-study`. The pre-existing frontend edit was left untouched.

The observed baseline was 282 passed, 4 skipped, rather than the count supplied in the request. After adding six ablation tests, the local suite passed with 288 passed and 4 skipped; the PostgreSQL suite passed all 292 with none skipped. Before/after and PostgreSQL test outputs are retained under `results/tests_*.txt`. No paid APIs or planning calls are used by the harness.
