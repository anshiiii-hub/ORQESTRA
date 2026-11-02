"""Verify the fixed study's coverage, Wilson intervals and repeatable analysis artifacts."""

from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments/results"
ARTIFACTS = (
    "summary.csv",
    "tables.md",
    "comparisons.csv",
    "comparisons.md",
    "reproducibility.json",
    "protocol_audit.json",
    "recovery.png",
    "stale_writes.png",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    raw = [
        json.loads(line) for line in (RESULTS / "raw.jsonl").read_text().splitlines()
    ]
    cells = Counter((r["design"], r["fault"], r["lease_seconds"]) for r in raw)
    assert len(cells) == 18 and set(cells.values()) == {30}
    assert (
        len({(r["design"], r["fault"], r["lease_seconds"], r["seed"]) for r in raw})
        == 540
    )
    assert all(not r["harness_error"] and not r.get("snapshot_error") for r in raw)
    assert not json.loads((RESULTS / "protocol_audit.json").read_text())["issues"]
    for row in csv.DictReader((RESULTS / "summary.csv").open()):
        for key in ("completed", "correct", "stuck", "stale"):
            ci = binomtest(int(row[key + "_count"]), int(row["n"])).proportion_ci(
                method="wilson"
            )
            assert abs(ci.low - float(row[key + "_low"])) < 1e-12
            assert abs(ci.high - float(row[key + "_high"])) < 1e-12
    paths = [RESULTS / name for name in ARTIFACTS] + [ROOT / "experiments/FINDINGS.md"]
    before = {str(path): digest(path) for path in paths}
    # The computation must reproduce artifacts exactly from the same saved observations.
    subprocess.run(
        [sys.executable, str(ROOT / "experiments/analyze.py")],
        cwd=ROOT,
        env={
            **os.environ,
            "MPLCONFIGDIR": os.getenv("MPLCONFIGDIR", "/tmp/orqestra-mpl"),
        },
        check=True,
    )
    assert before == {str(path): digest(path) for path in paths}
    checks = dict(
        primary_trials=len(raw),
        cells=len(cells),
        trials_per_cell=30,
        unique_trial_keys=True,
        wilson_matches_scipy=True,
        analysis_regenerates_identically=True,
    )
    (RESULTS / "verification.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
