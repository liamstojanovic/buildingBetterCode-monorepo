"""Portable export tests use local fixture handlers, never real provider requests."""

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from canopy.advisor_cli import read_comparison_report
from canopy.advisor_contracts import AdvisorReport
from canopy.contracts import HandlerResult, Step, Task, Usage
from canopy.handlers import HandlerRegistry

ROOT = Path(__file__).parents[2]


def _example(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "core" / "examples" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def exporter():
    return _example("export_advisor_live_demo")


@pytest.fixture
def source(tmp_path):
    return _source(tmp_path)


def _source(directory, *, tenant="demo-tenant", provider="openai", category="tool_selection",
            two_steps=False):
    fields = {"business_decision": "label", "tool_selection": "tool", "next_step": "next_step"}
    field = fields[category]
    task = Task(
        task_id=f"test-{category}", tenant_id=tenant, workflow_id=f"fixture-{category}",
        steps=[Step(
            step_id="choose", kind="model", handler="fixture.model",
            inputs={"prompt": "Choose the bounded action for invented fixture data.",
                    "choices": ["lookup", "clarify"]},
        )],
        metadata={
            "sample_data": True,
            "test_provenance": "Local fake handler exercises export; no real provider was called.",
            "advisor": {"steps": {"choose": {
                "category": category, "choices": ["lookup", "clarify"], "output_field": field,
                "evidence_kind": "text", "argument_source": "existing",
            }}},
        },
    )
    if two_steps:
        # Execution follows dependencies; persisted snapshots retain task order.
        task.steps.append(task.steps[0].model_copy(update={"step_id": "another"}, deep=True))
        task.steps[0].depends_on = ["another"]
        task.metadata["advisor"]["steps"]["another"] = deepcopy(
            task.metadata["advisor"]["steps"]["choose"],
        )
    calls = []

    def fixture(_context):
        calls.append(1)
        return HandlerResult(
            output={"provider": provider, "model": "test-only-fixture",
                    "response_id": f"fixture-response-{len(calls)}",
                    "provider_usage": {"input_tokens": 12, "output_tokens": 4},
                    "text": json.dumps({field: "lookup"})},
            usage=Usage(input_tokens=12, output_tokens=4, model_calls=1),
        )

    registry = HandlerRegistry()
    # Explicitly non-synthetic tests the gate without claiming a network call.
    registry.register("fixture.model", fixture, kind="model", cacheable=True, synthetic=False)
    report = _example("live_comparison").run_comparison(
        task, registry, model_fingerprint=f"test-only-{provider}", output_dir=directory,
        provider_configuration={
            "provider": provider, "model": "test-only-fixture", "max_output_tokens": 100,
            "api_key_env": "PRIVATE_ENV_NAME", "worker_python": "C:/private/python.exe",
            "model_cache_dir": "C:/private/cache", "base_url": "https://private.example/api",
            "api_key": "SECRET-SENTINEL", "unknown": {"argv": "SECRET-SENTINEL"},
        },
    )
    path = Path(report["artifacts_directory"]) / "report.json"
    return path, report, calls


def _files(directory):
    # SQLite may create transient WAL/SHM coordination files even in read-only
    # mode. The report, application databases and Pollard databases are immutable.
    return {path.name: (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in directory.iterdir()
            if path.is_file() and path.suffix in {".json", ".sqlite3"}}


def test_export_preserves_verified_recordings_and_frontend_measurements(tmp_path, source, exporter):
    path, original, calls = source
    before = _files(path.parent)
    output = tmp_path / "portable"
    manifest = exporter.export_demo([path], output)
    assert len(calls) == 3  # Export did not execute a fourth fixture/model request.
    assert _files(path.parent) == before
    assert manifest["counts"] == {
        "runs": 4, "step_observations": 4, "model_calls": 3, "reused_steps": 1,
    }
    assert manifest["export"]["model_calls"] == manifest["export"]["tool_calls"] == 0
    assert manifest["export"]["original_recordings_verified"] is True
    entry = manifest["sources"][0]
    assert entry["label"] == "openai-tool-selection"
    assert entry["provider"] == "openai" and entry["category"] == "tool_selection"
    portable_path = output / entry["files"]["comparison"]
    portable = json.loads(portable_path.read_text(encoding="utf-8"))
    for field in ("task", "configuration", "runs", "summary", "model_fingerprint"):
        assert portable[field] == original[field]
    assert portable["provider_configuration"] == {
        "provider": "openai", "model": "test-only-fixture", "max_output_tokens": 100,
    }
    assert len(read_comparison_report(portable_path, tenant_id="demo-tenant")) == 4
    bundle = json.loads((output / entry["files"]["frontend_comparison"]).read_text(encoding="utf-8"))
    assert bundle["format"] == "canopy-comparison/v1"
    assert bundle["evaluations"] == [] and bundle["benchmark"]["repetitions"] == 1
    assert set(bundle["recordings"]) == {"baseline_cold", "baseline", "cold", "warm"}
    assert bundle["recordings"]["baseline"]["result"] == original["runs"]["baseline_repeat"]
    assert bundle["recordings"]["warm"]["result"] == original["runs"]["governed_warm"]
    for recording in bundle["recordings"].values():
        assert recording["task"] == original["task"]
        assert recording["events"]
        assert all(event["run_id"] == recording["result"]["run_id"]
                   for event in recording["events"])
        original_result = next(run for run in original["runs"].values()
                               if run["run_id"] == recording["result"]["run_id"])
        assert recording["result"]["usage"] == original_result["usage"]
    assert bundle["recordings"]["warm"]["result"]["usage"]["model_calls"] == 0
    assert bundle["benchmark"]["cases"]["warm"]["trials"][0]["baseline_cost_usd"] is None
    assert bundle["benchmark"]["cases"]["warm"]["trials"][0]["governed_cost_usd"] == 0
    advisor = AdvisorReport.model_validate_json(
        (output / manifest["combined_advisor"]).read_text(encoding="utf-8"),
    )
    assert advisor.analysis.scanned_runs == 4 and advisor.analysis.model_calls == 0
    assert advisor.source_integrity == "validated_import" and advisor.source == "comparison_report"
    assert advisor.recommendations[0].finding.category == "tool_selection"
    assert advisor.recommendations[0].evaluation_status == "not_evaluated"
    assert any("original read-only" in note for note in advisor.limitations)
    for relative, information in manifest["files"].items():
        content = (output / relative).read_bytes()
        assert not Path(relative).is_absolute()
        assert information == {"sha256": sha256(content).hexdigest(), "bytes": len(content)}
    all_content = "\n".join(file.read_text(encoding="utf-8") for file in output.rglob("*.json"))
    for private in ("SECRET-SENTINEL", "PRIVATE_ENV_NAME", "C:/private", "private.example",
                    original["artifacts_directory"], json.dumps(original["artifacts_directory"]),
                    '"artifacts_directory":'):
        assert private not in all_content


def test_export_combines_categories_providers_and_deduplicates_runs(tmp_path, source, exporter):
    path, _, _ = source
    other, _, _ = _source(tmp_path / "second", provider="gemini", category="next_step")
    business, _, _ = _source(tmp_path / "third", provider="anthropic", category="business_decision")
    output = tmp_path / "combined"
    manifest = exporter.export_demo([path, other, business, path], output)
    assert len(manifest["sources"]) == 4
    assert manifest["counts"]["runs"] == 12 and manifest["counts"]["model_calls"] == 9
    assert len({source["label"] for source in manifest["sources"]}) == 4
    advisor = AdvisorReport.model_validate_json((output / "advisor-report.json").read_text())
    assert advisor.analysis.scanned_runs == 12
    assert {item.finding.category for item in advisor.recommendations} == {
        "business_decision", "tool_selection", "next_step",
    }


def test_export_binds_step_snapshots_independently_of_execution_order(tmp_path, exporter):
    path, original, calls = _source(tmp_path / "source", two_steps=True)
    assert original["task"]["steps"][0]["step_id"] == "choose"
    assert original["runs"]["baseline_first"]["steps"][0]["step_id"] == "another"
    manifest = exporter.export_demo([path], tmp_path / "portable")
    assert manifest["counts"]["step_observations"] == 8
    assert manifest["counts"]["model_calls"] == len(calls) == 6


@pytest.mark.parametrize("mutation", ["sample", "synthetic", "task", "config", "provider"])
def test_rejects_invalid_or_unbound_sources_before_output(tmp_path, source, exporter, mutation):
    path, report, _ = source
    value = deepcopy(report)
    if mutation == "sample":
        value["sample_data"] = False
    elif mutation == "synthetic":
        value["runs"]["baseline_first"]["synthetic"] = True
    elif mutation == "task":
        value["task"]["steps"][0]["inputs"]["prompt"] = "Changed after recording"
    elif mutation == "config":
        for config in value["configuration"].values():
            config["actor_id"] = "different-operator"
    elif mutation == "provider":
        for run in value["runs"].values():
            run["steps"][0]["output"]["provider"] = "unknown"
            run["output"]["choose"]["provider"] = "unknown"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        exporter.export_demo([path], tmp_path / "refused")
    assert not (tmp_path / "refused").exists()


def test_rejects_tenant_mixing_and_does_not_replace_destination(tmp_path, source, exporter):
    path, _, _ = source
    other, _, _ = _source(tmp_path / "other", tenant="another-tenant")
    with pytest.raises(ValueError, match="one explicit tenant"):
        exporter.export_demo([path, other], tmp_path / "mixed")
    assert not (tmp_path / "mixed").exists()
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    with pytest.raises(ValueError, match="new output directory"):
        exporter.export_demo([path], destination)
    assert sentinel.read_text() == "unchanged"


def test_rejects_pollard_tampering_and_missing_sources_read_only(tmp_path, source, exporter):
    path, _, _ = source
    with sqlite3.connect(path.parent / "baseline.sqlite3") as connection:
        connection.execute("UPDATE canopy_runs SET head_id = ?", ("tampered-head",))
    before = _files(path.parent)
    with pytest.raises(ValueError, match="integrity"):
        exporter.export_demo([path], tmp_path / "tampered")
    assert _files(path.parent) == before and not (tmp_path / "tampered").exists()
    missing_report = tmp_path / "missing" / "report.json"
    missing_report.parent.mkdir()
    missing_report.write_bytes(path.read_bytes())
    with pytest.raises(FileNotFoundError):
        exporter.export_demo([missing_report], tmp_path / "missing-output")
    assert list(missing_report.parent.iterdir()) == [missing_report]


def test_cli_errors_do_not_echo_source_content_or_paths(tmp_path, exporter, capsys):
    source = tmp_path / "SECRET-SENTINEL.json"
    source.write_text('{"task": {"tenant_id": "SECRET-SENTINEL"}}', encoding="utf-8")
    assert exporter.main([str(source), "--output-dir", str(tmp_path / "out")]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "SECRET-SENTINEL" not in captured.err
    assert "Traceback" not in captured.err
