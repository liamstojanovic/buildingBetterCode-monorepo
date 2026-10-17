"""Structural recommendations stay conservative without executing model calls."""

from copy import deepcopy

import pytest

from canopy._advisor_detection import inspect_step
from canopy.contracts import RunConfig, Step, StepResult, Task
from canopy.pollard_jev import pollard_jev_sample_task


def inspect(inputs=None, output=None, hints=None, *, side_effect=False):
    step = Step(step_id="choose", kind="model", handler="model.generate",
                inputs=inputs or {}, side_effect=side_effect)
    task = Task(task_id="task", tenant_id="tenant", workflow_id="workflow", steps=[step])
    if hints is not None:
        task.metadata = {"advisor": {"steps": {"choose": hints}}}
    result = StepResult(step_id="choose", status="completed", output=output)
    return inspect_step(task, step, result, RunConfig())


def test_business_enum_discovered_without_annotation_but_needs_text_mapping():
    found = inspect({"ticket": "A late delivery", "output_schema": {
        "type": "object", "properties": {"label": {"enum": ["billing", "delivery"]}}
    }}, {"provider": "openai", "text": '{"label":"delivery"}'})
    assert found.finding.category == "business_decision"
    assert found.finding.status == "needs_mapping"
    assert found.finding.native_compatibility == "requires_text_adapter"
    assert found.finding.choices == ["billing", "delivery"]
    assert not found.finding.partial_substitution
    assert found.selected_choice == "delivery"


