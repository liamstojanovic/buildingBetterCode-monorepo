"""Export real core events and five paired synthetic trials; no provider spend.

Run with the core's pinned Python environment. This script writes only web/public.
It creates all runtime databases in a temporary directory and never changes core.
"""

import argparse
import json
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "src"))

from canopy.contracts import BudgetLimits, Evidence, Policy, RunConfig
from canopy.handlers import synthetic_registry, synthetic_task
from canopy.runtime import create_runtime


def evaluate(task, result):
    amount = next(
        e.data["amount_minor"]
        for e in task.evidence
        if e.evidence_id == "synthetic-catalog"
    )
    expected = {
        "read_catalog": {
            "sku": "DEMO-001",
            "amount_minor": amount,
            "currency": "USD",
            "source_version": next(
                e.version for e in task.evidence if e.evidence_id == "synthetic-catalog"
            ),
            "synthetic": True,
        },
        "summarize_quote": {
            "summary": f"Synthetic quote for DEMO-001: {amount} USD minor units.",
            "amount_minor": amount,
            "synthetic": True,
            "provider": "deterministic-python-fixture",
        },
        "greeting": {"message": "Hello, Canopy demo.", "synthetic": True},
    }
    correct = sum(result.output.get(key) == value for key, value in expected.items())
    return {
        "run_id": result.run_id,
        "passed": result.status == "completed" and correct == 3,
        "checked_fields": 3,
        "correct_fields": correct,
        "method": "Independent exact-output checks for the three deterministic synthetic handlers.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run five paired repetitions of each case",
    )
    args = parser.parse_args()
    repetitions = 5 if args.benchmark else 1
    recordings, evaluations, cases = {}, [], {}
    initial_task = synthetic_task(observed_at=datetime.now(UTC))
    config = RunConfig(
        actor_id="local-demo-operator",
        permissions=["execute:*"],
        policy=Policy(allowed_tools=["synthetic.catalog", "synthetic.greeting"]),
        budget=BudgetLimits(max_steps=10),
        model_fingerprint="synthetic-python-fixture-v1",
    )
    with TemporaryDirectory(prefix="canopy-web-benchmark-") as temporary:
        for case in ("cold", "warm", "changed", "unrelated"):
            timings = {"baseline": [], "governed": []}
            for repetition in range(repetitions):
                with closing(
                    create_runtime(
                        str(Path(temporary) / f"{case}-{repetition}.sqlite3"),
                        registry=synthetic_registry(),
                    )
                ) as runtime:
                    task = initial_task.model_copy(deep=True)
                    if case != "cold":
                        seed = runtime.run_task(task, config)
                        if repetition == repetitions - 1 and case == "warm":
                            recordings["seed"] = {
                                "task": task.model_dump(mode="json"),
                                "result": seed.model_dump(mode="json"),
                                "events": [
                                    e.model_dump(mode="json")
                                    for e in runtime.store.events(
                                        task.tenant_id, seed.run_id
                                    )
                                ],
                            }
                    if case == "changed":
                        task.evidence[0].version = "2"
                        task.evidence[0].data["amount_minor"] = 3100
                    if case == "unrelated":
                        task.evidence.append(
                            Evidence(
                                evidence_id="unrelated-source",
                                tenant_id=task.tenant_id,
                                source="synthetic://unrelated",
                                version="2",
                                observed_at=task.evidence[0].observed_at,
                                data={"note": "No step consumes this source."},
                            )
                        )
                    modes = (
                        ("baseline", "governed")
                        if repetition % 2 == 0
                        else ("governed", "baseline")
                    )
                    for mode in modes:
                        current_task = task.model_copy(
                            update={"task_id": f"web-{case}-{repetition}-{mode}"},
                            deep=True,
                        )
                        result = runtime.run_task(
                            current_task, config.model_copy(update={"mode": mode})
                        )
                        evaluation = evaluate(current_task, result)
                        if not evaluation["passed"]:
                            raise AssertionError(f"Incorrect result: {case}/{mode}")
                        timings[mode].append(result.usage.elapsed_ms)
                        evaluations.append(evaluation)
                        if repetition == repetitions - 1:
                            label = (
                                "baseline"
                                if case == "warm" and mode == "baseline"
                                else case
                                if mode == "governed"
                                else f"baseline_{case}"
                            )
                            recordings[label] = {
                                "task": current_task.model_dump(mode="json"),
                                "result": result.model_dump(mode="json"),
                                "events": [
                                    e.model_dump(mode="json")
                                    for e in runtime.store.events(
                                        task.tenant_id, result.run_id
                                    )
                                ],
                            }
            cases[case] = timings
    output = ROOT / "web" / "public" / "recordings"
    output.mkdir(parents=True, exist_ok=True)
    bundle = {
        "format": "canopy-comparison/v1",
        "description": "Actual core execution with deterministic synthetic handlers. Zero live provider requests; costs and energy are unknown. Timings measure this local fixture only.",
        "recordings": recordings,
        "evaluations": evaluations,
        "benchmark": {"repetitions": repetitions, "cases": cases},
    }
    (output / "core-synthetic.json").write_text(json.dumps(bundle, indent=2) + "\n")
    (output / "core-task.json").write_text(
        initial_task.model_dump_json(indent=2) + "\n"
    )
    print(
        f"Exported {len(recordings)} recordings and {repetitions} paired repetitions per case to {output}"
    )


if __name__ == "__main__":
    main()
