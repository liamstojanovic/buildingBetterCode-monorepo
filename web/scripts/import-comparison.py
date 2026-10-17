"""Convert retained core live-comparison reports to frontend recordings.

This reads saved results and databases only. It never runs models or tools.
Usage: python scripts/import-comparison.py /path/to/comparison/report.json
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "src"))
from canopy.recording import ExecutionStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "web" / "public" / "recordings" / "provider-comparison.json",
    )
    parser.add_argument(
        "--evaluations",
        type=Path,
        help="Optional independently produced Evaluation[] JSON",
    )
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(ROOT / "web"):
        parser.error("Frontend exports must stay inside web/.")
    recordings = {}
    timings = {
        "cold": {"baseline": [], "governed": [], "trials": []},
        "warm": {"baseline": [], "governed": [], "trials": []},
    }
    identity = None
    for report_path in args.reports:
        report = json.loads(report_path.read_text())
        if report.get("schema_version") != "1" or not report.get("runs"):
            raise ValueError("Expected a version 1 core live_comparison report")
        task_identity = {
            key: value for key, value in report["task"].items() if key != "task_id"
        }
        current_identity = json.dumps(
            [report["model_fingerprint"], task_identity], sort_keys=True
        )
        if identity is not None and current_identity != identity:
            raise ValueError(
                "Repeated reports must use the same task snapshot and model fingerprint"
            )
        identity = current_identity
        directory = Path(report.get("artifacts_directory", report_path.parent))
        if not directory.is_dir():
            directory = report_path.parent
        for label, alias, store_name, case, mode in [
            ("baseline_first", "baseline_cold", "baseline", "cold", "baseline"),
            ("baseline_repeat", "baseline", "baseline", "warm", "baseline"),
            ("governed_cold", "cold", "governed", "cold", "governed"),
            ("governed_warm", "warm", "governed", "warm", "governed"),
        ]:
            result = report["runs"][label]
            with ExecutionStore(
                directory / f"{store_name}.sqlite3", read_only=True
            ) as store:
                recorded = store.get_run(result["tenant_id"], result["run_id"])
                if (
                    recorded.result is None
                    or recorded.result.model_dump(mode="json") != result
                ):
                    raise ValueError(f"Report and recorded result disagree for {label}")
                recordings[alias] = {
                    "task": recorded.task.model_dump(mode="json"),
                    "result": result,
                    "events": [
                        event.model_dump(mode="json")
                        for event in store.events(result["tenant_id"], result["run_id"])
                    ],
                }
            if result["usage"].get("elapsed_known"):
                timings[case][mode].append(result["usage"]["elapsed_ms"])
        for case, base_name, governed_name in [
            ("cold", "baseline_first", "governed_cold"),
            ("warm", "baseline_repeat", "governed_warm"),
        ]:
            baseline, governed = (
                report["runs"][base_name],
                report["runs"][governed_name],
            )
            timings[case]["trials"].append(
                {
                    "baseline_run_id": baseline["run_id"],
                    "governed_run_id": governed["run_id"],
                    "baseline_cost_usd": baseline["usage"]["cost_usd"],
                    "governed_cost_usd": governed["usage"]["cost_usd"],
                    "completed": baseline["status"]
                    == governed["status"]
                    == "completed",
                    "synthetic": baseline["synthetic"] or governed["synthetic"],
                }
            )
    evaluations = json.loads(args.evaluations.read_text()) if args.evaluations else []
    bundle = {
        "format": "canopy-comparison/v1",
        "description": "Retained core comparison. Provider measurements are preserved; independent quality scoring is supplied separately. Playback makes zero provider calls.",
        "recordings": recordings,
        "evaluations": evaluations,
        "benchmark": {"repetitions": len(args.reports), "cases": timings},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(bundle, indent=2) + "\n")
    print(f"Exported four recordings to {destination}; no inference was executed.")


if __name__ == "__main__":
    main()