def choice_question_inputs(field="priority"):
    return {
        "prompt": "An invented team cannot export a report needed tomorrow.",
        "questions": {field: {
            "type": "choice", "instructions": "Apply the supplied policy.",
            "criteria": {
                "high": "A workflow is blocked with a deadline within 48 hours.",
                "normal": "A workaround exists without a near-term deadline.",
            },
        }},
    }


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```"])
def test_real_recording_choice_question_is_partial_text_mapping(wrapper):
    found = inspect(choice_question_inputs(), {
        "provider": "anthropic", "model": "recorded-model", "response_id": "recorded-response",
        "provider_usage": {"input_tokens": 347},
        "text": wrapper.format('{"priority":"high","reason":"The deadline is tomorrow."}'),
    })
    assert found.finding.category == "business_decision"
    assert found.finding.status == "needs_mapping"
    assert found.finding.strategy == "evaluate_openjev"
    assert found.finding.native_compatibility == "requires_text_adapter"
    assert found.finding.partial_substitution
    assert found.finding.choices == ["high", "normal"]
    assert found.selected_choice == "high"
    assert found.descriptor.output_field == "priority"
    assert found.descriptor.candidate_hypotheses == {}
    assert "bounded_choice_question" in found.finding.signals
    assert any("Author and review a hypothesis" in change for change in found.finding.required_changes)
    assert any("remaining output work" in change for change in found.finding.required_changes)


@pytest.mark.parametrize("field, category", [
    ("priority", "business_decision"), ("tool", "tool_selection"),
    ("tool_name", "tool_selection"), ("next_step", "next_step"),
])
def test_named_question_maps_explicit_output_field_to_category(field, category):
    found = inspect(choice_question_inputs(field), {field: "high"})
    assert found.finding.category == category
    assert found.finding.status == "needs_mapping"
    assert found.selected_choice == "high"


def test_standalone_fences_do_not_split_equivalent_observed_output_shapes():
    inputs = choice_question_inputs()
    bare = '{"priority":"high","reason":"A deadline."}'
    plain = inspect(inputs, {"provider": "openai", "text": bare})
    fenced = inspect(inputs, {"provider": "anthropic", "text": f"```json\r\n{bare}\r\n```"})
    assert plain.grouping == fenced.grouping


def test_choice_question_criteria_semantics_remain_part_of_group_identity():
    hints = {"template_id": "priority", "template_version": "1"}
    inputs = choice_question_inputs()
    first = inspect(inputs, {"priority": "high"}, hints)
    changed = deepcopy(inputs)
    changed["questions"]["priority"]["criteria"]["high"] = "Different policy threshold."
    second = inspect(changed, {"priority": "high"}, hints)
    assert first.grouping != second.grouping
    assert first.grouping["catalogs"]["questions"] == inputs["questions"]


@pytest.mark.parametrize("questions", [
    None, [], {},
    {"priority": {"type": "text", "criteria": {"high": "Urgent"}}},
    {"priority": {"type": "choice", "criteria": {}}},
    {"priority": {"type": "choice", "criteria": {"high": ""}}},
    {"priority": {"type": "choice", "criteria": {"high": {"nested": "Urgent"}}}},
    {"priority": {"type": "choice", "criteria": {"high": "Urgent"}, "instructions": []}},
    {"priority": {"type": "choice", "criteria": {" ": "Urgent"}}},
    {"priority": {"type": "choice", "criteria": {str(i): "Criterion" for i in range(129)}}},
    {"priority": {"type": "choice", "criteria": {"high": "x" * 4001}}},
    {"priority": {"type": "choice", "criteria": {"high": "Urgent"}},
     "queue": {"type": "choice", "criteria": {"support": "Support queue"}}},
])
def test_malformed_or_multiple_questions_require_explicit_mapping(questions):
    found = inspect({"questions": questions}, {"priority": "high", "queue": "support"})
    assert found.finding.status == "needs_mapping"
    assert found.finding.strategy == "retain_llm"
    assert found.finding.partial_substitution
    assert found.selected_choice is None
    assert any("questions contract" in blocker for blocker in found.finding.blockers)


@pytest.mark.parametrize("schema", [
    {"enum": ["high", "normal"]},
    {"properties": {"label": {"enum": ["high", "normal"]}}},
    {"properties": {"priority": {"enum": ["high", "low"]}}},
    {"properties": {"tool": {"enum": ["high", "normal"]}}},
])
def test_choice_question_conflicts_with_different_schema_shape_field_or_choices(schema):
    found = inspect({**choice_question_inputs(), "output_schema": schema}, {"priority": "high"})
    assert found.finding.status == "needs_mapping"
    assert any("disagree" in blocker for blocker in found.finding.blockers)


def test_choice_question_with_matching_schema_keeps_bounded_discovery():
    found = inspect({**choice_question_inputs(), "output_schema": {
        "properties": {"priority": {"enum": ["high", "normal"]}}
    }}, {"priority": "high"})
    assert not any("disagree" in blocker for blocker in found.finding.blockers)
    assert found.selected_choice == "high"


@pytest.mark.parametrize("change,hints", [
    ({"allowed_tools": ["high", "normal"]}, None),
    ({"allowed_next_steps": ["high", "normal"]}, None),
    ({"choices": ["high", "low"]}, None),
    ({}, {"category": "tool_selection"}),
    ({}, {"output_field": "label"}),
])
def test_choice_question_conflicts_with_candidates_category_or_output_hint(change, hints):
    found = inspect({**choice_question_inputs(), **change}, {"priority": "high"}, hints)
    assert found.finding.status == "needs_mapping"
    assert any("disagree" in blocker for blocker in found.finding.blockers)


def test_multiple_questions_downgrade_even_an_otherwise_native_candidate():
    inputs = deepcopy(pollard_jev_sample_task().steps[0].inputs)
    inputs["questions"] = {
        "decision": {"type": "choice", "criteria": {"reorder": "Low", "wait": "Enough"}},
        "another": {"type": "choice", "criteria": {"yes": "Confirmed"}},
    }
    found = inspect(inputs, {"decision": "reorder", "another": "yes"})
    assert found.finding.status == "needs_mapping"
    assert found.finding.partial_substitution
    assert any("questions contract" in blocker for blocker in found.finding.blockers)


def test_declared_generation_does_not_hide_a_malformed_question_contract():
    found = inspect({"questions": {"priority": {"type": "choice"}}}, "Generated answer.",
                    {"generation_required": True})
    assert found.finding.status == "needs_mapping"
    assert found.finding.strategy == "retain_llm"
    assert found.finding.partial_substitution
    assert any("questions contract" in blocker for blocker in found.finding.blockers)


@pytest.mark.parametrize("text", [
    'Here is the answer:\n```json\n{"priority":"high"}\n```',
    '```json\n{"priority":"high"}\n```\nMore explanation.',
    '```json\n{"priority":"high"}\n```\n```json\n{"priority":"normal"}\n```',
    '```python\n{"priority":"high"}\n```',
    '```json {"priority":"high"}```',
    '```json\n{"priority":"high"}',
    '```json\n{"priority":"high","priority":"normal"}\n```',
    '```json\n{"priority":"high","confidence":NaN}\n```',
    '```json\n{"priority":"high","confidence":1e999}\n```',
])
def test_malformed_fenced_output_is_not_extracted_or_selected(text):
    found = inspect(choice_question_inputs(), {"provider": "openai", "text": text})
    assert found.selected_choice is None
    assert found.grouping["output_shape"] == "string"


