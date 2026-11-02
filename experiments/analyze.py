"""All reported study figures are derived exclusively from the supplied raw JSONL."""

import argparse
from datetime import datetime
import csv
import itertools
import json
import math
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import fisher_exact, mannwhitneyu

DESIGNS = ("full", "no_fencing", "no_lease")
FAULTS = ("F1", "F2", "F3")
LEASES = (2, 5)


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def seconds(end, start):
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def metrics(row):
    events = row.get("events", [])
    publishes = [e["details"] for e in events if e["kind"] == "experiment_publish"]
    stale = any(p["stale"] and p["accepted"] for p in publishes)
    counts = list(row.get("execution_counts", {}).values())
    fault = row.get("fault_at")
    target = next(
        (a for a in row.get("attempts", []) if a["id"] == row.get("target_attempt_id")),
        None,
    )
    retries = [
        a["started_at"]
        for a in row.get("attempts", [])
        if fault
        and target
        and a["task_id"] == target["task_id"]
        and a["attempt_number"] > 1
        and a["started_at"] > fault
    ]
    completed = [e["created_at"] for e in events if e["kind"] == "workflow_succeeded"]
    terminal = row.get("final_status") in ("succeeded", "failed", "cancelled")
    return dict(
        completed=row.get("final_status") == "succeeded",
        stale=stale,
        stuck=not terminal,
        correct=bool(row.get("output_correct")),
        duplicate=np.mean([n - 1 for n in counts]) if counts else np.nan,
        extra=np.mean([max(n - 1, 0) for n in counts]) if counts else np.nan,
        missing=sum(n == 0 for n in counts),
        restart=seconds(min(retries), fault) if retries else None,
        recovery=seconds(min(completed), fault) if completed and fault else None,
        error=bool(row.get("harness_error") or row.get("snapshot_error")),
    )


def wilson(k, n):
    if not n:
        return ("", "", "")
    z = 1.959963984540054
    p = k / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    # Clamp floating-point roundoff at the 0/1 endpoints.
    return (p, max(0.0, min(p, center - half)), min(1.0, max(p, center + half)))


