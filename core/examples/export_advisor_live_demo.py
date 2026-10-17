"""Export invented-data provider recordings for offline advisor/frontend playback.

Inputs are complete live_comparison report.json files with their original
baseline.sqlite3, governed.sqlite3 and Pollard sidecars beside them. This reads
and verifies existing recordings; it never executes a workflow or a model.
The destination must not exist. Fixture authors must use invented content:
sample_data is an explicit assertion, not a general-purpose secret scrubber.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from canopy.advisor import analyze_runs
from canopy.advisor_cli import MAX_REPORT_BYTES, read_comparison_report
from canopy.advisor_contracts import AdvisorReport
from canopy.contracts import utcnow
from canopy.recording import ExecutionStore, RecordedRun, canonical_json

_LABELS = (
    ("baseline_first", "baseline_cold", "baseline", "cold"),
    ("baseline_repeat", "baseline", "baseline", "warm"),
    ("governed_cold", "cold", "governed", "cold"),
    ("governed_warm", "warm", "governed", "warm"),
)
_REPORT_FIELDS = {
    "schema_version", "task", "sample_data", "model_fingerprint", "execution_order",
    "configuration", "summary", "runs", "interpretation",
}
_PROVIDER_FIELDS = {
    "provider", "model", "instructions", "max_output_tokens", "temperature",
    "timeout_seconds", "cache_namespace", "device", "max_context_tokens",
}
_PROVIDERS = {"openai", "anthropic", "gemini", "openai-compatible"}
_VERIFICATION_NOTE = (
    "The exporter verified the original read-only SQLite/Pollard recordings and their "
    "task, configuration, result and event bindings. Portable JSON does not contain the "
    "Pollard proof and remains a structurally validated import."
)


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_advisor(runs: list[RecordedRun], tenant_id: str) -> AdvisorReport:
    report = analyze_runs(runs, tenant_id=tenant_id)
    if report.analysis.limit_reasons or report.analysis.skipped_oversized_steps:
        raise ValueError("demo analysis must include all input observations")
    return report.model_copy(update={
        "source": "comparison_report", "source_integrity": "validated_import",
        "limitations": [*report.limitations, _VERIFICATION_NOTE],
    })


def _read_source(path: Path, tenant_id: str | None) -> dict[str, Any]:
    with path.open("rb") as handle:
        encoded = handle.read(MAX_REPORT_BYTES + 1)
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("comparison report exceeds the size limit")
    # Only obtain the scope here. read_comparison_report subsequently validates
    # strict JSON, complete contracts, finite numbers and all report bindings.
    raw = json.loads(encoded)
    scope = raw["task"]["tenant_id"]
    if not isinstance(scope, str) or not scope.strip() or (tenant_id and scope != tenant_id):
        raise ValueError("all sources must have one explicit tenant")
    runs = read_comparison_report(path, tenant_id=scope)
    if raw.get("sample_data") is not True or runs[0].task.metadata.get("sample_data") is not True:
        raise ValueError("only explicitly invented sample data may be exported")
    if any(run.result is None or run.result.synthetic or run.result.status != "completed"
           for run in runs):
        raise ValueError("sources must contain completed non-synthetic executions")
    if any(step.kind != "model" or step.side_effect for step in runs[0].task.steps):
        raise ValueError("provider decision fixtures must contain side-effect-free model steps")
    providers = {
        step.output.get("provider") if isinstance(step.output, dict) else None
        for run in runs for step in run.steps
    }
    if len(providers) != 1 or not providers <= _PROVIDERS:
        raise ValueError("source outputs must identify one supported hosted provider")
    provider = next(iter(providers))
    paths = [
        path.parent / name for name in (
            "baseline.sqlite3", "baseline.sqlite3.pollard.sqlite3",
            "governed.sqlite3", "governed.sqlite3.pollard.sqlite3",
        )
    ]
    original_hashes = {item.name: _digest(item) for item in paths}
    recordings = {}
    for (_label, alias, store_name, _case), imported in zip(_LABELS, runs, strict=True):
        with ExecutionStore(path.parent / f"{store_name}.sqlite3", read_only=True) as store:
            if not store.verify_run(scope, imported.run_id).ok:
                raise ValueError("original recording integrity verification failed")
            recorded = store.get_run(scope, imported.run_id)
            if any(canonical_json(left) != canonical_json(right) for left, right in (
                (recorded.task, imported.task), (recorded.config, imported.config),
                (recorded.result, imported.result),
                ({step.step_id: step for step in recorded.steps},
                 {step.step_id: step for step in imported.steps}),
                (recorded.started_at, imported.started_at),
            )):
                raise ValueError("original recording does not match the complete report")
            recordings[alias] = {
                "task": recorded.task.model_dump(mode="json"),
                "result": recorded.result.model_dump(mode="json"),
                "events": [event.model_dump(mode="json")
                           for event in store.events(scope, imported.run_id)],
            }
    if original_hashes != {item.name: _digest(item) for item in paths}:
        raise ValueError("source recording changed during export")
    if _digest(path) != sha256(encoded).hexdigest():
        raise ValueError("source report changed during export")
    portable = {key: value for key, value in raw.items() if key in _REPORT_FIELDS}
    configuration = raw.get("provider_configuration")
    if configuration is not None:
        if not isinstance(configuration, dict):
            raise ValueError("provider configuration must be an object")
        portable["provider_configuration"] = {
            key: value for key, value in configuration.items() if key in _PROVIDER_FIELDS
        }
    elif "provider_configuration" in raw:
        portable["provider_configuration"] = None
    advisor = _portable_advisor(runs, scope)
    categories = sorted({item.finding.category for item in advisor.recommendations})
    category = categories[0] if len(categories) == 1 else "mixed"
    return {
        "tenant_id": scope, "provider": provider, "category": category,
        "categories": categories, "runs": runs, "portable": portable,
        "frontend": _frontend_bundle(portable, recordings), "advisor": advisor,
        "original_sha256": {"report.json": sha256(encoded).hexdigest(), **original_hashes},
    }


def _frontend_bundle(report: dict, recordings: dict) -> dict:
    cases = {}
    for case, baseline_label, governed_label in (
        ("cold", "baseline_first", "governed_cold"),
        ("warm", "baseline_repeat", "governed_warm"),
    ):
        baseline, governed = report["runs"][baseline_label], report["runs"][governed_label]
        cases[case] = {
            "baseline": ([baseline["usage"]["elapsed_ms"]]
                         if baseline["usage"]["elapsed_known"] else []),
            "governed": ([governed["usage"]["elapsed_ms"]]
                         if governed["usage"]["elapsed_known"] else []),
            "trials": [{
                "baseline_run_id": baseline["run_id"], "governed_run_id": governed["run_id"],
                "baseline_cost_usd": baseline["usage"]["cost_usd"],
                "governed_cost_usd": governed["usage"]["cost_usd"],
                "completed": True, "synthetic": False,
            }],
        }
    return {
        "format": "canopy-comparison/v1",
        "description": (
            "Recorded hosted-provider execution on invented sample data. Original usage, "
            "elapsed times, outputs and events are preserved; unknown cost stays null. "
            "Playback makes zero provider calls. No independent quality evaluation or "
            "JEV replacement result is included."
        ),
        "recordings": recordings, "evaluations": [],
        "benchmark": {"repetitions": 1, "cases": cases},
    }


def _write_json(path: Path, value: Any) -> dict[str, Any]:
    encoded = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(encoded)
    return {"sha256": sha256(encoded).hexdigest(), "bytes": len(encoded)}


def export_demo(reports: list[Path], output_dir: Path) -> dict[str, Any]:
    """Validate every source before creating a new portable export directory."""
    started = perf_counter()
    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink() or not reports:
        raise ValueError("provide input reports and a new output directory")
    sources, seen, combined = [], {}, []
    tenant_id = None
    for path in reports:
        source = _read_source(Path(path), tenant_id)
        tenant_id = source["tenant_id"]
        sources.append(source)
        for run in source["runs"]:
            content = canonical_json({"task": run.task, "config": run.config, "result": run.result})
            if run.run_id in seen:
                if seen[run.run_id] != content:
                    raise ValueError("duplicate run identities disagree")
            else:
                seen[run.run_id] = content
                combined.append(run)
    combined_advisor = _portable_advisor(combined, tenant_id)
    output_dir.mkdir(parents=True, exist_ok=False)
    files = {}
    entries = []
    labels: set[str] = set()
    for source in sources:
        base_label = f"{source['provider']}-{source['category'].replace('_', '-')}"
        label, suffix = base_label, 2
        while label in labels:
            label, suffix = f"{base_label}-{suffix}", suffix + 1
        labels.add(label)
        directory = output_dir / label
        directory.mkdir()
        relative_files = {}
        for name, value in (
            ("comparison.json", source["portable"]),
            ("frontend-comparison.json", source["frontend"]),
            ("advisor-report.json", source["advisor"].model_dump(mode="json")),
        ):
            relative = f"{label}/{name}"
            files[relative] = _write_json(directory / name, value)
            relative_files[name.removesuffix(".json").replace("-", "_")] = relative
        entries.append({
            "label": label, "provider": source["provider"], "category": source["category"],
            "categories": source["categories"], "files": relative_files,
            "run_ids": {name: run.run_id for (name, *_rest), run
                        in zip(_LABELS, source["runs"], strict=True)},
            "counts": _counts(source["runs"]),
            "original_recordings_verified": True,
            "original_sha256": source["original_sha256"],
        })
    files["advisor-report.json"] = _write_json(
        output_dir / "advisor-report.json", combined_advisor.model_dump(mode="json"),
    )
    manifest = {
        "format": "canopy-advisor-live-demo/v1", "generated_at": utcnow().isoformat(),
        "tenant_id": tenant_id, "sample_data": True, "synthetic": False,
        "sources": entries, "counts": _counts(combined),
        "combined_advisor": "advisor-report.json", "files": files,
        "export": {
            "model_calls": 0, "tool_calls": 0, "elapsed_ms": (perf_counter() - started) * 1000,
            "cost_usd": None, "energy_j": None,
            "original_recordings_verified": True, "verification_note": _VERIFICATION_NOTE,
            "omitted_provenance": [
                "absolute artifacts_directory", "provider endpoint and credential-variable name",
                "provider worker/cache filesystem paths", "unrecognized report metadata",
            ],
        },
        "limitations": [
            "Invented sample data, not production traffic or a representative benchmark.",
            "Non-synthetic marks the recorded handler, not independent provider attestation.",
            "Three uncached/cold executions and one warm reuse per comparison; no quality score.",
            "JEV suitability recommendations are unevaluated; no replacement or savings claim.",
            "File hashes support reproducibility but do not authenticate a portable bundle.",
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def _counts(runs: list[RecordedRun]) -> dict[str, int]:
    return {
        "runs": len(runs), "step_observations": sum(len(run.steps) for run in runs),
        "model_calls": sum(run.result.usage.model_calls for run in runs),
        "reused_steps": sum(run.result.reused_steps for run in runs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; never overwritten")
    args = parser.parse_args(argv)
    try:
        manifest = export_demo(args.reports, args.output_dir)
    except Exception:
        print("Export failed. Check source fixtures, tenant, recording integrity and new output path.",
              file=sys.stderr)
        return 2
    print(f"Exported {len(manifest['sources'])} comparisons and {manifest['counts']['runs']} unique "
          "runs; zero model calls during export.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
