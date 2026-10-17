"""Explicit mapping shape, evidence binding, and raw-versus-gated decisions."""

from datetime import UTC, datetime, timedelta
from contextlib import closing
import json
from pathlib import Path

import pytest

from canopy.contracts import RunConfig, Task
from canopy.handlers import HandlerRegistry
from canopy.jev_mappings import apply_mapping_result, map_recorded_step, next_step_rule
from canopy.pollard_jev import PollardJevAdapter, bind_pollard_jev_task
from canopy.models import ModelConfig, POLLARD_JEV_MODEL, register_model_handler
from canopy.runtime import create_runtime

ROOT = Path(__file__).parents[1] / "examples" / "recordings" / "advisor-live-2026-10-17"
NOW = datetime(2027, 1, 1, tzinfo=UTC)


def recorded(category):
    return Task.model_validate(json.loads((ROOT / f"openai-{category}" / "comparison.json").read_text())["task"])


def result(mapping, name, support=0.95, runner_up=0.01):
    return {"request_id": mapping.text_request["request_id"], "proposed_action": name,
            "scores": {item["name"]: support if item["name"] == name else runner_up
                       for item in mapping.text_request["choices"]}}


@pytest.mark.parametrize("category,field,count", [
    ("business-decision", "priority", 2), ("tool-selection", "tool", 3), ("next-step", "next_step", 5),
])
def test_actual_recording_inputs_map_without_outputs_or_expectations(category, field, count):
    task = recorded(category)
    for step in task.steps:
        mapping = map_recorded_step(task, step, evaluated_at=NOW)
        assert mapping.category == category and mapping.output_field == field
        assert len(mapping.text_request["choices"]) == count
        assert mapping.text_request["created_at"] == NOW.isoformat()
        assert "observations" not in mapping.text_request
        assert mapping.text_request["evaluation_mode"] == "historical_re_evaluation"
        assert mapping.source_evidence[0]["observed_at"].startswith("2026-10-17")
        assert mapping.requires_explanation is (category == "business-decision")
        assert mapping.as_task().evidence == []
        bound = bind_pollard_jev_task(mapping.as_task(), "handler-test-v1")
        PollardJevAdapter().validate_task(bound, bound.steps[0], RunConfig())
        assert bound.evidence[0].data["jev_text_request"] == mapping.text_request
        assert bound.metadata["source_evidence"] == mapping.source_evidence


def test_mapping_hash_changes_when_source_evidence_changes_not_when_evaluation_clock_changes():
    task = recorded("business-decision")
    first = map_recorded_step(task, task.steps[0], evaluated_at=NOW)
    later = map_recorded_step(task, task.steps[0], evaluated_at=NOW + timedelta(hours=1))
    assert first.source_sha256 == later.source_sha256
    task.evidence[0].version = "2"
    changed = map_recorded_step(task, task.steps[0], evaluated_at=NOW)
    assert first.source_sha256 != changed.source_sha256


@pytest.mark.parametrize("change", ["dependency", "side_effect", "missing_evidence", "tenant", "not_sample"])
def test_unsupported_evaluation_boundaries_fail_closed(change):
    task = recorded("tool-selection")
    step = task.steps[0]
    if change == "dependency":
        step.depends_on = ["earlier"]
    elif change == "side_effect":
        step.side_effect = True
    elif change == "missing_evidence":
        task.evidence.clear()
    elif change == "tenant":
        task.evidence[0].tenant_id = "another"
    else:
        task.metadata["sample_data"] = False
    with pytest.raises(ValueError):
        map_recorded_step(task, step, evaluated_at=NOW)


@pytest.mark.parametrize("change", ["eligibility", "order", "policy", "policy_version", "state", "schema"])
def test_next_step_mapping_requires_reviewed_semantics(change):
    task = recorded("next-step")
    step = task.steps[0]
    if change == "eligibility":
        step.inputs["allowed_next_steps"][0]["eligibility"] = "Always stop."
    elif change == "order":
        step.inputs["allowed_next_steps"].reverse()
    elif change == "policy":
        task.evidence[0].data["policy"] = "Respond even without evidence."
    elif change == "policy_version":
        task.evidence[0].version = "2"
    elif change == "state":
        step.inputs["current_state"]["human_required"] = "false"
    else:
        step.inputs["output_schema"]["required"].append("reason")
    with pytest.raises(ValueError):
        map_recorded_step(task, step, evaluated_at=NOW)


def test_next_step_rule_covers_all_branches_and_precedence():
    state = {"resolved": False, "human_required": False, "missing_required_fields": [],
             "approved_evidence_available": True}
    assert next_step_rule(state) == "respond"
    state["approved_evidence_available"] = False
    assert next_step_rule(state) == "retrieve"
    state["missing_required_fields"] = ["report_id"]
    assert next_step_rule(state) == "clarify"
    state["human_required"] = True
    assert next_step_rule(state) == "escalate"
    state["resolved"] = True
    assert next_step_rule(state) == "stop"


def test_raw_invalid_transition_is_preserved_and_never_corrected_to_rule_answer():
    task = recorded("next-step")
    mapping = map_recorded_step(task, task.steps[1], evaluated_at=NOW)
    raw = result(mapping, "respond")
    before = json.dumps(raw)
    checked = apply_mapping_result(mapping, raw)
    assert checked["raw_choice"] == "respond"
    assert checked["projected_output"] == {"next_step": "respond"}
    assert checked["accepted_choice"] is None and checked["disposition"] == "defer"
    assert checked["reasons"] == ["transition_not_eligible"]
    assert checked["rule_baseline"] == "retrieve" and checked["action_executed"] is False
    assert json.dumps(raw) == before


