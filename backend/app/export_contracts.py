"""Generate or check the committed JSON Schema documents."""

import argparse
import json
from pathlib import Path

from .contracts import (
    AgentRequest,
    AgentResult,
    Contract,
    InspectionOutput,
    SummaryOutput,
    VerificationOutput,
)

SCHEMA_DIRECTORY = Path(__file__).resolve().parents[1] / "contracts"


def schemas() -> dict[str, dict]:
    models: dict[str, type[Contract]] = {
        "agent-request.v1.json": AgentRequest,
        "agent-result.v1.json": AgentResult,
        "csv-inspection.v1.json": InspectionOutput,
        "csv-summary.v1.json": SummaryOutput,
        "verification.v1.json": VerificationOutput,
    }
    return {
        name: {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }
        for name, model in models.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=SCHEMA_DIRECTORY)
    args = parser.parse_args()
    if not args.check:
        args.output.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas().items():
        path = args.output / name
        text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != text:
                raise SystemExit(f"Contract schema is missing or outdated: {name}")
        else:
            path.write_text(text)


if __name__ == "__main__":
    main()