@pytest.mark.parametrize("text", [
    '{"label":"a","label":"b"}',
    '{"label":"a","details":{"x":1,"x":2}}',
    '{"label":"a","confidence":NaN}',
    '{"label":"a","confidence":Infinity}',
    '{"label":"a","confidence":-Infinity}',
    '{"label":"a","confidence":1e999}',
])
def test_malformed_json_never_yields_a_selected_choice(text):
    found = inspect({"allowed_labels": ["a", "b"]}, {"provider": "openai", "text": text})
    assert found.selected_choice is None
    assert found.grouping["output_shape"] == "string"


def test_tool_allowlist_and_openai_catalog_preserve_argument_work():
    found = inspect({"allowed_tools": ["lookup_order"], "tools": [
        {"type": "function", "function": {"name": "lookup_order", "description": "Read order"}},
        {"type": "function", "function": {"name": "refund_order", "description": "Refund"}},
    ]}, {"tool": "lookup_order", "arguments": {"order_id": "123"}})
    assert found.finding.category == "tool_selection"
    assert found.finding.choices == ["lookup_order"]
    assert found.finding.partial_substitution
    assert any("argument generation" in reason for reason in found.finding.blockers)
    assert not any("disagree" in reason for reason in found.finding.blockers)


def test_existing_tool_arguments_can_be_bound_but_execution_still_gated():
    found = inspect({"allowed_tools": ["lookup_order"]},
                    {"tool_name": "lookup_order", "arguments": {"order_id": "123"}},
                    {"argument_source": "existing"})
    assert not found.finding.partial_substitution
    assert any("permissions and approvals" in change for change in found.finding.required_changes)


def test_next_step_transitions_are_discovered_without_metadata():
    found = inspect({"allowed_next_steps": ["retrieve", "clarify", "stop"]},
                    {"next_step": "clarify"})
    assert found.finding.category == "next_step"
    assert found.selected_choice == "clarify"
    assert any("stopping, loops" in change for change in found.finding.required_changes)


@pytest.mark.parametrize("key", ["allowed_labels", "choices", "candidates"])
def test_known_choice_inputs_are_discovered(key):
    found = inspect({key: ["approve", "review"]}, "review")
    assert found.finding.choices == ["approve", "review"]
    assert found.finding.status == "needs_mapping"
    assert found.selected_choice == "review"


def test_choices_may_have_authored_hypotheses():
    found = inspect({"choices": [
        {"name": "review", "hypothesis": "Human review is required."},
        {"name": "approve", "hypothesis": "The evidence supports approval."},
    ]}, {"decision": "review"})
    assert found.descriptor.candidate_hypotheses["review"] == "Human review is required."


