"""Native task/cache boundary and isolated-worker transport regressions."""

from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic

import pytest

from canopy.contracts import RunConfig
from canopy.handlers import HandlerExecutionError, HandlerRegistry
from canopy.models import ModelConfig, ModelRequest, POLLARD_JEV_MODEL, register_model_handler
from canopy.pollard_jev import (
    PollardJevAdapter, bind_pollard_jev_task, pollard_jev_sample_task,
)
from canopy.runtime import create_runtime


def config(**options):
    return ModelConfig("pollard-jev", POLLARD_JEV_MODEL, **options)


def test_jev_alias_selects_package_and_rejects_cloud_configuration():
    assert ModelConfig("jev", POLLARD_JEV_MODEL).provider == "pollard-jev"
    for options in ({"base_url": "https://api.typesafe.ai"}, {"api_key_env": "JEV_API_KEY"},
                    {"temperature": 0.1}, {"max_context_tokens": 0}):
        with pytest.raises(ValueError):
            config(**options)
    with pytest.raises(ValueError, match="pinned model"):
        ModelConfig("pollard-jev", "jev-latest")


def test_binding_preserves_native_request_and_captures_earliest_expiry():
    original = pollard_jev_sample_task()
    native = original.steps[0].inputs["decision_request"]
    native["observations"][1]["valid_until"] = (
        original.model_validate(original.model_dump()).steps[0].inputs["decision_request"]["created_at"]
    )
    with pytest.raises(ValueError, match="validity interval"):
        bind_pollard_jev_task(original, "revision")
    original = pollard_jev_sample_task()
    bound = bind_pollard_jev_task(original, "revision")
    assert not original.evidence and original.steps[0].handler_version == "1"
    assert bound.steps[0].handler_version == "revision"
    assert bound.evidence[0].data["decision_request"] == original.steps[0].inputs["decision_request"]
    assert bound.evidence[0].expires_at > bound.evidence[0].observed_at
    PollardJevAdapter().validate_task(bound, bound.steps[0], RunConfig())


class RecordedWorker(PollardJevAdapter):
    def __init__(self):
        super().__init__()
        self.inferences = 0

    def _exchange(self, payload, config):
        versions = {"pollard-jev": "0.13", "pollard": "1.6.0"}
        if payload["op"] == "inspect":
            return {"ok": True, "protocol_version": 1, "package_versions": versions}
        self.inferences += 1
        return {
            "ok": True, "protocol_version": 1, "package_versions": versions,
            "result": {
                "request_id": payload["request"]["request_id"],
                "identity": {
                    "synthetic": False, "model": POLLARD_JEV_MODEL,
                    "provider": "alexwortega-openjev",
                    "model_version": "552759daad712f1af6c4c13dabcb1e047886fc9c",
                },
                "semantics": "independent_entailment", "scores": {"reorder": 0.9, "wait": 0.1},
            },
            "usage": {"input_tokens": 100, "output_tokens": 0, "model_calls": 1},
            "measurement_basis": "complete_unpadded_pairs",
        }


@pytest.mark.parametrize("change", ["evidence_removed", "expiry_extended", "native_changed"])
def test_native_binding_checked_even_before_warm_reuse(tmp_path, change):
    registry, adapter = HandlerRegistry(), RecordedWorker()
    handler = register_model_handler(registry, config(), cacheable=True, adapter=adapter)
    task = bind_pollard_jev_task(pollard_jev_sample_task(), handler.version)
    run_config = RunConfig(permissions=["execute:*"], model_fingerprint=handler.fingerprint)
    with closing(create_runtime(str(tmp_path / "runs.sqlite3"), registry=registry)) as runtime:
        cold = runtime.run_task(task, run_config)
        warm = runtime.run_task(task, run_config)
        assert cold.status == warm.status == "completed" and warm.reused_steps == 1
        assert adapter.inferences == 1 and cold.usage.input_tokens == 100
        changed = task.model_copy(deep=True)
        if change == "evidence_removed":
            changed.steps[0].evidence_ids.clear()
        elif change == "expiry_extended":
            changed.evidence[0].expires_at += timedelta(days=1)
        else:
            changed.steps[0].inputs["decision_request"]["observations"][0]["value"] = 1.0
        with pytest.raises(ValueError, match="native-validity"):
            runtime.run_task(changed, run_config)
        assert adapter.inferences == 1


