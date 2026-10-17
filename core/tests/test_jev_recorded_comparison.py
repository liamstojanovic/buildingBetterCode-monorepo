"""Retained-source checks and fake-inference report tests; no model is downloaded or called."""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from canopy.contracts import HandlerResult, Usage
from canopy.models import ModelConfig, POLLARD_JEV_MODEL

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "core/examples/recordings/advisor-live-2026-10-17"


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location(
        "jev_recorded_comparison", ROOT / "core/examples/jev_recorded_comparison.py",
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def data(module):
    return module.collect_cases(SOURCE)


def test_retained_sources_preserve_all_real_calls_and_known_failure(module, data):
    assert len(data["cases"]) == 7
    assert len(data["hosted_calls"]) == 63
    assert data["excluded_reused_steps"] == 21
    assert sum(call["normalized_match"] for call in data["hosted_calls"]) == 62
    assert sum(call["bare_json_object"] for call in data["hosted_calls"]) == 29
    assert sum(call["strict_contract_match"] for call in data["hosted_calls"]) == 28
    wrong = [call for call in data["hosted_calls"] if not call["normalized_match"]]
    assert [(call["provider"], call["run_label"], call["step_id"], call["normalized_choice"])
            for call in wrong] == [("openai", "baseline_first", "choose_retrieval", "respond")]
    business = [call for call in data["hosted_calls"] if call["category"] == "business_decision"]
    assert len(business) == 9 and all(call["reason_retained"] for call in business)


@pytest.mark.parametrize("text,bare,match", [
    ('{"tool":"knowledge_search"}', True, True),
    ('```json\n{"tool":"knowledge_search"}\n```', False, True),
    ('Prefix {"tool":"knowledge_search"}', False, False),
    ('{"tool":"knowledge_search","tool":"incident_lookup"}', False, False),
    ('{"tool":"knowledge_search","extra":NaN}', False, False),
    ('["knowledge_search"]', False, False),
])
def test_normalization_never_excuses_strict_json_contract(module, text, bare, match):
    result = module.assess_output(text, field="tool", expected="knowledge_search", category="tool_selection")
    assert result["bare_json_object"] is bare
    assert result["normalized_match"] is match
    assert result["strict_contract_match"] is (bare and match)


def test_changed_recordings_fail_before_inference(module, tmp_path):
    directory = tmp_path / "source"
    shutil.copytree(SOURCE, directory)
    path = directory / "openai-next-step/comparison.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        module.collect_cases(directory)


def test_inventory_cannot_read_outside_source(module, tmp_path):
    directory = tmp_path / "source"
    shutil.copytree(SOURCE, directory)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    manifest["files"]["../outside.txt"] = sha256(b"outside").hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="inside"):
        module.collect_cases(directory)


class FakeAdapter:
    """An explicit test seam: results never leave pytest temporary directories."""

    revision = "test-only-fake-adapter"

    def __init__(self, *, synthetic=False, fail=False):
        self.requests = []
        self.closed = False
        self.synthetic = synthetic
        self.fail = fail

    def generate(self, request, config):
        self.requests.append(deepcopy(request.inputs))
        if self.fail:
            raise RuntimeError("test-only inference failure")
        native = request.inputs["jev_text_request"]
        # Choose the first available option deliberately, not the expected label.
        names = [choice["name"] for choice in native["choices"]]
        scores = {name: 0.95 if index == 0 else 0.01 for index, name in enumerate(names)}
        output = {
            "provider": "pollard-jev", "model": "test-only-fake",
            "decision": {
                "request_id": native["request_id"], "proposed_action": names[0],
                "scores": scores, "identity": {"synthetic": self.synthetic},
            },
            "phase_timings_ms": {"model_load": 100 if len(self.requests) == 1 else 0,
                                 "inference": 10},
        }
        return HandlerResult(output=output, usage=Usage(model_calls=1, input_tokens=12, tokens_known=True))

    def close(self):
        self.closed = True


def _config():
    return ModelConfig(provider="pollard-jev", model=POLLARD_JEV_MODEL,
                       worker_python="test-only-unused", device="cpu")


