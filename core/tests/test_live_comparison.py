"""Comparison isolation and reporting tests; no credentials or network calls."""

import importlib.util
import json
from pathlib import Path
import sys
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
    assert all("prompt" in step["inputs"] for step in sample["steps"])
    assert all("questions" not in step["inputs"] for step in sample["steps"])


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
        "--provider", "anthropic", "--model", "explicit-model", "--task-file", str(task_file),
        "--api-key-env", "MY_ANTHROPIC_KEY",
    ])
    report = json.loads(capsys.readouterr().out)
    assert result == 0 and closed == [1]
    assert observed == [("anthropic", "explicit-model", "MY_ANTHROPIC_KEY")]
    assert report["sample_data"] is False and report["task"]["task_id"] == "teammate-task"
    assert all(step["handler_version"] == "test-v1" for step in report["task"]["steps"])
    assert json.loads(task_file.read_text())["steps"][0]["handler_version"] == "1"


def test_cli_rejects_unregistered_task_handler():
    task = comparison.sample_task()
    task.steps[0].handler = "not.registered"
    with pytest.raises(ValueError, match="model.generate"):
        comparison.bind_model_version(task, "test-v1")


@pytest.mark.parametrize("provider", ["pollard-jev", "jev"])
def test_native_provider_uses_package_sample_and_evidence_binding(monkeypatch, capsys, provider):
    calls, bindings, closed = [], [], []
    native_task = comparison.sample_task().model_copy(update={"task_id": "package-task"})

    def bind(task, version):
        bindings.append((task.task_id, version))
        return comparison.bind_model_version(task, version)

    native_module = SimpleNamespace(
        pollard_jev_sample_task=lambda: native_task,
        bind_pollard_jev_task=bind,
    )
    monkeypatch.setitem(sys.modules, "canopy.pollard_jev", native_module)
    handler = SimpleNamespace(version="test-v1", fingerprint="test-v1", close=lambda: closed.append(1))
    observed = []

    def registry(args):
        observed.append((args.worker_python, args.model_cache_dir, args.device, args.max_context_tokens))
        return fake_registry(calls), handler

    monkeypatch.setattr(comparison, "_live_registry", registry)
    result = comparison.main([
        "--provider", provider, "--model", "AlexWortega/openjev/qwen3.5-4b-nli-v2",
        "--worker-python", "worker/python.exe", "--model-cache-dir", "local-weights",
        "--device", "cpu", "--max-context-tokens", "2048",
    ])
    report = json.loads(capsys.readouterr().out)
    assert result == 0 and closed == [1]
    assert bindings == [("package-task", "test-v1")]
    assert observed == [("worker/python.exe", "local-weights", "cpu", 2048)]
    assert report["task"]["task_id"] == "package-task"
    assert len(calls) == 3


def test_native_task_file_uses_package_binder_instead_of_regenerating(monkeypatch, tmp_path, capsys):
    bindings = []
    task = comparison.sample_task().model_copy(update={"task_id": "native-file-task"})
    path = tmp_path / "native.json"
    path.write_text(task.model_dump_json(), encoding="utf-8")

    def no_sample():
        raise AssertionError("A supplied task file must not be replaced by a sample")

    def bind(task, version):
        bindings.append(task.task_id)
        return comparison.bind_model_version(task, version)

    monkeypatch.setitem(sys.modules, "canopy.pollard_jev", SimpleNamespace(
        pollard_jev_sample_task=no_sample, bind_pollard_jev_task=bind,
    ))
    handler = SimpleNamespace(version="test-v1", fingerprint="test-v1", close=lambda: None)
    monkeypatch.setattr(comparison, "_live_registry", lambda args: (fake_registry([]), handler))
    assert comparison.main([
        "--provider", "pollard-jev", "--model", "AlexWortega/openjev/qwen3.5-4b-nli-v2",
        "--task-file", str(path),
    ]) == 0
    capsys.readouterr()
    assert bindings == ["native-file-task"]


def test_native_sample_export_does_not_initialize_model(monkeypatch, tmp_path):
    task = comparison.sample_task().model_copy(update={"task_id": "native-sample"})
    monkeypatch.setitem(sys.modules, "canopy.pollard_jev", SimpleNamespace(
        pollard_jev_sample_task=lambda: task,
    ))

    def no_provider(args):
        raise AssertionError("Sample export must not initialize any model")

    monkeypatch.setattr(comparison, "_live_registry", no_provider)
    path = tmp_path / "native-sample.json"
    assert comparison.main([
        "--provider", "pollard-jev", "--write-sample-task", str(path),
    ]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["task_id"] == "native-sample"


def test_native_worker_configuration_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("CANOPY_POLLARD_JEV_PYTHON", "test-worker.exe")
    monkeypatch.setenv("CANOPY_OPENJEV_CACHE", "test-model-cache")
    args = comparison.build_parser().parse_args(["--provider", "jev"])
    assert args.worker_python == "test-worker.exe"
    assert args.model_cache_dir == "test-model-cache"
    assert args.max_context_tokens == 4096


def test_real_native_helpers_bind_sample_without_loading_weights(monkeypatch):
    monkeypatch.delenv("CANOPY_BASE_URL", raising=False)
    args = comparison.build_parser().parse_args([
        "--provider", "pollard-jev", "--model", "AlexWortega/openjev/qwen3.5-4b-nli-v2",
    ])
    task = comparison._sample_for_provider(args.provider)
    registry, handler = comparison._live_registry(args)
    try:
        bound = comparison._bind_for_provider(task, handler.version, args.provider)
        step = bound.steps[0]
        request = step.inputs["decision_request"]
        evidence = next(item for item in bound.evidence if item.evidence_id in step.evidence_ids)
        assert registry.resolve(step.handler, step.handler_version).kind == "model"
        assert "observations" in request and "choices" in request
        assert "questions" not in step.inputs
        assert evidence.data["decision_request"] == request
        assert evidence.observed_at < evidence.expires_at
        assert task.evidence == []
    finally:
        handler.close()