def bootstrap(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return ("", "", "")
    # Resample whole trials so dependent tasks stay in the same cluster.
    rng = np.random.default_rng(20261102)
    samples = rng.choice(values, (5000, len(values)), replace=True).mean(axis=1)
    return (float(values.mean()), *np.quantile(samples, [0.025, 0.975]).tolist())


def distribution(values):
    values = [v for v in values if v is not None]
    if not values:
        return (0, "", "", "")
    q1, med, q3 = np.quantile(values, [0.25, 0.5, 0.75])
    return (len(values), float(med), float(q1), float(q3))


def summarize(raw):
    summary = []
    for design, fault, lease in itertools.product(DESIGNS, FAULTS, LEASES):
        cell = [
            r
            for r in raw
            if (r["design"], r["fault"], r["lease_seconds"]) == (design, fault, lease)
        ]
        ms = [metrics(r) for r in cell]
        row = dict(design=design, fault=fault, lease_seconds=lease, n=len(cell))
        for key in ("completed", "stale", "stuck", "correct"):
            count = sum(m[key] for m in ms)
            row[key + "_count"] = count
            for suffix, value in zip(("rate", "low", "high"), wilson(count, len(ms))):
                row[key + "_" + suffix] = value
        for key in ("duplicate", "extra"):
            for suffix, value in zip(
                ("mean", "low", "high"), bootstrap([m[key] for m in ms])
            ):
                row[key + "_" + suffix] = value
        for key in ("restart", "recovery"):
            for suffix, value in zip(
                ("n", "median", "q1", "q3"), distribution([m[key] for m in ms])
            ):
                row[key + "_" + suffix] = value
        row["missing_tasks"] = sum(m["missing"] for m in ms)
        row["harness_errors"] = sum(m["error"] for m in ms)
        summary.append(row)
    return summary


def fmt(value):
    return f"{value:.3f}" if isinstance(value, (float, np.floating)) else str(value)


def interval(row, key, name="rate"):
    if row[key + "_" + name] == "":
        return "—"
    return f"{fmt(row[key + '_' + name])} [{fmt(row[key + '_low'])}, {fmt(row[key + '_high'])}]"


def timefmt(row, key):
    if not row[key + "_n"]:
        return "— (n=0)"
    return (
        f"{fmt(row[key + '_median'])} [{fmt(row[key + '_q1'])}, "
        f"{fmt(row[key + '_q3'])}] (n={row[key + '_n']})"
    )


def tables(summary):
    lines = [
        "Proportions and means show 95% confidence intervals. Times show median "
        "[Q1, Q3] in seconds with observed sample size.",
        "",
        "| Design | Fault | Lease | n | Completion | Correct output | Stale accepted | Stuck |",
        "|---|---|---:|---:|---|---|---|---|",
    ]
    for r in summary:
        lines.append(
            f"| {r['design']} | {r['fault']} | {r['lease_seconds']} | {r['n']} | "
            + " | ".join(
                interval(r, k) for k in ("completed", "correct", "stale", "stuck")
            )
            + " |"
        )
    lines += [
        "",
        "| Design | Fault | Lease | Executions − 1/task | Extra executions/task | "
        "Never-executed tasks | Fault → new attempt | Fault → completion |",
        "|---|---|---:|---|---|---:|---|---|",
    ]
    for r in summary:
        lines.append(
            f"| {r['design']} | {r['fault']} | {r['lease_seconds']} | "
            f"{interval(r, 'duplicate', 'mean')} | {interval(r, 'extra', 'mean')} | "
            f"{r['missing_tasks']} | {timefmt(r, 'restart')} | {timefmt(r, 'recovery')} |"
        )
    return "\n".join(lines) + "\n"


def plots(summary, out):
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), sharex=True)
    for i, fault in enumerate(("F1", "F2")):
        for j, key in enumerate(("restart", "recovery")):
            ax = axes[i, j]
            for design in DESIGNS:
                rs = [
                    r
                    for r in summary
                    if r["design"] == design and r["fault"] == fault and r[key + "_n"]
                ]
                if not rs:
                    continue
                x = [r["lease_seconds"] for r in rs]
                y = np.array([r[key + "_median"] for r in rs])
                lo = np.array([r[key + "_q1"] for r in rs])
                hi = np.array([r[key + "_q3"] for r in rs])
                ax.errorbar(
                    x, y, yerr=[y - lo, hi - y], marker="o", label=design, capsize=3
                )
            ax.set_title(
                f"{fault}: fault → {'new attempt' if key == 'restart' else 'completion'}"
            )
            ax.set_ylabel("Seconds (median, IQR)")
            ax.set_xlabel("Lease seconds")
            ax.set_xticks(LEASES)
            ax.grid(alpha=0.2)
            if ax.lines:
                ax.legend(fontsize=8)
    fig.suptitle("Observed recovery only; missing/censored times omitted (see table n)")
    fig.tight_layout()
    fig.savefig(out / "recovery.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(DESIGNS))
    for i, (fault, lease) in enumerate(itertools.product(FAULTS, LEASES)):
        rs = [
            next(
                r
                for r in summary
                if (r["design"], r["fault"], r["lease_seconds"]) == (d, fault, lease)
            )
            for d in DESIGNS
        ]
        y = np.array([r["stale_rate"] if r["n"] else np.nan for r in rs])
        low = np.array([r["stale_low"] if r["n"] else np.nan for r in rs])
        high = np.array([r["stale_high"] if r["n"] else np.nan for r in rs])
        ax.bar(
            x + (i - 2.5) * 0.12,
            y,
            width=0.12,
            label=f"{fault}, {lease}s",
            yerr=[y - low, high - y],
            capsize=2,
        )
    ax.set_xticks(x, DESIGNS)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("Trials with stale publication accepted (Wilson 95% CI)")
    ax.legend(ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "stale_writes.png", dpi=160)
    plt.close(fig)


def reproducibility(raw, repeat):
    original = {
        r["seed"]: r
        for r in raw
        if (r["design"], r["fault"], r["lease_seconds"]) == ("full", "F2", 2)
    }
    fields = (
        "completed",
        "correct",
        "stale",
        "stuck",
        "duplicate",
        "extra",
        "missing",
        "error",
    )
    comparisons = []
    for r in repeat:
        if r["seed"] not in original:
            continue
        a = original[r["seed"]]
        am, bm = metrics(a), metrics(r)
        comparisons.append(
            dict(
                seed=r["seed"],
                raw_equal=a == r,
                outcomes_equal=all(am[k] == bm[k] for k in fields),
                original={k: am[k] for k in fields},
                repeat={k: bm[k] for k in fields},
                restart_difference_seconds=(bm["restart"] - am["restart"])
                if am["restart"] is not None and bm["restart"] is not None
                else None,
            )
        )
    return dict(
        matched_seeds=len(comparisons),
        raw_equal=sum(x["raw_equal"] for x in comparisons),
        outcomes_equal=sum(x["outcomes_equal"] for x in comparisons),
        comparisons=comparisons,
    )


def audit(raw):
    issues = []
    seen = set()
    for row in raw:
        key = (row["design"], row["fault"], row["lease_seconds"], row["seed"])
        if key in seen:
            issues.append({"trial": key, "issue": "duplicate trial key"})
        seen.add(key)
        if row.get("harness_error") or row.get("snapshot_error"):
            issues.append({"trial": key, "issue": "harness or snapshot error"})
        if row["fault"] != "F3":
            attempt = next(
                (
                    a
                    for a in row.get("attempts", [])
                    if a["id"] == row.get("target_attempt_id")
                ),
                None,
            )
            event = next(
                (
                    e
                    for e in row.get("events", [])
                    if e["kind"] == "experiment_execution"
                    and attempt
                    and e["details"]["request_id"] == attempt["request_id"]
                ),
                None,
            )
            if not row.get("fault_at") or not event:
                issues.append({"trial": key, "issue": "missing fault/target execution"})
            elif (
                not 0
                <= seconds(row["fault_at"], event["created_at"])
                < row["delay_ms"] / 1000
            ):
                issues.append(
                    {"trial": key, "issue": "signal outside injected task window"}
                )
            if row["fault"] == "F2" and (
                not row.get("resumed_at")
                or seconds(row["resumed_at"], row["target_lease_expires_at"]) <= 0
            ):
                issues.append(
                    {"trial": key, "issue": "resume did not follow lease expiry"}
                )
        if len(row.get("tasks", [])) != 3:
            issues.append({"trial": key, "issue": "expected three tasks"})
    return {"trials_checked": len(raw), "issues": issues}


def comparisons(raw):
    results = []
    for baseline, fault, lease in itertools.product(
        ("no_fencing", "no_lease"), FAULTS, LEASES
    ):
        groups = [
            [
                metrics(r)
                for r in raw
                if (r["design"], r["fault"], r["lease_seconds"]) == (d, fault, lease)
            ]
            for d in ("full", baseline)
        ]
        for key in ("completed", "stale", "correct", "stuck", "restart", "recovery"):
            timed = key in ("restart", "recovery")
            values = [[m[key] for m in group if m[key] is not None] for group in groups]
            a, b = values
            pvalue = ""
            if a and b:
                if timed:
                    # Only observed times enter; censored outcomes remain in completion tests.
                    pvalue = float(
                        mannwhitneyu(
                            a, b, alternative="two-sided", method="asymptotic"
                        ).pvalue
                    )
                else:
                    pvalue = float(
                        fisher_exact(
                            [[sum(a), len(a) - sum(a)], [sum(b), len(b) - sum(b)]],
                            alternative="two-sided",
                        ).pvalue
                    )
            results.append(
                dict(
                    baseline=baseline,
                    fault=fault,
                    lease_seconds=lease,
                    metric=key,
                    test="Mann-Whitney U" if timed else "Fisher exact",
                    full_n=len(a),
                    baseline_n=len(b),
                    full_successes="" if timed else sum(a),
                    baseline_successes="" if timed else sum(b),
                    p_value=pvalue,
                )
            )
    return results


def comparison_table(results):
    lines = [
        "Exploratory two-sided comparisons; unadjusted p-values. Many cells and metrics "
        "create multiple comparisons. Time tests use observed recoveries only and cannot "
        "establish a benefit when one design has only censored times. The tests were "
        "added after initial data inspection to satisfy the requested comparison methods; "
        "they were not in the initial analysis plan. No parameter or trial exclusion changed.",
        "",
        "| Baseline | Fault | Lease | Metric | Test | Full n | Baseline n | p (unadjusted) |",
        "|---|---|---:|---|---|---:|---:|---:|",
    ]
    for r in results:
        p = f"{r['p_value']:.6g}" if r["p_value"] != "" else "—"
        lines.append(
            f"| {r['baseline']} | {r['fault']} | {r['lease_seconds']} | {r['metric']} | "
            f"{r['test']} | {r['full_n']} | {r['baseline_n']} | {p} |"
        )
    return "\n".join(lines) + "\n"


def findings(raw, summary, table, repro, tests, auxiliary):
    ms = [metrics(r) for r in raw]
    checked = audit(raw)
    surprises = []
    for row, m in zip(raw, ms):
        expected_success = not (row["design"] == "no_lease" and row["fault"] == "F1")
        expected_stale = row["design"] == "no_fencing" and row["fault"] == "F2"
        if (
            m["completed"] != expected_success
            or m["stale"] != expected_stale
            or (row["fault"] == "F3" and (m["extra"] != 0 or m["missing"] != 0))
        ):
            surprises.append(
                f"{row['design']}/{row['fault']}/{row['lease_seconds']}/seed={row['seed']}"
            )
    lines = [
        "# Findings",
        "",
        "Generated by experiments/analyze.py from raw JSONL; "
        "the exploratory comparison tests are disclosed below.",
        "",
        f"Primary trials: {len(raw)}. Planned: 540, with 30 in each of 18 cells. "
        f"Harness/snapshot errors: {sum(m['error'] for m in ms)}. "
        "Pilot, concurrent validation and sequential repeat trials are retained separately and excluded from primary estimates "
        "by their named role, not their outcomes. No primary trial was excluded.",
        "",
        f"Retained auxiliary trials: {auxiliary.get('pilot', 0)} pilot, "
        f"{auxiliary.get('concurrent_repeat', 0)} concurrent validation, "
        f"{auxiliary.get('repeat', 0)} sequential repeat. These are outside the 540 primary trials.",
        "",
        f"Protocol audit: {len(checked['issues'])} issues across {checked['trials_checked']} trials. "
        "See results/protocol_audit.json; audit flags do not exclude records.",
        "",
        table,
        "## Comparisons and interpretation",
        "",
    ]
    for fault, lease in itertools.product(FAULTS, LEASES):
        rs = {
            d: next(
                r
                for r in summary
                if (r["design"], r["fault"], r["lease_seconds"]) == (d, fault, lease)
            )
            for d in DESIGNS
        }
        for baseline in ("no_fencing", "no_lease"):
            a, b = rs["full"], rs[baseline]
            comparison = (
                (
                    "Completion tied. "
                    if a["completed_rate"] == b["completed_rate"]
                    else "Full had higher completion. "
                    if a["completed_rate"] > b["completed_rate"]
                    else "Full had lower completion. "
                )
                if a["n"] and b["n"]
                else "Comparison unavailable. "
            )
            if a["n"] and b["n"]:
                comparison += (
                    "Full accepted fewer stale results. "
                    if a["stale_rate"] < b["stale_rate"]
                    else "Stale acceptance tied. "
                    if a["stale_rate"] == b["stale_rate"]
                    else "Full accepted more stale results. "
                )
            if a["recovery_n"] and b["recovery_n"]:
                comparison += (
                    f"Fault-to-completion medians: {fmt(a['recovery_median'])}s "
                    f"vs {fmt(b['recovery_median'])}s. "
                )
                difference = a["recovery_median"] - b["recovery_median"]
                comparison += f"Observed median difference (full minus baseline): {difference:.6f}s. "

            lines.append(
                f"- {fault}, {lease}s: full vs {baseline}: successful workflows "
                f"{a['completed_count']}/{a['n']} vs {b['completed_count']}/{b['n']}; "
                f"stale acceptance {a['stale_count']}/{a['n']} vs "
                f"{b['stale_count']}/{b['n']}; extra executions/task "
                f"{fmt(a['extra_mean'])} vs {fmt(b['extra_mean'])}. "
                + comparison
                + "See the exploratory tests below; comparisons do not establish real-world fault rates."
            )
    lines += [
        "",
        "The pre-run expectations were: leases allow recovery after F1; fencing rejects "
        "the late F2 result; F3 has no duplicate execution. Check each observed cell above "
        "against those expectations. "
        f"Unexpected trial outcomes under those explicit expectations: {len(surprises)}. "
        + ("Affected trials: " + ", ".join(surprises) + ". " if surprises else "")
        + "The full design does not guarantee exactly-once "
        "computation: an expired attempt may have already executed the deterministic agent. "
        "Stale results can be accepted while output values remain correct because the "
        "agents compute identical values. Provenance events, rather than output inequality, "
        "therefore measure stale publication.",
        "",
        "The signed executions-minus-one metric can be negative when downstream tasks never "
        "execute. Extra executions are also reported with a floor of zero; missing tasks "
        "are reported separately. Recovery distributions condition on observed recovery; "
        "timeouts are censored and must not be interpreted as zero recovery time.",
        "",
        "## Exploratory comparisons",
        "",
        comparison_table(tests),
        "## Reproducibility",
        "",
    ]
    if repro:
        lines.append(
            f"Repeated full/F2/2s with the same seeds: {repro['matched_seeds']} matched "
            f"trials; {repro['raw_equal']} identical raw records; "
            f"{repro['outcomes_equal']} identical normalized categorical/count outcomes. "
            "Exact raw reproducibility is not established: UUIDs, timestamps, scheduling "
            "and durations vary. See results/reproducibility.json for every comparison."
        )
    else:
        lines.append("Repeat data not supplied; reproducibility remains unverified.")
    lines += [
        "",
        "## Limits and unfinished work",
        "",
        "Single machine; deterministic millisecond agents; delays injected after the handler "
        "returns and before publication; injected faults are not real-world fault rates; "
        "short leases used for speed. Concurrent isolated trials contend for the same host "
        "and PostgreSQL server. No external side effects, network partitions, lease renewal, "
        "automatic retries after agent errors, or real-world workloads were studied. "
        "Production idle polling is one second; the harness uses 20 ms to bound runtime. "
        "Schema creation uses current application metadata, not a migration-path experiment. "
        "No 10-second trials or inference about routing, LLM quality, or the 78-case benchmark.",
        "",
        "Docker was installed but its daemon was unavailable. The existing suite ran through "
        "the local Python equivalent; before/after results are recorded separately. "
        "Environment and exact commands are in README.md. P-values are unadjusted and "
        "multiple comparisons limit their interpretation.",
        "",
    ]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, default=Path("experiments/results/raw.jsonl"))
    p.add_argument(
        "--repeat", type=Path, default=Path("experiments/results/repeat.jsonl")
    )
    p.add_argument("--out", type=Path, default=Path("experiments/results"))
    args = p.parse_args()
    raw = read(args.raw)
    args.out.mkdir(parents=True, exist_ok=True)
    summary = summarize(raw)
    (args.out / "protocol_audit.json").write_text(json.dumps(audit(raw), indent=2))
    with (args.out / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    tests = comparisons(raw)
    with (args.out / "comparisons.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(tests[0]))
        writer.writeheader()
        writer.writerows(tests)
    (args.out / "comparisons.md").write_text(comparison_table(tests))
    table = tables(summary)
    (args.out / "tables.md").write_text(table)
    plots(summary, args.out)
    repro = reproducibility(raw, read(args.repeat)) if args.repeat.exists() else None
    if repro:
        (args.out / "reproducibility.json").write_text(json.dumps(repro, indent=2))
    auxiliary = {
        name: len(read(args.raw.parent / f"{name}.jsonl"))
        for name in ("pilot", "concurrent_repeat", "repeat")
        if (args.raw.parent / f"{name}.jsonl").exists()
    }
    (args.out.parent / "FINDINGS.md").write_text(
        findings(raw, summary, table, repro, tests, auxiliary)
    )
    print(
        f"Analyzed {len(raw)} primary trials; wrote {len(summary)} cells to {args.out}"
    )


if __name__ == "__main__":
    main()
