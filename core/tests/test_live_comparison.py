"""Comparison isolation and reporting tests; no credentials or network calls."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from canopy.contracts import HandlerResult, Usage
from canopy.handlers import HandlerRegistry

_spec = importlib.util.spec_from_file_location(
    "canopy_live_comparison", Path(__file__).parents[1] / "examples" / "live_comparison.py"
)
comparison = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(comparison)


def fake_registry(calls, *, usage=None):
    registry = HandlerRegistry()

    def generate(context):
        calls.append((context.step.step_id, context.config.mode))
        return HandlerResult(
            output={"answer": "sample result", "dependencies": context.dependencies},
            usage=usage if usage is not None else Usage(
                input_tokens=12, output_tokens=4, provider_cached_input_tokens=3,
            ),
        )

    registry.register("model.generate", generate, kind="model", version="test-v1", cacheable=True)
    return registry


def test_comparison_isolates_cold_from_baseline_and_reports_full_results():
    calls = []
    task = comparison.bind_model_version(comparison.sample_task(), "test-v1")
    before = task.model_dump(mode="json")
    report = comparison.run_comparison(task, fake_registry(calls), model_fingerprint="test-v1")

    assert report["task"] == before == task.model_dump(mode="json")
    assert report["sample_data"] is True
    assert report["configuration"]["baseline"]["cache_enabled"] is False
    assert report["configuration"]["governed"]["cache_enabled"] is True
    assert report["execution_order"] == [
        "baseline_first", "baseline_repeat", "governed_cold", "governed_warm",
    ]
    assert len(calls) == 3
    assert calls == [
        ("classify_ticket", "baseline"),
        ("classify_ticket", "baseline"),
        ("classify_ticket", "governed"),
    ]
    for label in ("baseline_first", "baseline_repeat", "governed_cold"):
        summary = report["summary"][label]
        assert summary["status"] == "completed"
        assert summary["input_tokens"] == 12 and summary["output_tokens"] == 4
        assert summary["provider_cached_input_tokens"] == 3
        assert summary["model_calls"] == 1 and summary["reused_steps"] == 0
        assert summary["cost_usd"] is None and summary["energy_j"] is None
    warm = report["summary"]["governed_warm"]
    assert warm["model_calls"] == warm["input_tokens"] == warm["output_tokens"] == 0
    assert warm["provider_cached_input_tokens"] == 0 and warm["reused_steps"] == 1
    assert report["runs"]["governed_warm"]["output"] == report["runs"]["governed_cold"]["output"]
    assert all(len(run["steps"]) == 1 for run in report["runs"].values())
    assert "artifacts_directory" not in report


def test_persistent_reports_always_get_new_directories(tmp_path):
    calls = []
    task = comparison.bind_model_version(comparison.sample_task(), "test-v1")
    reports = [
        comparison.run_comparison(
            task, fake_registry(calls), model_fingerprint="test-v1", output_dir=tmp_path,
        )
        for _ in range(2)
    ]
    assert len(calls) == 6
    directories = [Path(report["artifacts_directory"]) for report in reports]
    assert directories[0] != directories[1]
    for directory, report in zip(directories, reports, strict=True):
        assert (directory / "baseline.sqlite3").is_file()
        assert (directory / "governed.sqlite3").is_file()
        assert json.loads((directory / "report.json").read_text(encoding="utf-8")) == report


def test_missing_provider_usage_is_not_reported_as_measured_zero():
    task = comparison.bind_model_version(comparison.sample_task(), "test-v1")
    report = comparison.run_comparison(
        task, fake_registry([], usage=Usage()), model_fingerprint="test-v1",
    )
    for label in ("baseline_first", "baseline_repeat", "governed_cold"):
        assert report["summary"][label]["input_tokens"] is None
        assert report["summary"][label]["output_tokens"] is None
        assert report["summary"][label]["tokens_known"] is False
        assert report["summary"][label]["model_calls"] == 1


def test_budget_refusal_is_visible_and_never_dispatches():
    calls = []
    task = comparison.bind_model_version(comparison.sample_task(), "test-v1")
    report = comparison.run_comparison(
        task, fake_registry(calls), model_fingerprint="test-v1", max_steps=0,
    )
    assert calls == []
    assert all(run["status"] == "blocked" for run in report["runs"].values())
    assert all(summary["model_calls"] == 0 for summary in report["summary"].values())


def test_write_sample_task_needs_no_model_or_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("CANOPY_MODEL", raising=False)

    def no_provider(args):
        raise AssertionError("Provider must not be initialized")

    monkeypatch.setattr(comparison, "_live_registry", no_provider)
    target = tmp_path / "sample.json"
    assert comparison.main(["--write-sample-task", str(target)]) == 0
    sample = json.loads(target.read_text(encoding="utf-8"))
    assert sample["metadata"]["sample_data"] is True
    assert all("questions" in step["inputs"] for step in sample["steps"])


def test_model_must_be_explicit(monkeypatch, capsys):
    monkeypatch.delenv("CANOPY_MODEL", raising=False)
    with pytest.raises(SystemExit) as error:
        comparison.main([])
    assert error.value.code == 2
    assert "supply --model or CANOPY_MODEL" in capsys.readouterr().err


def test_cli_task_file_is_bound_once_and_handler_is_closed(monkeypatch, tmp_path, capsys):
    calls, closed = [], []
    task = comparison.sample_task().model_copy(update={
        "task_id": "teammate-task", "metadata": {"sample_data": False},
    })
    task_file = tmp_path / "task.json"
    task_file.write_text(task.model_dump_json(), encoding="utf-8")
    handler = SimpleNamespace(version="test-v1", fingerprint="test-v1", close=lambda: closed.append(1))
    observed = []

    def registry(args):
        observed.append((args.provider, args.model, args.api_key_env))
        return fake_registry(calls), handler

    monkeypatch.setattr(comparison, "_live_registry", registry)
    result = comparison.main([
        "--provider", "jev", "--model", "explicit-model", "--task-file", str(task_file),
        "--api-key-env", "MY_JEV_KEY",
    ])
    report = json.loads(capsys.readouterr().out)
    assert result == 0 and closed == [1]
    assert observed == [("jev", "explicit-model", "MY_JEV_KEY")]
    assert report["sample_data"] is False and report["task"]["task_id"] == "teammate-task"
    assert all(step["handler_version"] == "test-v1" for step in report["task"]["steps"])
    assert json.loads(task_file.read_text())["steps"][0]["handler_version"] == "1"


def test_cli_rejects_unregistered_task_handler():
    task = comparison.sample_task()
    task.steps[0].handler = "not.registered"
    with pytest.raises(ValueError, match="model.generate"):
        comparison.bind_model_version(task, "test-v1")