@pytest.mark.parametrize("output", [{"tool": "private_tool"}, {"next_step": "stop"}, "approve"])
def test_observed_labels_are_weak_hints_not_candidate_contracts(output):
    found = inspect({}, output)
    assert found.finding.status == "needs_mapping"
    assert found.finding.strategy == "retain_llm"
    assert found.finding.choices == []
    assert found.selected_choice is None
    assert "private_tool" not in found.finding.model_dump_json()


def test_prompt_keywords_and_open_generation_are_not_selection_proof():
    found = inspect({"prompt": "classify this and select a tool, then write a detailed essay"},
                    "An unconstrained explanation. " * 10)
    assert found.finding.status == "unsupported"
    assert found.finding.strategy == "retain_llm"


def test_explicit_generation_without_choices_is_unsupported_even_with_short_output():
    found = inspect({"text": "A ticket"}, "Here is a response.", {
        "generation_required": True, "explanation_required": True, "evidence_kind": "text",
    })
    assert found.finding.status == "unsupported"
    assert found.finding.strategy == "retain_llm"


def test_mixed_explanation_is_only_partial_substitution():
    found = inspect({"output_schema": {"properties": {
        "label": {"enum": ["a", "b"]}, "explanation": {"type": "string"}
    }}}, {"label": "a", "explanation": "A long explanation."})
    assert found.finding.partial_substitution
    assert found.finding.status == "needs_mapping"


def test_explicit_rule_recommends_function_without_claiming_verification():
    found = inspect({"choices": ["reorder", "wait"]}, "reorder", {"deterministic_rule": True})
    assert found.finding.strategy == "deterministic_function"
    assert found.finding.status == "candidate"
    assert any("independent validation" in blocker for blocker in found.finding.blockers)


@pytest.mark.parametrize("extra_hint, output", [
    ({"explanation_required": True}, {"decision": "reorder"}),
    ({"generation_required": True}, {"decision": "reorder"}),
    ({}, {"decision": "reorder", "explanation": "Stock is low."}),
    ({"category": "tool_selection", "argument_source": "generated"}, {"tool": "reorder"}),
    ({"category": "tool_selection"}, {"tool": "reorder", "arguments": {"amount": 2}}),
])
def test_deterministic_decision_does_not_replace_remaining_generation(extra_hint, output):
    found = inspect({"choices": ["reorder", "wait"]}, output,
                    {"deterministic_rule": True, **extra_hint})
    assert found.finding.strategy == "deterministic_function"
    assert found.finding.status == "needs_mapping"
    assert found.finding.partial_substitution
    assert any("retained generation" in blocker for blocker in found.finding.blockers)


def test_schema_declared_explanation_keeps_deterministic_recommendation_partial():
    found = inspect({"output_schema": {"properties": {
        "decision": {"enum": ["reorder", "wait"]}, "explanation": {"type": "string"}
    }}}, {"decision": "reorder"}, {"deterministic_rule": True})
    assert found.finding.status == "needs_mapping"
    assert found.finding.partial_substitution


@pytest.mark.parametrize("field, hints", [
    ("tool", {"category": "business_decision"}),
    ("next_step", {"category": "tool_selection"}),
])
def test_conflicting_declared_category_downgrades_native_readiness(field, hints):
    found = native_inspection(hints=hints, output={field: "reorder"})
    assert found.finding.status == "needs_mapping"
    assert any("category conflicts" in blocker for blocker in found.finding.blockers)


@pytest.mark.parametrize("hints", [
    {"template_id": "secret-input-do-not-publish"},
    {"choices": ["a", "a"]},
    {"anything": "secret-input-do-not-publish"},
    "secret-input-do-not-publish",
])
def test_invalid_annotations_fail_closed_without_raw_exception_text(hints):
    found = inspect({"choices": ["a", "b"]}, "a", hints)
    assert found.finding.status == "needs_mapping"
    assert found.finding.strategy == "retain_llm"
    assert "secret-input-do-not-publish" not in found.finding.model_dump_json()


