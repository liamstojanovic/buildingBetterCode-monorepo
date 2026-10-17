"""Analyze existing recordings without executing models, tools, or workflows.

Use --db for a tenant-scoped recording store, or --report for one complete
live_comparison.py report. --output creates a new JSON file and refuses to
overwrite an existing file. Without --output, the advisor report goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from time import perf_counter
from typing import Any

from .advisor_contracts import AdvisorLimits, AdvisorReport
from .contracts import BudgetLimits, Evidence, Policy, RunConfig, RunResult, Step, StepResult, Task, Usage
from .recording import ExecutionStore, RecordedRun, canonical_json
from .temporal import validate_task

MAX_REPORT_BYTES = 32 * 1024 * 1024
_LABEL_MODES = {
    "baseline_first": "baseline",
    "baseline_repeat": "baseline",
    "governed_cold": "governed",
    "governed_warm": "governed",
}


def _reject_constant(_value: str) -> None:
    raise ValueError("JSON must contain finite numbers")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON must contain finite numbers")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON must not contain duplicate keys")
        value[key] = item
    return value


def _complete_contract(model: type, value: Any):
    # Exports contain the complete effective configuration; do not invent a
    # configuration or measurements by applying defaults to incomplete imports.
    if not isinstance(value, dict) or not set(model.model_fields) <= value.keys():
        raise ValueError("comparison report contains an incomplete contract")
    if model is Task:
        for step in value["steps"]:
            _complete_contract(Step, step)
        for evidence in value["evidence"]:
            _complete_contract(Evidence, evidence)
    elif model is Step:
        _complete_contract(Usage, value["estimated_usage"])
    elif model is RunConfig:
        _complete_contract(Policy, value["policy"])
        _complete_contract(BudgetLimits, value["budget"])
    elif model is RunResult:
        _complete_contract(Usage, value["usage"])
        for result in value["steps"]:
            _complete_contract(StepResult, result)
    elif model is StepResult:
        _complete_contract(Usage, value["usage"])
    return model.model_validate(value)


def read_comparison_report(path: Path, *, tenant_id: str) -> list[RecordedRun]:
    """Validate an exported comparison's bindings, not its claimed authenticity.

    Imported JSON has no Pollard integrity proof. Its measurements remain
    caller-provided data even after structural consistency has been checked.
    """
    if not path.is_file():
        raise ValueError("comparison report must be a file")
    with path.open("rb") as source:
        encoded = source.read(MAX_REPORT_BYTES + 1)
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("comparison report exceeds the size limit")
    data = json.loads(
        encoded.decode("utf-8"), object_pairs_hook=_unique_object,
        parse_constant=_reject_constant, parse_float=_finite_float,
    )
    if not isinstance(data, dict) or data.get("schema_version") != "1":
        raise ValueError("unsupported comparison report")
    task = _complete_contract(Task, data.get("task"))
    validate_task(task)
    if task.tenant_id != tenant_id:
        raise ValueError("comparison report tenant does not match")
    configuration = data.get("configuration")
    if not isinstance(configuration, dict) or set(configuration) != {"baseline", "governed"}:
        raise ValueError("comparison report requires both effective configurations")
    configs = {
        mode: _complete_contract(RunConfig, raw) for mode, raw in configuration.items()
    }
    for mode, config in configs.items():
        if config.mode != mode or config.replay_run_id is not None:
            raise ValueError("comparison report mode does not match its configuration")
        if config.model_fingerprint != data.get("model_fingerprint"):
            raise ValueError("comparison report model does not match its configuration")
    if configs["baseline"].cache_enabled:
        raise ValueError("comparison baseline must disable caching")
    if configs["baseline"].model_dump(exclude={"mode", "cache_enabled"}) != (
        configs["governed"].model_dump(exclude={"mode", "cache_enabled"})
    ):
        raise ValueError("comparison configurations differ beyond execution mode and caching")
    runs = data.get("runs")
    if not isinstance(runs, dict) or set(runs) != set(_LABEL_MODES):
        raise ValueError("comparison report requires its four execution results")
    if data.get("execution_order") != list(_LABEL_MODES):
        raise ValueError("comparison execution order does not match")
    step_ids = {step.step_id for step in task.steps}
    evidence_versions = {item.evidence_id: item.version for item in task.evidence}
    recorded: list[RecordedRun] = []
    seen: set[str] = set()
    for label, mode in _LABEL_MODES.items():
        result = _complete_contract(RunResult, runs[label])
        config = configs[mode]
        if not result.run_id or result.run_id in seen:
            raise ValueError("comparison report run identities must be distinct")
        seen.add(result.run_id)
        if (result.tenant_id, result.task_id, result.mode, result.policy_version) != (
            task.tenant_id, task.task_id, mode, config.policy.version,
        ):
            raise ValueError("comparison result does not match its task or configuration")
        if result.replay_source_run_id is not None:
            raise ValueError("live comparison results must not reference replay sources")
        if result.evidence_versions != evidence_versions or result.finished_at < result.started_at:
            raise ValueError("comparison result has inconsistent evidence or timestamps")
        result_ids = [step.step_id for step in result.steps]
        if len(set(result_ids)) != len(result_ids) or not set(result_ids) <= step_ids:
            raise ValueError("comparison result steps do not match the task")
        if mode == "baseline" and (
            result.reused_steps or any(step.status == "reused" for step in result.steps)
        ):
            raise ValueError("uncached comparison baseline must not contain reused steps")
        if result.status == "completed" and (
            set(result_ids) != step_ids
            or any(step.status not in {"completed", "reused"} for step in result.steps)
        ):
            raise ValueError("completed comparison result requires all steps to succeed")
        expected_output = {
            step.step_id: step.output for step in result.steps
            if step.status in {"completed", "reused"}
        }
        if canonical_json(result.output) != canonical_json(expected_output):
            raise ValueError("comparison output does not match its step results")
        if result.reused_steps != sum(step.status == "reused" for step in result.steps):
            raise ValueError("comparison reuse count does not match its step results")
        recorded.append(RecordedRun(
            run_id=result.run_id, task=task.model_copy(deep=True),
            config=config.model_copy(deep=True), started_at=result.started_at,
            result=result, steps=result.steps, pollard_root_id="", pollard_head_id="",
        ))
    return recorded


def write_report_new(path: Path, report: AdvisorReport) -> None:
    """Publish a complete report atomically without replacing any existing path."""
    payload = report.model_dump_json(indent=2) + "\n"
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".canopy-advisor-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Linking a complete sibling file gives atomic visibility and fails if
        # the destination already exists, including a concurrent writer's file.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", type=Path, help="Existing Canopy recording SQLite database")
    source.add_argument("--report", type=Path, help="One complete live comparison JSON report")
    parser.add_argument("--tenant-id", required=True, help="Explicit tenant scope")
    parser.add_argument("--output", type=Path, help="New report file; never overwrites existing files")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--max-groups", type=int)
    parser.add_argument("--max-samples-per-group", "--max-samples", type=int)
    parser.add_argument("--max-steps-per-run", "--max-steps", type=int)
    parser.add_argument("--max-step-chars", type=int)
    parser.add_argument("--max-seconds", "--seconds", type=float)
    parser.add_argument("--rank-by", choices=["model_calls", "elapsed_ms", "cost_usd"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.tenant_id.strip():
            raise ValueError("tenant scope must be nonempty")
        limits = AdvisorLimits(**{
            name: getattr(args, name) for name in AdvisorLimits.model_fields
            if getattr(args, name) is not None
        })
        protected = (
            [args.db, Path(f"{args.db}.pollard.sqlite3")] if args.db else [args.report]
        )
        if args.output and (
            args.output.exists()
            or args.output.resolve() in {path.resolve() for path in protected}
        ):
            raise ValueError("output must be a new path distinct from input artifacts")
        started = perf_counter()
        # Imported here so --help needs neither an analysis nor any providers.
        from .advisor import analyze_runs, analyze_store

        if args.db:
            with ExecutionStore(args.db, read_only=True) as store:
                report = analyze_store(store, tenant_id=args.tenant_id, limits=limits)
        else:
            runs = read_comparison_report(args.report, tenant_id=args.tenant_id)
            report = analyze_runs(runs, tenant_id=args.tenant_id, limits=limits)
            report = report.model_copy(update={
                "source": "comparison_report", "source_integrity": "validated_import",
                "limitations": [*report.limitations,
                    "Imported measurements are caller-provided; structural validation does "
                    "not authenticate the report or establish Pollard recording integrity."],
            })
        # Include source opening/import and verification as CLI overhead. The
        # cooperative max_seconds limit applies inside analysis; JSON import
        # has a separate byte cap. Serialization and publication are excluded.
        report.analysis.elapsed_ms = (perf_counter() - started) * 1000
        if args.output:
            write_report_new(args.output, report)
        else:
            print(report.model_dump_json(indent=2))
        return 0
    except Exception:
        # File paths, validation input fragments, prompts, and provider outputs
        # must not be echoed in terminal error messages or tracebacks.
        print(
            "Advisor input or output is invalid. Check tenant scope, source integrity, "
            "report format, limits, and that the output path is new.", file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
