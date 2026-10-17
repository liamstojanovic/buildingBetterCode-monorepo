"""Protocol tests plus real Pollard-Jev contracts in its separate base environment.

No test loads model weights. Set CANOPY_TEST_POLLARD_JEV_PYTHON to an interpreter
with pollard-jev==0.13 and pollard==1.6.0 to run the package integration cases.
"""

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

WORKER_PATH = Path(__file__).parents[1] / "src" / "canopy" / "_pollard_jev_worker.py"


@pytest.fixture
def worker_module():
    spec = importlib.util.spec_from_file_location("canopy_worker_test_module", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_import_requires_only_stdlib():
    script = (
        "import importlib.util, sys; "
        "spec=importlib.util.spec_from_file_location('worker',sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "assert not any(k == 'canopy' or k.startswith(('canopy.', 'pollard', 'torch', 'transformers')) "
        "for k in sys.modules)"
    )
    subprocess.run([sys.executable, "-I", "-c", script, str(WORKER_PATH)], check=True, capture_output=True)


def test_inspection_reports_incompatible_pin(worker_module, monkeypatch):
    versions = {"pollard-jev": "0.13", "pollard": "1.6.2"}
    monkeypatch.setattr(worker_module.metadata, "version", versions.__getitem__)
    result = worker_module.Worker().handle({"op": "inspect"})
    assert result["ok"] is False
    assert result["error_code"] == "unsupported_package_versions"
    assert result["package_versions"] == versions


def test_inspection_reports_missing_dependency_without_exception_text(worker_module, monkeypatch):
    def missing(_name):
        raise worker_module.metadata.PackageNotFoundError("private-path-and-secret")

    monkeypatch.setattr(worker_module.metadata, "version", missing)
    result = worker_module.Worker().handle({"op": "inspect"})
    assert result["error_code"] == "missing_dependencies"
    assert "private-path-and-secret" not in json.dumps(result)


def test_protocol_recovers_after_invalid_lines_and_duplicate_keys(worker_module, monkeypatch):
    versions = {"pollard-jev": "0.13", "pollard": "1.6.0"}
    monkeypatch.setattr(worker_module.metadata, "version", versions.__getitem__)
    source = io.StringIO('{bad json}\n{"op":"inspect","op":"infer"}\n{"value":NaN}\n{"op":"inspect"}\n')
    destination = io.StringIO()
    worker_module.serve(input_stream=source, output_stream=destination)
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert [item.get("error_code") for item in responses[:3]] == ["invalid_json"] * 3
    assert responses[3]["ok"] is True
    assert responses[3]["package_versions"] == versions


@pytest.mark.parametrize("message,code", [
    ([], "invalid_message"),
    ({"op": "exec"}, "unsupported_operation"),
    ({"op": "inspect", "protocol_version": 2}, "unsupported_protocol"),
    ({"op": "inspect", "protocol_version": True}, "unsupported_protocol"),
    ({"op": "inspect", "factory": "arbitrary.module"}, "invalid_message"),
])
def test_protocol_rejects_unsupported_operations(worker_module, message, code):
    assert worker_module.Worker().handle(message)["error_code"] == code


def test_protocol_discards_oversized_line_and_handles_next(worker_module, monkeypatch):
    monkeypatch.setattr(worker_module, "MAX_LINE_CHARS", 25)
    source = io.StringIO("x" * 100 + '\n{"op":"exec"}\n')
    output = io.StringIO()
    worker_module.serve(input_stream=source, output_stream=output)
    result = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [item["error_code"] for item in result] == ["message_too_large", "unsupported_operation"]


@pytest.fixture
def package_python():
    executable = os.environ.get("CANOPY_TEST_POLLARD_JEV_PYTHON")
    if not executable:
        pytest.skip("CANOPY_TEST_POLLARD_JEV_PYTHON is needed for isolated package integration")
    if not Path(executable).is_file():
        pytest.fail("CANOPY_TEST_POLLARD_JEV_PYTHON does not name an existing interpreter")
    return executable


PACKAGE_SCENARIO = r'''
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_file_location("canopy_isolated_worker", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from pollard_jev.contracts import ActionChoice, DecisionRequest, Observation
from pollard_jev.providers.openjev import OpenJevProvider

scenario = sys.argv[2]
now = datetime.now(timezone.utc)
state = {"loads": 0, "calls": 0}

class Encoder:
    template = "{premise} / {hypothesis}"
    max_len = 4096

    def tok(self, text, **kwargs):
        assert kwargs == {"truncation": False, "padding": False}
        return {"input_ids": [1, 2, 3, 4]}

    def predict_hypotheses(self, premise, hypotheses):
        state["calls"] += 1
        print("provider log must stay outside protocol")
        assert len(hypotheses) == 2
        if scenario == "encoder_failure":
            raise RuntimeError("do-not-persist-provider-secret")
        if scenario == "malformed_scores":
            return [[0.1, 0.8, 0.1]]
        return [[0.05, 0.9, 0.05], [0.2, 0.7, 0.1]]

def factory(**kwargs):
    state["loads"] += 1
    assert kwargs["representation"] == "json-v1"
    if scenario == "load_failure":
        raise RuntimeError("do-not-persist-cache-secret")
    encoder = Encoder()
    if scenario == "overflow":
        encoder.max_len = 3
    provider = OpenJevProvider(encoder, model="test-nli-encoder", model_version="fake-v1", synthetic=True)
    if scenario == "mismatched_result":
        original = provider.infer
        def mismatched(requests):
            result, = original(requests)
            return (result.model_copy(update={"request_id": "another-request"}),)
        provider.infer = mismatched
    return provider

question = DecisionRequest(
    request_id="invoice-decision", question="Which next step is supported by the invoice facts?",
    created_at=now-timedelta(minutes=1), valid_until=now+timedelta(minutes=10),
    observations=(Observation(
        observation_id="days-overdue", source="test-ledger", feature="invoice_days_overdue",
        value=45.0, unit="days", observed_at=now-timedelta(minutes=1),
        valid_until=now+timedelta(minutes=10),
    ),),
    choices=(
        ActionChoice(name="review", hypothesis="The invoice needs a review.", parameters={"priority": 2}),
        ActionChoice(name="wait", hypothesis="The invoice can wait.", parameters={"days": 1}),
    ),
).model_dump(mode="json")
clock_times = []
if scenario == "expiry_during_inference":
    clock_times = [now, now, now+timedelta(hours=1)]
elif scenario == "expiry_during_load":
    clock_times = [now, now+timedelta(hours=1)]
def clock():
    return clock_times.pop(0) if clock_times else now
if scenario == "stale":
    question["valid_until"] = (now-timedelta(seconds=1)).isoformat()
if scenario == "missing_observations":
    question["observations"] = []
if scenario == "invalid_request":
    question["observations"][0]["value"] = "not-a-number"
worker = module.Worker(provider_factory=None if scenario == "default_factory" else factory, clock=clock)
message = {"op": "infer", "request": question, "options": {"device": "cpu", "max_length": 4096}}
empty_cache = None
if scenario == "default_factory":
    empty_cache = tempfile.TemporaryDirectory()
    message["options"]["cache_dir"] = empty_cache.name
result = worker.handle(message)
second = None
if scenario in {"repeat", "configuration_changed"}:
    if scenario == "configuration_changed":
        message["options"]["max_length"] = 1024
    second = worker.handle(message)
print(json.dumps({"result": result, "second": second, "state": state,
                  "loaded_modules": [name for name in sys.modules if name in {
                      "pollard_jev.loop", "pollard_jev.policy", "pollard_jev.simulator",
                      "torch", "transformers", "canopy"}]}))
'''


def run_scenario(package_python, scenario):
    result = subprocess.run(
        [package_python, "-I", "-c", PACKAGE_SCENARIO, str(WORKER_PATH), scenario],
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=30,
    )
    assert "provider log" not in result.stdout
    assert "do-not-persist" not in result.stdout
    return json.loads(result.stdout)


def test_real_package_provider_mapping_and_context_meter(package_python):
    scenario = run_scenario(package_python, "success")
    result = scenario["result"]
    assert result["ok"] is True
    assert result["result"]["identity"]["synthetic"] is True
    assert result["result"]["semantics"] == "independent_entailment"
    assert result["result"]["scores"] == {"review": 0.9, "wait": 0.7}
    assert result["result"]["proposed_action"] == "review"
    assert result["result"]["parameters"] == {"priority": 2}
    assert result["usage"] == {"input_tokens": 8, "output_tokens": 0, "tokens_known": True, "model_calls": 1}
    assert result["measurement_basis"] == "complete_unpadded_pairs"
    assert result["measurements"][0]["tokenized_pair_lengths"] == [4, 4]
    assert scenario["state"] == {"loads": 1, "calls": 1}
    assert scenario["loaded_modules"] == []


def test_repeated_requests_reuse_model_but_count_each_inference(package_python):
    scenario = run_scenario(package_python, "repeat")
    assert scenario["result"]["ok"] is scenario["second"]["ok"] is True
    assert scenario["state"] == {"loads": 1, "calls": 2}
    assert scenario["second"]["usage"]["input_tokens"] == 8
    assert len(scenario["second"]["measurements"]) == 1


@pytest.mark.parametrize("scenario,code,loads,calls", [
    ("invalid_request", "invalid_request", 0, 0),
    ("missing_observations", "missing_observations", 0, 0),
    ("stale", "stale_request", 0, 0),
    ("load_failure", "model_load_failed", 1, 0),
    ("expiry_during_load", "stale_request", 1, 0),
    ("overflow", "context_limit_exceeded", 1, 0),
    ("encoder_failure", "inference_failed", 1, 1),
    ("malformed_scores", "inference_failed", 1, 1),
    ("mismatched_result", "invalid_result", 1, 1),
    ("expiry_during_inference", "stale_after_inference", 1, 1),
])
def test_real_package_failure_boundaries_preserve_measurements(package_python, scenario, code, loads, calls):
    observed = run_scenario(package_python, scenario)
    result = observed["result"]
    assert result["ok"] is False
    assert result["error_code"] == code
    assert observed["state"] == {"loads": loads, "calls": calls}
    assert result["usage"]["tokens_known"] is bool(calls)
    assert result["usage"]["input_tokens"] == (8 if calls else 0)
    assert result["usage"]["model_calls"] == calls
    assert "result" not in result


def test_real_default_factory_fails_without_model_and_never_downloads(package_python):
    scenario = run_scenario(package_python, "default_factory")
    assert scenario["result"]["error_code"] == "model_load_failed"
    assert scenario["result"]["usage"]["tokens_known"] is False
    assert scenario["result"]["usage"]["model_calls"] == 0
    assert scenario["state"] == {"loads": 0, "calls": 0}


def test_loaded_options_cannot_change(package_python):
    scenario = run_scenario(package_python, "configuration_changed")
    assert scenario["result"]["ok"] is True
    assert scenario["second"]["error_code"] == "configuration_changed"
    assert scenario["state"] == {"loads": 1, "calls": 1}


def test_process_protocol_works_without_core_or_model_dependencies(package_python):
    run = subprocess.run(
        [package_python, "-I", str(WORKER_PATH)],
        input='{"op":"inspect"}\nmalformed\n{"op":"inspect"}\n',
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=30,
    )
    responses = [json.loads(line) for line in run.stdout.splitlines()]
    assert len(responses) == 3
    assert responses[0]["ok"] is responses[2]["ok"] is True
    assert responses[0]["package_versions"]["pollard-jev"] == "0.13"
    assert responses[0]["package_versions"]["pollard"] == "1.6.0"
    assert responses[1]["error_code"] == "invalid_json"


TEXT_SCENARIO = r'''
import importlib.util
import json
import sys
from datetime import datetime, timezone
spec = importlib.util.spec_from_file_location("canopy_isolated_worker", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from pollard_jev.providers.openjev import OpenJevProvider
scenario = sys.argv[2]
payload = json.load(sys.stdin)
state = {"loads": 0, "calls": 0, "premises": [], "hypotheses": []}
class Encoder:
    template = "{premise} / {hypothesis}"
    max_len = 3 if scenario == "overflow" else 4096
    def tok(self, text, **kwargs):
        assert kwargs == {"truncation": False, "padding": False}
        return {"input_ids": [1, 2, 3, 4]}
    def predict_hypotheses(self, premise, hypotheses):
        state["calls"] += 1
        state["premises"].append(premise)
        state["hypotheses"].append(hypotheses)
        if scenario == "failure":
            raise RuntimeError("private-failure")
        if scenario == "invalid_scores":
            return [[0.1, float("nan"), 0.2] for _ in hypotheses]
        return [[0.05, 0.9, 0.05] if i == 0 else [0.2, 0.7, 0.1]
                for i, _ in enumerate(hypotheses)]
def factory(**options):
    state["loads"] += 1
    assert options["representation"] == "json-v1"
    return OpenJevProvider(Encoder(), model="unit-test-encoder", model_version="test-v1", synthetic=True)
worker = module.Worker(provider_factory=factory)
first = worker.handle({"op": "infer_text", "request": payload["text"], "options": {}})
second = None
third = None
if scenario == "mixed":
    second = worker.handle({"op": "infer", "request": payload["native"], "options": {}})
    third = worker.handle({"op": "infer_text", "request": payload["text"], "options": {}})
print(json.dumps({"first": first, "second": second, "third": third, "state": state}))
'''


def text_payload():
    from canopy.contracts import Task
    from canopy.jev_mappings import map_recorded_step
    from canopy.pollard_jev import pollard_jev_sample_task

    source = Path(__file__).parents[1] / "examples/recordings/advisor-live-2026-10-17/openai-business-decision/comparison.json"
    task = Task.model_validate(json.loads(source.read_text(encoding="utf-8"))["task"])
    return {"text": map_recorded_step(task, task.steps[0]).text_request,
            "native": pollard_jev_sample_task().steps[0].inputs["decision_request"]}


def run_text_scenario(package_python, scenario, payload=None):
    run = subprocess.run(
        [package_python, "-I", "-c", TEXT_SCENARIO, str(WORKER_PATH), scenario],
        input=json.dumps(payload or text_payload()),
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=30,
    )
    assert "private-failure" not in run.stdout
    return json.loads(run.stdout)


def test_text_bridge_forwards_actual_text_and_keeps_native_identity_separate(package_python):
    payload = text_payload()
    observed = run_text_scenario(package_python, "mixed", payload)
    first, second, third = (observed[name] for name in ("first", "second", "third"))
    assert first["ok"] is second["ok"] is third["ok"] is True
    assert observed["state"]["loads"] == 1 and observed["state"]["calls"] == 3
    assert observed["state"]["premises"][0] == payload["text"]["premise"]
    assert observed["state"]["hypotheses"][0] == [item["hypothesis"] for item in payload["text"]["choices"]]
    assert first["result"]["scores"] == {"high": 0.9, "normal": 0.7}
    assert first["result"]["nli_scores"]["high"]["contradiction"] == 0.05
    assert first["result"]["identity"]["settings"]["input_representation"] == "canopy-text-v1"
    assert first["result"]["identity"]["settings"]["source_sha256"] == payload["text"]["source_sha256"]
    assert second["result"]["identity"]["settings"] == {"input_representation": "json-v1"}
    assert first["usage"]["input_tokens"] == 8 and first["usage"]["model_calls"] == 1
    assert first["phase_timings_ms"]["model_load"] >= 0
    assert second["phase_timings_ms"]["model_load"] == 0


@pytest.mark.parametrize("scenario,code,calls", [
    ("overflow", "context_limit_exceeded", 0), ("failure", "inference_failed", 1),
    ("invalid_scores", "inference_failed", 1),
])
def test_text_bridge_failure_and_context_boundaries(package_python, scenario, code, calls):
    observed = run_text_scenario(package_python, scenario)
    assert observed["first"]["ok"] is False
    assert observed["first"]["error_code"] == code
    assert observed["first"]["usage"]["model_calls"] == calls
    assert "result" not in observed["first"]


@pytest.mark.parametrize("change", ["schema", "version", "mapping", "source", "duplicate", "numeric", "naive"])
def test_text_envelope_rejects_unversioned_or_untyped_inputs_before_loading(package_python, change):
    payload = text_payload()
    request = payload["text"]
    if change == "schema":
        request["schema_version"] = "future"
    elif change == "version":
        request["mapping_version"] = "2"
    elif change == "mapping":
        request["mapping_id"] = "unknown-code-factory"
    elif change == "source":
        request["source_sha256"] = "not-a-hash"
    elif change == "duplicate":
        request["choices"][1]["name"] = request["choices"][0]["name"]
    elif change == "numeric":
        request["premise"] = 42
    else:
        request["created_at"] = "2026-10-17T00:00:00"
    observed = run_text_scenario(package_python, "invalid", payload)
    assert observed["first"]["error_code"] == "invalid_request"
    assert observed["state"]["loads"] == observed["state"]["calls"] == 0
