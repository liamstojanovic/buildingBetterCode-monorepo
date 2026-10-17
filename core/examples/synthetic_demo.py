"""Credential-free end-to-end fixture; this is not a live model benchmark.

From the repository root, run:
    .venv/Scripts/python.exe core/examples/synthetic_demo.py

Each invocation uses a fresh temporary SQLite database unless --db is supplied.
The demo reports observed execution results; it makes no cost or energy claims.
"""

import argparse
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from canopy.contracts import BudgetLimits, Policy, RunConfig
from canopy.handlers import synthetic_registry, synthetic_task
from canopy.runtime import Runtime, create_runtime, run_task


def run_demo(db_path: str) -> dict[str, Any]:
    with closing(create_runtime(db_path, registry=synthetic_registry())) as runtime:
        return _run_scenario(runtime)


def _run_scenario(runtime: Runtime) -> dict[str, Any]:
    task = synthetic_task()
    config = RunConfig(
        actor_id="synthetic-demo-operator",
        permissions=["execute:*"],
        policy=Policy(allowed_tools=["synthetic.catalog", "synthetic.greeting"]),
        budget=BudgetLimits(max_steps=10),
        model_fingerprint="synthetic-python-fixture-v1",
    )
    baseline = run_task(task, config.model_copy(update={"mode": "baseline"}), runtime=runtime)
    governed = run_task(task, config, runtime=runtime)
    warm = run_task(task, config, runtime=runtime)
    changed = synthetic_task(source_version="2", source_amount_minor=3100)
    invalidated = run_task(changed, config, runtime=runtime)
    refused = run_task(
        changed,
        config.model_copy(
            update={
                "cache_enabled": False,
                "budget": BudgetLimits(max_steps=0),
            }
        ),
        runtime=runtime,
    )
    replay = run_task(
        task,
        config.model_copy(update={"mode": "replay", "replay_run_id": governed.run_id}),
        runtime=runtime,
    )
    runs = {
        "baseline": baseline,
        "governed": governed,
        "warm": warm,
        "source_changed": invalidated,
        "budget_refused": refused,
        "replay": replay,
    }
    return {
        "synthetic": True,
        "description": "Local Python fixture; no live provider calls, tokens, cost, or energy claims.",
        "runs": {label: result.model_dump(mode="json") for label, result in runs.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Optional persistent SQLite database path")
    args = parser.parse_args()
    if args.db:
        report = run_demo(str(args.db))
    else:
        with TemporaryDirectory(prefix="canopy-synthetic-") as directory:
            report = run_demo(str(Path(directory) / "canopy.sqlite3"))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