def test_expired_native_request_blocks_cache_reuse(tmp_path):
    registry, adapter = HandlerRegistry(), RecordedWorker()
    handler = register_model_handler(registry, config(), cacheable=True, adapter=adapter)
    task = pollard_jev_sample_task()
    native = task.steps[0].inputs["decision_request"]
    from datetime import datetime
    for obj in [native, *native["observations"]]:
        start_key = "created_at" if obj is native else "observed_at"
        for key in (start_key, "valid_until"):
            obj[key] = (datetime.fromisoformat(obj[key]) - timedelta(days=1)).isoformat()
    task = bind_pollard_jev_task(task, handler.version)
    with closing(create_runtime(str(tmp_path / "runs.sqlite3"), registry=registry)) as runtime:
        result = runtime.run_task(task, RunConfig(permissions=["execute:*"],
                                                  model_fingerprint=handler.fingerprint))
    assert result.status == "blocked" and adapter.inferences == 0


def test_worker_path_explicit_no_default_main_environment():
    with closing(PollardJevAdapter()) as adapter:
        with pytest.raises(HandlerExecutionError, match="separate Pollard-Jev"):
            adapter.generate(ModelRequest("", {"decision_request": {}}), config())


def test_timeout_covers_hung_worker_and_terminates_it(monkeypatch):
    actual_popen = subprocess.Popen
    processes = []

    def hung_worker(argv, **kwargs):
        process = actual_popen([sys.executable, "-u", "-c", "import time; time.sleep(20)"], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", hung_worker)
    with closing(PollardJevAdapter()) as adapter:
        started = monotonic()
        with pytest.raises(HandlerExecutionError, match="timed out"):
            adapter._exchange({"op": "inspect"}, config(worker_python=sys.executable, timeout_seconds=0.1))
        assert monotonic() - started < 5
        assert processes[0].poll() is not None and adapter._process is None


def test_worker_error_output_is_not_persisted(monkeypatch):
    actual_popen = subprocess.Popen

    def malformed_worker(argv, **kwargs):
        return actual_popen([sys.executable, "-u", "-c", "print('private-error-message')"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", malformed_worker)
    with closing(PollardJevAdapter()) as adapter:
        with pytest.raises(HandlerExecutionError) as failure:
            adapter._exchange({"op": "inspect"}, config(worker_python=sys.executable))
        assert "private-error-message" not in str(failure.value)
        assert adapter._process is None


def test_actual_package_worker_inspection_and_missing_weights(tmp_path):
    python = os.environ.get("CANOPY_TEST_POLLARD_JEV_PYTHON")
    if not python or not Path(python).is_file():
        pytest.skip("Set CANOPY_TEST_POLLARD_JEV_PYTHON to the isolated base-package environment")
    settings = config(worker_python=python, model_cache_dir=str(tmp_path), timeout_seconds=10)
    with closing(PollardJevAdapter()) as adapter:
        inspected = adapter._exchange({"op": "inspect"}, settings)
        assert inspected["ok"] and inspected["package_versions"] == {
            "pollard-jev": "0.13", "pollard": "1.6.0",
        }
        native = pollard_jev_sample_task().steps[0].inputs["decision_request"]
        with pytest.raises(HandlerExecutionError, match="model_load_failed"):
            adapter.generate(ModelRequest("", {"decision_request": native}), settings)


def test_local_loader_settings_change_cache_identity():
    first = register_model_handler(HandlerRegistry(), config())
    second = register_model_handler(HandlerRegistry(), replace(config(), max_context_tokens=1024))
    assert first.version != second.version


def test_reused_worker_rejects_changed_settings_before_inference():
    adapter = RecordedWorker()
    adapter._config = config()
    with pytest.raises(HandlerExecutionError, match="cannot change provider settings"):
        adapter.generate(ModelRequest("", {}), config(max_context_tokens=1024))
    assert adapter.inferences == 0


@pytest.mark.parametrize("field,value", [
    ("model", "another-model"), ("provider", "another-provider"),
    ("model_version", "unexpected-revision"), ("synthetic", True),
])
def test_worker_result_must_match_pinned_identity(field, value):
    class InconsistentWorker(RecordedWorker):
        def _exchange(self, payload, config):
            response = super()._exchange(payload, config)
            if payload["op"] == "infer":
                response["result"]["identity"][field] = value
            return response

    native = pollard_jev_sample_task().steps[0].inputs["decision_request"]
    with closing(InconsistentWorker()) as adapter:
        with pytest.raises(HandlerExecutionError, match="inconsistent result provenance") as failure:
            adapter.generate(ModelRequest("", {"decision_request": native}), config())
        assert failure.value.usage.input_tokens == 100