def test_exact_grouping_keeps_customer_inputs_when_no_template_is_declared():
    left = inspect({"ticket": "first", "choices": ["a", "b"]}, "a")
    right = inspect({"ticket": "second", "choices": ["a", "b"]}, "a")
    assert left.grouping_basis == "exact_inputs"
    assert left.grouping != right.grouping


def test_template_groups_explicit_instance_fields_but_keeps_static_and_catalog_values():
    hints = {"template_id": "router", "template_version": "1", "instance_fields": ["ticket"]}
    inputs = {"ticket": "first", "instructions": "Choose a queue", "choices": ["a", "b"]}
    base = inspect(inputs, "a", hints)
    second = inspect({**inputs, "ticket": "second"}, "a", hints)
    assert base.grouping_basis == "declared_template"
    assert base.grouping == second.grouping
    for change in ({"ticket": {"text": "first"}}, {"choices": ["a", "c"]},
                   {"instructions": "Choose a queue and explain"}, {"new_unknown_field": 1}):
        assert inspect({**inputs, **change}, "a", hints).grouping != base.grouping
    assert inspect(inputs, "a", {**hints, "template_version": "2"}).grouping != base.grouping


def test_template_id_alone_does_not_guess_away_dynamic_inputs():
    hints = {"template_id": "router", "template_version": "1"}
    assert inspect({"ticket": "first"}, "a", hints).grouping != inspect(
        {"ticket": "second"}, "a", hints
    ).grouping


def test_prompt_cannot_be_excluded_using_an_instance_field_hint():
    hints = {"template_id": "router", "template_version": "1", "instance_fields": ["prompt"]}
    first = inspect({"prompt": "First instructions", "choices": ["a", "b"]}, "a", hints)
    second = inspect({"prompt": "Changed instructions", "choices": ["a", "b"]}, "a", hints)
    assert first.finding.strategy == "retain_llm"
    assert first.grouping_basis == "exact_inputs"
    assert first.grouping != second.grouping


def test_missing_declared_instance_field_needs_mapping():
    found = inspect({"choices": ["a", "b"]}, "a", {
        "template_id": "router", "template_version": "1", "instance_fields": ["ticket"]
    })
    assert found.finding.status == "needs_mapping"
    assert found.finding.strategy == "retain_llm"
    assert any("absent instance fields" in blocker for blocker in found.finding.blockers)


def native_inspection(mutate=None, hints=None, output="reorder"):
    inputs = deepcopy(pollard_jev_sample_task().steps[0].inputs)
    if mutate:
        mutate(inputs["decision_request"])
    return inspect(inputs, output, hints)


def test_native_numeric_shape_is_only_structural_candidate_not_validated_replacement():
    found = native_inspection()
    assert found.finding.status == "candidate"
    assert found.finding.native_compatibility == "native_request_shape"
    assert any("have not been performed" in reason for reason in found.finding.blockers)
    assert any("abstention" in reason for reason in found.finding.blockers)


def test_conflicting_tool_and_transition_contracts_do_not_imply_business_readiness():
    inputs = deepcopy(pollard_jev_sample_task().steps[0].inputs)
    inputs.update(allowed_tools=["reorder", "wait"], allowed_next_steps=["reorder", "wait"])
    found = inspect(inputs, "reorder")
    assert found.finding.category == "unknown"
    assert found.finding.status == "needs_mapping"
    assert any("reconcile the decision category" in reason for reason in found.finding.blockers)
    assert any("permissions and approvals" in change for change in found.finding.required_changes)
    assert any("stopping, loops" in change for change in found.finding.required_changes)


def test_hosted_choice_only_output_remains_a_native_structural_candidate():
    found = native_inspection(output={"provider": "openai", "model": "model",
                                      "text": '{"decision":"reorder"}'})
    assert found.finding.status == "candidate"
    assert not found.finding.partial_substitution
    assert found.selected_choice == "reorder"