def test_two_passes_are_fourteen_inferences_not_sixty_three(module, data, tmp_path):
    adapter = FakeAdapter()
    when = datetime(2026, 10, 18, tzinfo=UTC)
    before = deepcopy(data)
    report = module.evaluate_cases(data, _config(), adapter=adapter, evaluated_at=when)
    assert data == before
    assert adapter.closed and len(adapter.requests) == 14
    assert report["summary"]["distinct_cases"] == 7
    assert report["summary"]["hosted_calls"] == 63
    assert report["summary"]["jev_trials"] == 14
    assert report["summary"]["jev_model_load_ms"] == 100
    assert report["summary"]["jev_inference_latency"]["count"] == 14
    assert report["summary"]["jev_warm_end_to_end_latency"]["count"] == 13
    assert sum(trial["worker_cold"] for case in report["cases"] for trial in case["jev_trials"]) == 1
    assert all(request["jev_text_request"]["created_at"].startswith("2026-10-18") for request in adapter.requests)
    for request in adapter.requests:
        assert "expected_choice" not in json.dumps(request)
    for case in report["cases"]:
        assert all(evidence["observed_at"].startswith("2026-10-17") for evidence in case["source_evidence"])
        if case["category"] == "next_step":
            assert case["rule_baseline"]["expected_match"]
            assert case["rule_baseline"]["model_calls"] == 0
        if case["step_id"] == "choose_retrieval":
            assert case["jev_trials"][0]["projection"]["raw_choice"] == "stop"
            assert case["jev_trials"][0]["projection"]["accepted_choice"] is None
            assert "transition_not_eligible" in case["jev_trials"][0]["projection"]["reasons"]
    assert report["summary"]["cost_savings_usd"] is None
    assert report["summary"]["energy_savings_j"] is None
    directory = tmp_path / "report"
    module.write_report(report, directory)
    assert json.loads((directory / "comparison.json").read_text(encoding="utf-8"))["format"] == module.FORMAT
    page = (directory / "index.html").read_text(encoding="utf-8")
    assert "MISMATCH" in page and "format violation" in page and "test-only-fake" in page
    assert 'href="../index.html"' in page
    assert "Mapped model input" in page and "Model loading:</strong> 100" in page
    assert "Accepted selection" in page and "Deferred" in page
    assert "Supplied policy evidence" in page
    assert "<script" not in page and "fetch(" not in page
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name, expected_digest in manifest["files"].items():
        assert module.digest(directory / name) == expected_digest
    with pytest.raises(FileExistsError):
        module.write_report(report, directory)


def test_fake_outputs_cannot_claim_actual_evaluation(module, data):
    adapter = FakeAdapter(synthetic=True)
    with pytest.raises(ValueError, match="synthetic outputs"):
        module.evaluate_cases(data, _config(), adapter=adapter)
    assert adapter.closed


def test_failure_closes_worker_without_manufacturing_results(module, data):
    adapter = FakeAdapter(fail=True)
    with pytest.raises(RuntimeError, match="test-only"):
        module.evaluate_cases(data, _config(), adapter=adapter)
    assert adapter.closed and len(adapter.requests) == 1


def test_html_escapes_recorded_content(module, data):
    report = module.evaluate_cases(data, _config(), adapter=FakeAdapter(), repeats=1)
    report["hosted_calls"][0]["output"]["text"] = "<script>alert(1)</script>"
    page = module.render_html(report)
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_journal_preserves_completed_trials_and_failure_before_abort(module, data, tmp_path):
    adapter = FakeAdapter()
    generate = adapter.generate

    def fail_second(request, config):
        if adapter.requests:
            raise RuntimeError("SECRET-MUST-NOT-BE-JOURNALED")
        return generate(request, config)

    adapter.generate = fail_second
    path = tmp_path / "trials.jsonl"
    with pytest.raises(RuntimeError, match="SECRET"):
        module.evaluate_cases(data, _config(), adapter=adapter, journal=path)
    raw = path.read_bytes()
    assert b"\r\n" not in raw and b"SECRET" not in raw
    entries = [json.loads(line) for line in raw.splitlines()]
    assert [entry["event"] for entry in entries] == [
        "evaluation_started", "trial_started", "trial_completed", "trial_started", "trial_failed",
    ]
    assert entries[2]["result"]["result"]["output"]["decision"]["proposed_action"] == "high"
    assert entries[-1]["error_type"] == "RuntimeError"
    assert entries[-1]["result_before_validation"] is None
    assert adapter.closed
    with pytest.raises(FileExistsError):
        module.evaluate_cases(data, _config(), adapter=FakeAdapter(), journal=path)
    assert path.read_bytes() == raw


def test_journal_requires_an_existing_parent_directory(module, data, tmp_path):
    adapter = FakeAdapter()
    with pytest.raises(FileNotFoundError):
        module.evaluate_cases(data, _config(), adapter=adapter, journal=tmp_path / "missing" / "trials.jsonl")
    assert adapter.requests == []