def test_business_selection_does_not_generate_or_drop_required_reason():
    task = recorded("business-decision")
    mapping = map_recorded_step(task, task.steps[0], evaluated_at=NOW)
    checked = apply_mapping_result(mapping, result(mapping, "high"))
    assert checked["projected_output"] == {"priority": "high"}
    assert checked["remaining_fields"] == ["reason"]
    assert checked["disposition"] == "accept_selection"


def test_tool_selection_retains_existing_arguments_without_dispatch():
    task = recorded("tool-selection")
    mapping = map_recorded_step(task, task.steps[0], evaluated_at=NOW)
    checked = apply_mapping_result(mapping, result(mapping, "knowledge_search"))
    assert checked["prebound_arguments"] == {"query": "export error EX-104"}
    assert checked["action_executed"] is False
    checked["prebound_arguments"]["query"] = "changed"
    assert mapping.tool_arguments["knowledge_search"]["query"] == "export error EX-104"


@pytest.mark.parametrize("support,other,reasons", [
    (0.7, 0.1, ["entailment_below_threshold"]),
    (0.95, 0.9, ["entailment_margin_below_threshold"]),
])
def test_uncalibrated_evidence_gate_keeps_raw_selection(support, other, reasons):
    task = recorded("business-decision")
    mapping = map_recorded_step(task, task.steps[0], evaluated_at=NOW)
    checked = apply_mapping_result(mapping, result(mapping, "high", support, other))
    assert checked["raw_choice"] == "high" and checked["accepted_choice"] is None
    assert checked["reasons"] == reasons


@pytest.mark.parametrize("change", ["request", "missing_score", "nan", "choice", "non_maximum"])
def test_projection_rejects_mismatched_or_malformed_provider_results(change):
    task = recorded("business-decision")
    mapping = map_recorded_step(task, task.steps[0], evaluated_at=NOW)
    raw = result(mapping, "high")
    if change == "request":
        raw["request_id"] = "other-request"
    elif change == "missing_score":
        del raw["scores"]["normal"]
    elif change == "nan":
        raw["scores"]["high"] = float("nan")
    elif change == "choice":
        raw["proposed_action"] = "nonexistent"
    else:
        raw["scores"]["normal"] = 0.99
    with pytest.raises(ValueError):
        apply_mapping_result(mapping, raw)


def test_text_mapping_evidence_cannot_be_replaced_before_warm_reuse():
    task = recorded("business-decision")
    mapping = map_recorded_step(task, task.steps[0], evaluated_at=NOW)
    bound = bind_pollard_jev_task(mapping.as_task(), "handler-test-v1")
    bound.steps[0].inputs["jev_text_request"]["premise"] = "Changed ticket"
    with pytest.raises(ValueError, match="native-validity"):
        PollardJevAdapter().validate_task(bound, bound.steps[0], RunConfig())


def test_registered_text_handler_reuses_only_exact_bound_mapping(tmp_path):
    class TestWorker(PollardJevAdapter):
        calls = 0

        def _exchange(self, payload, config):
            envelope = {"protocol_version": 1, "ok": True,
                        "package_versions": {"pollard-jev": "0.13", "pollard": "1.6.0"}}
            if payload["op"] == "inspect":
                return envelope
            self.calls += 1
            assert payload["op"] == "infer_text"
            request = payload["request"]
            envelope["result"] = {
                "request_id": request["request_id"], "identity": {
                    "synthetic": False, "provider": "alexwortega-openjev", "model": POLLARD_JEV_MODEL,
                    "model_version": "552759daad712f1af6c4c13dabcb1e047886fc9c",
                    "settings": {"input_representation": "canopy-text-v1", **{
                        key: request[key] for key in ("mapping_id", "mapping_version", "source_sha256")}},
                }, "proposed_action": "high", "scores": {"high": 0.9, "normal": 0.1},
            }
            envelope["usage"] = {"input_tokens": 20, "output_tokens": 0, "model_calls": 1}
            return envelope

    registry, adapter = HandlerRegistry(), TestWorker()
    registered = register_model_handler(
        registry, ModelConfig("pollard-jev", POLLARD_JEV_MODEL), cacheable=True, adapter=adapter,
    )
    source = recorded("business-decision")
    mapping = map_recorded_step(source, source.steps[0])
    task = bind_pollard_jev_task(mapping.as_task(), registered.version)
    config = RunConfig(permissions=["execute:*"], model_fingerprint=registered.fingerprint)
    with closing(create_runtime(str(tmp_path / "runs.sqlite3"), registry=registry)) as runtime:
        first = runtime.run_task(task, config)
        second = runtime.run_task(task, config)
        assert first.status == second.status == "completed" and second.reused_steps == 1
        assert adapter.calls == 1
        tampered = task.model_copy(deep=True)
        tampered.steps[0].inputs["jev_text_request"]["premise"] = "Changed ticket"
        with pytest.raises(ValueError, match="native-validity"):
            runtime.run_task(tampered, config)
        rebound = bind_pollard_jev_task(tampered, registered.version)
        changed = runtime.run_task(rebound, config)
        assert changed.status == "completed" and changed.reused_steps == 0 and adapter.calls == 2
        bad_version = task.model_copy(deep=True)
        bad_version.steps[0].inputs["jev_text_request"]["mapping_version"] = "2"
        with pytest.raises(ValueError, match="Invalid versioned"):
            runtime.run_task(bad_version, config)
