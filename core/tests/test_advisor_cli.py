"""Offline CLI boundary, immutable inputs, and the runnable synthetic demonstration."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from canopy.advisor_cli import main, read_comparison_report
from canopy.advisor_contracts import AdvisorReport
from canopy.contracts import HandlerResult, Step, Task, Usage
from canopy.handlers import HandlerRegistry

ROOT = Path(__file__).parents[2]


def _load_example(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "core" / "examples" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def comparison_report():
    task = Task(
        task_id="cli-task", tenant_id="cli-tenant", workflow_id="triage",
        steps=[Step(
            step_id="classify", kind="model", handler="fixture.classify",
            inputs={"prompt": "Choose a label", "choices": ["low", "high"]},
        )],
        metadata={"advisor": {"steps": {"classify": {
            "category": "business_decision", "choices": ["low", "high"], "output_field": "label",
            "evidence_kind": "text",
        }}}},
    )
    registry = HandlerRegistry()
    registry.register(
        "fixture.classify", lambda _context: HandlerResult(
            output={"label": "low"}, usage=Usage(input_tokens=0, output_tokens=0),
        ), kind="model", synthetic=True, cacheable=True,
    )
    return _load_example("live_comparison").run_comparison(
        task, registry, model_fingerprint="fixture-v1",
    )


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "canopy.advisor_cli", *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
    )


def test_cli_imports_complete_comparison_without_modifying_input(tmp_path, comparison_report):
    source = _write(tmp_path / "comparison.json", comparison_report)
    before = source.read_bytes(), source.stat().st_mtime_ns
    destination = tmp_path / "advisor.json"
    result = _cli("--report", source, "--tenant-id", "cli-tenant", "--output", destination)
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr
    report = AdvisorReport.model_validate_json(destination.read_text(encoding="utf-8"))
    assert report.report_type == "canopy.offline_advisor"
    assert report.source == "comparison_report" and report.source_integrity == "validated_import"
    assert report.analysis.model_calls == report.analysis.tool_calls == 0
    assert report.analysis.input_tokens == report.analysis.output_tokens == 0
    assert report.analysis.scanned_runs == 4
    assert report.recommendations
    assert any("authenticate" in note for note in report.limitations)
    assert before == (source.read_bytes(), source.stat().st_mtime_ns)


def test_cli_can_emit_stdout_and_map_limits(tmp_path, comparison_report):
    source = _write(tmp_path / "comparison.json", comparison_report)
    result = _cli(
        "--report", source, "--tenant-id", "cli-tenant", "--max-runs", 2,
        "--max-groups", 1, "--max-samples", 1, "--max-steps", 1,
        "--max-step-chars", 2048, "--seconds", 10, "--rank-by", "elapsed_ms",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["limits"] == {
        "max_runs": 2, "max_groups": 1, "max_samples_per_group": 1,
        "max_steps_per_run": 1, "max_step_chars": 2048, "max_seconds": 10,
        "rank_by": "elapsed_ms",
    }
    assert report["analysis"]["scanned_runs"] == 2
    assert report["analysis"]["limit_reasons"]


def test_cli_elapsed_includes_import_setup_before_output_serialization(
    tmp_path, comparison_report, monkeypatch, capsys,
):
    import canopy.advisor_cli as cli

    source = _write(tmp_path / "comparison.json", comparison_report)
    clock = iter([10.0, 12.5])
    monkeypatch.setattr(cli, "perf_counter", lambda: next(clock))
    assert main(["--report", str(source), "--tenant-id", "cli-tenant"]) == 0
    analysis = json.loads(capsys.readouterr().out)["analysis"]
    assert analysis["elapsed_ms"] == 2500
    assert analysis["cost_usd"] is None and analysis["energy_j"] is None


@pytest.mark.parametrize("mutation", [
    "tenant", "run_tenant", "run_task", "run_mode", "duplicate_run", "unknown_step",
    "missing_configuration", "incomplete_configuration", "incomplete_policy", "incomplete_usage",
    "incomplete_step", "policy", "output", "evidence", "baseline_reuse", "replay_source",
])
def test_comparison_import_rejects_inconsistent_bindings(
    tmp_path, comparison_report, mutation, capsys,
):
    value = deepcopy(comparison_report)
    run = value["runs"]["baseline_first"]
    if mutation == "tenant":
        value["task"]["tenant_id"] = "SECRET-SENTINEL"
    elif mutation == "run_tenant":
        run["tenant_id"] = "SECRET-SENTINEL"
    elif mutation == "run_task":
        run["task_id"] = "SECRET-SENTINEL"
    elif mutation == "run_mode":
        run["mode"] = "governed"
    elif mutation == "duplicate_run":
        run["run_id"] = value["runs"]["baseline_repeat"]["run_id"]
    elif mutation == "unknown_step":
        run["steps"][0]["step_id"] = "SECRET-SENTINEL"
    elif mutation == "missing_configuration":
        del value["configuration"]
    elif mutation == "incomplete_configuration":
        del value["configuration"]["baseline"]["budget"]
    elif mutation == "incomplete_policy":
        value["configuration"]["baseline"]["policy"] = {}
    elif mutation == "incomplete_usage":
        run["steps"][0]["usage"] = {}
    elif mutation == "incomplete_step":
        del value["task"]["steps"][0]["handler_version"]
    elif mutation == "policy":
        run["policy_version"] = "SECRET-SENTINEL"
    elif mutation == "output":
        run["output"] = {}
    elif mutation == "evidence":
        run["evidence_versions"] = {"SECRET-SENTINEL": "1"}
    elif mutation == "baseline_reuse":
        run["steps"][0]["status"] = "reused"
        run["steps"][0]["source_run_id"] = "original"
        run["steps"][0]["usage"] = Usage(input_tokens=0, output_tokens=0).model_dump(mode="json")
        run["reused_steps"] = 1
    elif mutation == "replay_source":
        run["replay_source_run_id"] = "original"
    source = _write(tmp_path / "comparison.json", value)
    assert main(["--report", str(source), "--tenant-id", "cli-tenant"]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "SECRET-SENTINEL" not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("encoded", [
    '{"schema_version":"1", "schema_version":"1"}',
    '{"schema_version":"1", "irrelevant":NaN}',
    '{"schema_version":"1", "irrelevant":1e9999}',
    '{"synthetic":true,"runs":{}}',
])
def test_strict_import_rejects_unsupported_or_non_json_reports(tmp_path, encoded):
    path = tmp_path / "invalid.json"
    path.write_text(encoded, encoding="utf-8")
    with pytest.raises(ValueError):
        read_comparison_report(path, tenant_id="cli-tenant")


def test_import_size_limit_is_enforced_before_parsing(tmp_path, monkeypatch):
    import canopy.advisor_cli as cli

    monkeypatch.setattr(cli, "MAX_REPORT_BYTES", 32)
    path = tmp_path / "too-big.json"
    path.write_text(" " * 33, encoding="utf-8")
    with pytest.raises(ValueError, match="size limit"):
        read_comparison_report(path, tenant_id="cli-tenant")


def test_cli_never_overwrites_input_or_existing_output(tmp_path, comparison_report):
    source = _write(tmp_path / "comparison.json", comparison_report)
    existing = tmp_path / "existing.json"
    existing.write_text("keep this", encoding="utf-8")
    for output in (source, existing):
        before = output.read_bytes()
        result = _cli("--report", source, "--tenant-id", "cli-tenant", "--output", output)
        assert result.returncode == 2
        assert output.read_bytes() == before
    assert not list(tmp_path.glob(".canopy-advisor-*.tmp"))


def test_missing_recording_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    result = _cli("--db", path, "--tenant-id", "cli-tenant")
    assert result.returncode == 2
    assert not path.exists() and not Path(f"{path}.pollard.sqlite3").exists()


def test_demo_and_db_cli_cover_categories_and_preserve_recordings(tmp_path):
    demo = _load_example("advisor_demo")
    manifest = demo.run_demo(tmp_path / "demo")
    directory = Path(manifest["artifacts_directory"])
    report = AdvisorReport.model_validate_json(
        Path(manifest["advisor_report_path"]).read_text(encoding="utf-8")
    )
    assert manifest["synthetic"] and (directory / "demo.json").is_file()
    assert report.source == "recording_store" and report.source_integrity == "verified_recordings"
    assert {item.finding.category for item in report.recommendations} >= {
        "business_decision", "tool_selection", "next_step",
    }
    assert len(report.recommendations) == 7
    by_step = {item.step_id: item for item in report.recommendations}
    native = by_step["review_operations"]
    assert native.finding.status == "candidate"
    assert native.finding.strategy == "evaluate_openjev"
    assert native.finding.native_compatibility == "native_request_shape"
    assert native.measured_quality is None and native.estimated_savings_usd is None
    assert by_step["classify_ticket"].finding.status == "needs_mapping"
    assert by_step["check_threshold"].finding.strategy == "deterministic_function"
    assert by_step["write_response"].finding.status == "unsupported"
    assert all(item.evaluation_status == "not_evaluated" for item in report.recommendations)
    assert all(item.observed.input_tokens == item.observed.output_tokens == 0
               for item in report.recommendations)
    assert all(item.observed.synthetic_observations == 4 for item in report.recommendations)
    assert all(item.observed.model_calls == 3 for item in report.recommendations)
    assert all(item.observed.reused == 1 for item in report.recommendations)
    # Reuse has no per-step duration measurement, so a total including that
    # observation stays unknown; the originating run durations are measured.
    assert all(item.observed.elapsed_ms is None for item in report.recommendations)
    assert all(item.observed.unknown_elapsed_observations == 1 for item in report.recommendations)
    assert all(run["usage"]["elapsed_known"] and run["usage"]["elapsed_ms"] >= 0
               for run in manifest["runs"].values())
    assert manifest["runs"]["governed_warm"]["usage"]["model_calls"] == 0
    db = Path(manifest["recording_path"])
    paths = [db, Path(f"{db}.pollard.sqlite3")]
    before = {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
              for path in paths}
    result = _cli("--db", db, "--tenant-id", demo.TENANT_ID)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["analysis"]["scanned_runs"] == 4
    assert before == {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in paths
    }
    isolated = _cli("--db", db, "--tenant-id", "different-tenant")
    assert isolated.returncode == 0
    assert json.loads(isolated.stdout)["recommendations"] == []
    collision = _cli("--db", db, "--tenant-id", demo.TENANT_ID, "--output", paths[1])
    assert collision.returncode == 2


def test_demo_always_uses_a_fresh_child_directory(tmp_path):
    demo = _load_example("advisor_demo")
    first = demo.run_demo(tmp_path)
    second = demo.run_demo(tmp_path)
    assert first["artifacts_directory"] != second["artifacts_directory"]
    assert Path(first["advisor_report_path"]).is_file()
    assert Path(second["advisor_report_path"]).is_file()