@pytest.mark.parametrize("output", [
    {"decision": "reorder", "text": "Explain why the stock is low."},
    {"provider": "openai", "decision": "reorder", "text": "Explain why the stock is low."},
])
def test_choice_and_generated_text_siblings_require_partial_substitution(output):
    found = native_inspection(output=output)
    assert found.finding.status == "needs_mapping"
    assert found.finding.partial_substitution
    assert found.selected_choice == "reorder"


def test_unknown_provider_envelope_is_not_silently_unwrapped():
    found = native_inspection(output={"provider": "unknown", "text": '{"decision":"reorder"}'})
    assert found.finding.status == "needs_mapping"
    assert found.finding.partial_substitution
    assert found.selected_choice is None


def test_complete_hosted_envelope_preserves_choice_only_detection():
    found = native_inspection(output={
        "provider": "anthropic", "model": "model", "response_id": "response",
        "provider_usage": {"input_tokens": 10}, "text": '{"decision":"reorder"}',
    })
    assert found.finding.status == "candidate"
    assert not found.finding.partial_substitution
    assert found.selected_choice == "reorder"


@pytest.mark.parametrize("mutate", [
    lambda request: request["observations"][0].update(value="arbitrary ticket text"),
    lambda request: request["observations"][0].update(value=True),
    lambda request: request["observations"][0].update(observed_at="2026-10-17T00:00:00"),
    lambda request: request["observations"][0].update(valid_until="2020-10-17T00:00:00Z"),
    lambda request: request["choices"][0].update(parameters={"amount": "100"}),
    lambda request: request["choices"][0].pop("hypothesis"),
    lambda request: request.update(observations=[]),
    lambda request: request.update(arbitrary_text="hidden text evidence"),
])
def test_native_boundary_rejects_untyped_or_invalid_representation(mutate):
    found = native_inspection(mutate)
    assert found.finding.status == "needs_mapping"
    assert found.finding.native_compatibility != "native_request_shape"


def test_native_template_keeps_observation_meaning_and_hypotheses_in_identity():
    hints = {"template_id": "inventory", "template_version": "1"}
    base = native_inspection(hints=hints)
    changed = native_inspection(lambda request: request["observations"][0].update(value=60.0), hints)
    assert base.grouping == changed.grouping
    changed_unit = native_inspection(lambda request: request["observations"][0].update(unit="cases"), hints)
    assert base.grouping != changed_unit.grouping
    changed_choice = native_inspection(lambda request: request["choices"][0].update(hypothesis="Other hypothesis"), hints)
    assert base.grouping != changed_choice.grouping


def test_already_native_backend_is_not_suggested_for_replacement():
    found = native_inspection(output={"provider": "pollard-jev", "decision": {"proposed_action": "reorder"}})
    assert found.finding.strategy == "retain_backend"
    assert found.finding.status == "unsupported"


def test_conflicting_choice_contracts_require_reconciliation():
    found = inspect({"choices": ["a", "b"], "output_schema": {"enum": ["a", "c"]}}, "a")
    assert any("disagree" in reason for reason in found.finding.blockers)


def test_unknown_observed_choice_is_not_published_as_a_selection():
    found = inspect({"choices": ["a", "b"]}, {"label": "sensitive unrecognized text"})
    assert found.selected_choice is None
    assert "sensitive" not in found.finding.model_dump_json()


def test_side_effects_require_separation_even_when_rule_is_deterministic():
    found = inspect({"choices": ["a", "b"]}, "a", {"deterministic_rule": True}, side_effect=True)
    assert found.finding.status == "needs_mapping"
    assert found.finding.partial_substitution
    assert any("side effects" in blocker for blocker in found.finding.blockers)


def test_unknown_model_handler_side_effects_keep_the_retained_handler_blocker():
    found = inspect({}, "Unbounded generated output. " * 10, side_effect=True)
    assert found.finding.status == "needs_mapping"
    assert found.finding.strategy == "retain_llm"
    assert found.finding.partial_substitution
    assert any("side effects" in blocker for blocker in found.finding.blockers)
