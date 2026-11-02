# Fixed protocol (written before any trial)

540 primary trials: full, no_fencing, no_lease × F1, F2, F3 × 2, 5 seconds × seeds 1000–1029. No 10-second cells planned. Repeat full/F2/2 with the same 30 seeds. No parameter tuning after inspecting outcomes.

Each trial gets a fresh PostgreSQL schema containing the application's SQLAlchemy tables and the existing demo_plan() DAG. No shared workflow state, no API calls, no LLM. CSV is item,sales / A,10 / B,20. Expected output is an explicit checked-in fixture.

Two real Python worker processes call the production tick(), with a 20 ms idle polling interval. Startup calls production recover_running_tasks(). Delay is 0.4 × lease after each agent handler returns, inside dispatch before publication. A durable event records each completed handler invocation before the delay. Thus a killed invocation has already computed its output but has not published it. This studies computation/publication recovery, not partially applied external side effects.

For F1/F2, wait for the first execution event, then wait a seeded uniform 0.05–0.15 × lease before signaling that attempt's worker. F1 uses SIGKILL. F2 uses SIGSTOP and resumes at the first attempt's lease deadline + 0.25 × lease. F3 has the same delays with no signal. Signal scheduling is identical across designs; it does not wait for a particular design outcome.

Timeout is 10 × lease + 30 seconds from worker launch. After terminal status, allow already-running invocations to finish so stale publication attempts remain visible. Snapshot all attempts, tasks and events, plus fault scheduling and process exits. Never discard errors or failed trials. Eight isolated trials may run concurrently to limit elapsed runtime. This adds shared-host scheduling noise.

Primary estimates: successful completion and correct output proportions over all trials; stale accepted publication proportion from explicit ownership-disposition events; stuck proportion at deadline; executions minus one over **all** tasks (including -1 for never-executed tasks), additionally max(executions-1,0) to avoid hiding missing work. Bootstrap resamples trials, preserving the three-task cluster, 5000 replicates, seed 20261102. Wilson intervals use z=1.95996398454. Recovery times are observed-only medians/IQR; missing/censored observations remain missing and sample sizes are reported. No significance tests planned; this is a descriptive study with many cells.

Reproducibility checks compare byte-level logs as requested and normalized categorical outcomes separately. UUIDs, wall times, scheduling, process IDs and elapsed durations are expected to differ; same seeds cannot make OS scheduling deterministic.

## Disclosed analysis amendment

During the primary run, after inspecting early outcomes, the analysis was extended to include the user's requested exploratory two-sided Fisher exact comparisons for completion, correctness, stuck status and stale acceptance, and Mann–Whitney U comparisons for observed restart/completion times. Compare full against each baseline within each fault/lease cell, report all 72 comparisons, unadjusted p-values and sample sizes, and leave times with an empty group untested. This replaces the initial omission of significance tests; no fault timing, parameters, seeds, outcomes or exclusions were changed. Treat these tests as exploratory and consider multiple comparisons and time censoring.
