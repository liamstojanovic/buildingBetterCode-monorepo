"""Conservative offline inspection: structural evidence is not model validation.

Nothing here invokes a model, imports the optional OpenJEV runtime, or changes a
handler. Grouping material stays internal; callers hash it rather than publish it.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from typing import Any, Literal

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from .advisor_contracts import AdvisorFinding, DecisionDescriptor
from .contracts import RunConfig, Step, StepResult, Task

_DATETIME = TypeAdapter(AwareDatetime)
_MISSING = object()
_OUTPUT_KEYS = ("tool", "tool_name", "next_step", "action", "label", "decision", "classification")
_CATALOG_KEYS = (
    "output_schema", "choices", "candidates", "allowed_labels", "tools", "allowed_tools",
    "allowed_next_steps", "next_steps", "questions",
)


@dataclass(frozen=True)
class StepInspection:
    descriptor: DecisionDescriptor
    finding: AdvisorFinding
    grouping: dict[str, Any]
    selected_choice: str | None
    grouping_basis: Literal["declared_template", "exact_inputs"]


def _name(value: Any, limit: int = 200) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _names(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            function = item.get("function")
            if isinstance(function, dict):
                item = function.get("name")
            else:
                labels = [item[key] for key in ("name", "id", "label", "tool", "tool_name", "next_step", "action")
                          if key in item]
                item = labels[0] if len(labels) == 1 else None
        if not _name(item) or item in result:
            return []
        result.append(item)
    return result


def _shape(value: Any, depth: int = 0) -> Any:
    """Value-free structure; full static values are retained separately."""
    if depth >= 12:
        return "depth_limit"
    if isinstance(value, dict):
        return {str(key): _shape(item, depth + 1)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        members = {_stable(_shape(item, depth + 1)) for item in value}
        return {"array_items": [json.loads(item) for item in sorted(members)]}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (str, datetime)):
        return "string"
    return "unknown"


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _annotation(task: Task, step: Step) -> tuple[DecisionDescriptor, bool, bool]:
    advisor = task.metadata.get("advisor", _MISSING)
    if advisor is _MISSING:
        return DecisionDescriptor(), False, False
    if not isinstance(advisor, dict) or not isinstance(advisor.get("steps"), dict):
        return DecisionDescriptor(), False, True
    value = advisor["steps"].get(step.step_id, _MISSING)
    if value is _MISSING:
        return DecisionDescriptor(), False, False
    try:
        if not isinstance(value, dict):
            raise ValueError("invalid annotation")
        return DecisionDescriptor.model_validate(value), True, False
    except (ValueError, TypeError, ValidationError):
        return DecisionDescriptor(), False, True


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _output(value: Any) -> Any:
    # Hosted adapters return text inside a provider envelope. Parse only bounded
    # JSON; short unstructured answers remain weak hints, never an enum contract.
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        envelope_keys = {"provider", "model", "response_id", "text", "provider_usage"}
        known_envelope = (
            value.get("provider") in ("openai", "openai-compatible", "anthropic", "gemini")
            and set(value) <= envelope_keys
        )
        # A business object can contain both a choice and generated text.
        # Unwrapping it would erase the additional work that must be retained.
        if set(value) == {"text"} or known_envelope:
            value = value["text"]
    if isinstance(value, str) and len(value) <= 16384:
        candidate = value
        # Accept one complete JSON code block, never extract a fragment from
        # surrounding prose or multiple blocks. Keep the strict JSON checks.
        fence = re.fullmatch(r"```(?:json)?\r?\n(.*?)\r?\n```", value.strip(), re.DOTALL)
        if fence and "```" not in fence[1]:
            candidate = fence[1]
        try:
            return json.loads(
                candidate, object_pairs_hook=_json_object,
                parse_float=_json_number, parse_constant=_json_number,
            )
        except (ValueError, RecursionError):
            return value
    return value


def _is_native_shape(request: Any) -> bool:
    """Recognize the pinned package's typed request shape without importing it.

    This checks representation only. Historical timestamps may be expired, and
    neither freshness now nor the semantic sufficiency of evidence is implied.
    """
    if not isinstance(request, dict) or set(request) != {
        "request_id", "question", "created_at", "valid_until", "observations", "choices"
    }:
        return False
    if not _name(request["request_id"]) or not _name(request["question"], 8000):
        return False
    observations, choices = request["observations"], request["choices"]
    if not isinstance(observations, list) or not observations or not _names(choices):
        return False
    try:
        starts = [_DATETIME.validate_python(request["created_at"])]
        ends = [_DATETIME.validate_python(request["valid_until"])]
        if ends[0] <= starts[0]:
            return False
        observation_ids = set()
        for observation in observations:
            required = {
                "observation_id", "source", "feature", "value", "unit", "observed_at", "valid_until"
            }
            if (not isinstance(observation, dict) or not required <= set(observation)
                    or set(observation) - required - {"status"}):
                return False
            if not all(_name(observation[key]) for key in ("observation_id", "source", "feature", "unit")):
                return False
            if observation["observation_id"] in observation_ids:
                return False
            observation_ids.add(observation["observation_id"])
            status, value = observation.get("status", "known"), observation["value"]
            if status not in ("known", "unknown") or ((status == "known") != (value is not None)):
                return False
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                return False
            starts.append(_DATETIME.validate_python(observation["observed_at"]))
            ends.append(_DATETIME.validate_python(observation["valid_until"]))
            if ends[-1] <= starts[-1]:
                return False
        for choice in choices:
            if (not isinstance(choice, dict) or not {"name", "hypothesis"} <= set(choice)
                    or set(choice) - {"name", "hypothesis", "parameters"}
                    or not _name(choice["hypothesis"], 4000)):
                return False
            parameters = choice.get("parameters", {})
            if not isinstance(parameters, dict) or not all(type(v) is int for v in parameters.values()):
                return False
        return max(starts) < min(ends)
    except (TypeError, ValueError, OverflowError):
        return False


def _schema_choices(schema: Any) -> tuple[list[str], str | None, bool]:
    if not isinstance(schema, dict):
        return [], None, False
    if choices := _names(schema.get("enum")):
        return choices, None, False
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return [], None, False
    enums = [(field, _names(value.get("enum"))) for field, value in properties.items()
             if isinstance(value, dict) and _names(value.get("enum"))]
    if len(enums) != 1:
        return [], None, len(enums) > 1
    return enums[0][1], enums[0][0], False


def _question_choices(questions: Any) -> tuple[list[str], str | None, bool]:
    """Recognize one authored choice question; criteria are not JEV hypotheses."""
    if questions is _MISSING:
        return [], None, False
    if not isinstance(questions, dict) or len(questions) != 1:
        return [], None, True
    field, question = next(iter(questions.items()))
    if (not _name(field) or not isinstance(question, dict)
            or question.get("type") != "choice"
            or ("instructions" in question and not _name(question["instructions"], 8000))):
        return [], None, True
    criteria = question.get("criteria")
    if (not isinstance(criteria, dict) or not _names(list(criteria))
            or not all(_name(text, 4000) for text in criteria.values())):
        return [], None, True
    return list(criteria), field, False


def _field_category(field: str | None) -> str:
    return ("tool_selection" if field in ("tool", "tool_name") else
            "next_step" if field == "next_step" else "business_decision")


def _grouping(step: Step, descriptor: DecisionDescriptor, output: Any) -> tuple[dict, str]:
    grouping: dict[str, Any] = {
        "descriptor": descriptor.model_dump(mode="json"),
        "input_shape": _shape(step.inputs), "output_shape": _shape(output),
    }
    if descriptor.template_id is None:
        grouping["exact_inputs"] = step.inputs
        return grouping, "exact_inputs"
    # A template ID alone cannot identify dynamic values safely. Explicit
    # instance-field hints may omit values, but never their shapes or catalogs.
    instance_fields = set(descriptor.instance_fields)
    grouping["static_inputs"] = {
        key: value for key, value in step.inputs.items()
        if key not in instance_fields or key in _CATALOG_KEYS
    }
    grouping["catalogs"] = {key: step.inputs[key] for key in _CATALOG_KEYS if key in step.inputs}
    request = step.inputs.get("decision_request")
    if _is_native_shape(request):
        grouping["static_inputs"]["decision_request"] = {
            "question": request["question"], "choices": request["choices"],
            "observations": [
                {**{key: value for key, value in item.items()
                    if key not in {"value", "observed_at", "valid_until"}},
                 "value_shape": _shape(item["value"])}
                for item in request["observations"]
            ],
        }
    return grouping, "declared_template"


def inspect_step(task: Task, step: Step, result: StepResult, config: RunConfig) -> StepInspection:
    """Explain one model step using only its recorded structural evidence."""
    descriptor, annotated, invalid = _annotation(task, step)
    output = _output(result.output)
    native_request = step.inputs.get("decision_request")
    native = _is_native_shape(native_request)
    schema = step.inputs.get("output_schema")
    choices, field, ambiguous = _schema_choices(schema)
    question_choices, question_field, question_problem = _question_choices(
        step.inputs.get("questions", _MISSING)
    )
    question_conflict = bool(question_choices and choices and field != question_field)
    sources: list[tuple[str, list[str], str]] = []
    if choices:
        sources.append(("bounded_output_schema", choices, _field_category(field)))
    if question_choices:
        sources.append(("bounded_choice_question", question_choices, _field_category(question_field)))
        if field is None:
            field = question_field
    for key, category in (
        ("allowed_next_steps", "next_step"), ("next_steps", "next_step"),
        ("allowed_tools", "tool_selection"), ("tools", "tool_selection"),
        ("allowed_labels", "business_decision"), ("choices", "business_decision"),
        ("candidates", "business_decision"),
    ):
        if names := _names(step.inputs.get(key)):
            sources.append((key, names, category))
    if isinstance(native_request, dict) and (names := _names(native_request.get("choices"))):
        sources.append(("native_choices", names, "business_decision"))
    if descriptor.choices:
        sources.insert(0, ("declared_choices", descriptor.choices, descriptor.category))
    if sources:
        choices = sources[0][1]
    declared_category = descriptor.category
    category = declared_category
    if category == "unknown" and sources:
        categories = {item[2] for item in sources} - {"unknown", "business_decision"}
        category = next(iter(categories)) if len(categories) == 1 else "business_decision"
    if field is None:
        field = descriptor.output_field
    if isinstance(output, dict) and field is None:
        field = next((key for key in _OUTPUT_KEYS if isinstance(output.get(key), str)), None)
    if category == "unknown":
        category = ("tool_selection" if field in ("tool", "tool_name") else
                    "next_step" if field == "next_step" else
                    "business_decision" if field in _OUTPUT_KEYS else "unknown")
    specialized_categories = {item[2] for item in sources} & {"tool_selection", "next_step"}
    if field in ("tool", "tool_name"):
        specialized_categories.add("tool_selection")
    elif field == "next_step":
        specialized_categories.add("next_step")
    ambiguous_category = len(specialized_categories) > 1
    if ambiguous_category:
        category = "unknown"
    if question_choices:
        question_category = _field_category(question_field)
        question_conflict = question_conflict or bool(
            specialized_categories - {question_category}
        ) or declared_category not in ("unknown", question_category)
    hypothesis_map = dict(descriptor.candidate_hypotheses)
    for key in ("choices", "candidates"):
        for choice in step.inputs.get(key, []) if isinstance(step.inputs.get(key), list) else []:
            if (isinstance(choice, dict) and choice.get("name") in choices
                    and _name(choice.get("hypothesis"), 4000)):
                hypothesis_map.setdefault(choice["name"], choice["hypothesis"])
    if native:
        hypothesis_map.update({item["name"]: item["hypothesis"] for item in native_request["choices"]})
    descriptor = descriptor.model_copy(update={
        "category": category, "choices": choices, "output_field": field,
        "candidate_hypotheses": hypothesis_map,
    })
    grouping, basis = _grouping(step, descriptor, output)
    selected = output.get(field) if isinstance(output, dict) and field else output
    selected = selected if isinstance(selected, str) and selected in choices else None

    def finish(finding: AdvisorFinding) -> StepInspection:
        if step.side_effect and finding.strategy != "retain_backend":
            finding = finding.model_copy(update={
                "status": "needs_mapping", "partial_substitution": True,
                "blockers": [*finding.blockers,
                             "The handler performs side effects that backend substitution cannot replace."],
                "required_changes": [*finding.required_changes,
                                     "Separate selection from side effects and retain execution authorization gates."],
            })
        return StepInspection(descriptor, finding, grouping, selected, basis)

    provider = result.output.get("provider") if isinstance(result.output, dict) else None
    if provider in ("pollard-jev", "jev"):
        return finish(AdvisorFinding(
            category=category, status="unsupported", strategy="retain_backend", choices=choices,
            signals=["already_pollard_jev"], reasons=["This call already uses the native backend."],
            native_compatibility="not_applicable",
        ))
    missing_instance = bool(set(descriptor.instance_fields) - set(step.inputs))
    if invalid or missing_instance:
        return finish(AdvisorFinding(
            category=category, status="needs_mapping", strategy="retain_llm", choices=choices,
            blockers=["The advisor annotation is invalid or references absent instance fields; repair it before using its hints."],
            required_changes=["Supply a valid versioned decision descriptor."],
        ))
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    observed_fields = set(output) if isinstance(output, dict) else set()
    schema_fields = set(properties) if isinstance(properties, dict) else set()
    extra_fields = (observed_fields | schema_fields) - {field}
    has_arguments = bool(extra_fields & {"arguments", "parameters", "args"})
    extra_fields -= {"arguments", "parameters", "args"}
    generated_arguments = descriptor.argument_source == "generated" or (
        has_arguments and descriptor.argument_source != "existing"
    )
    mixed = (descriptor.explanation_required or descriptor.generation_required or question_problem
             or bool(extra_fields) or generated_arguments)
    category_conflict = declared_category != "unknown" and bool(
        specialized_categories - {declared_category}
    )
    conflict = (ambiguous or ambiguous_category or category_conflict
                or question_problem or question_conflict) or bool(
        annotated and field and task.metadata["advisor"]["steps"][step.step_id].get("output_field")
        and task.metadata["advisor"]["steps"][step.step_id]["output_field"] != field
    )
    for key, names, _ in sources:
        # A state-specific allowlist can legitimately be a subset of the tool
        # catalog. Other competing explicit sets require a clarified contract.
        if key == "tools" and set(choices) <= set(names):
            continue
        if set(names) != set(choices):
            conflict = True
    if descriptor.deterministic_rule:
        blockers = ["The rule and equivalent outcomes still require independent validation."]
        changes = ["Implement and benchmark the explicit rule as an ordinary function."]
        if mixed:
            blockers.append("The rule only replaces the decision; retained generation, arguments or other output work still has a resource cost.")
            changes.append("Separate the deterministic decision from remaining output work and measure both.")
        if conflict:
            blockers.append("The declared decision contract conflicts with the recorded schema or candidates.")
            changes.append("Reconcile the decision category, output schema and candidate catalogs.")
        if question_problem:
            blockers.append("The questions contract is malformed or contains multiple questions; map each decision separately.")
        if ambiguous_category:
            blockers.append("Recorded structure mixes tool-selection and next-step decision contracts; reconcile the decision category.")
        return finish(AdvisorFinding(
            category=category, status="needs_mapping" if mixed or conflict else "candidate",
            strategy="deterministic_function", choices=choices, partial_substitution=mixed,
            signals=["declared_deterministic_rule"],
            reasons=["The application declares an explicit deterministic rule."],
            blockers=blockers, required_changes=changes,
            native_compatibility="not_applicable",
        ))
    if not choices:
        weak = field is not None or (isinstance(output, str) and 0 < len(output.strip()) <= 80)
        if descriptor.generation_required and not question_problem:
            return finish(AdvisorFinding(
                category=category, status="unsupported", strategy="retain_llm",
                signals=["declared_generation_requirement"],
                reasons=["The declared output requires generation and has no bounded selection contract."],
                native_compatibility="not_applicable",
            ))
        return finish(AdvisorFinding(
            category=category, status="needs_mapping" if weak or annotated or question_problem else "unsupported",
            strategy="retain_llm", signals=["observed_selection_hint"] if weak else [],
            reasons=["Observed output alone does not establish the complete set of valid choices."]
            if weak else ["No bounded selection contract was found."],
            blockers=["A complete authored candidate set and evidence mapping are missing."] + (
                ["The questions contract is malformed or contains multiple questions; map each decision separately."]
                if question_problem else []
            ),
            required_changes=["Declare the decision contract before evaluating substitution."],
            partial_substitution=mixed if question_problem else False,
            native_compatibility="not_applicable",
        ))

    signals = list(dict.fromkeys(item[0] for item in sources))
    blockers = [
        "Independent outcome evaluation and resource measurements have not been performed.",
        "An explicit acceptance and abstention rule is required; scores do not authorize execution.",
    ]
    changes = ["Validate the candidate set against independent expected outcomes and missing-option cases."]
    if conflict:
        blockers.append("The recorded candidate contracts disagree or have ambiguous enum fields.")
        changes.append("Reconcile the decision category, output schema, declared choices and candidate catalogs.")
    if question_problem:
        blockers.append("The questions contract is malformed or contains multiple questions; map each decision separately.")
    elif question_conflict:
        blockers.append("The named choice question conflicts with the recorded output field or decision category.")
    if category_conflict:
        blockers.append("The declared decision category conflicts with recorded tool or transition structure.")
    if ambiguous_category:
        blockers.append("Recorded structure mixes tool-selection and next-step decision contracts; reconcile the decision category.")
    includes_tools = category == "tool_selection" or (
        ambiguous_category and "tool_selection" in specialized_categories
    )
    if includes_tools and generated_arguments:
        blockers.append("Tool selection does not replace argument generation or validation.")
        changes.append("Separate tool identity selection from generated arguments and execution.")
    elif includes_tools:
        changes.append("Bind existing arguments and enforce tool permissions and approvals at execution.")
        if descriptor.argument_source == "unknown":
            blockers.append("The source of tool arguments has not been established.")
    if mixed:
        blockers.append("The output also requires generation or additional fields beyond the bounded choice.")
        changes.append("Keep or independently map the remaining output work and include its resource cost.")
    if category == "next_step" or (ambiguous_category and "next_step" in specialized_categories):
        changes.append("Validate stopping, loops, missing transitions and state-dependent eligibility.")
    if native:
        signals.append("typed_numeric_observations_and_hypotheses")
        compatibility = "native_request_shape"
        changes.append("Preserve native request validity and map the proposal to the existing output contract.")
    else:
        text_evidence = descriptor.evidence_kind == "text" or any(
            isinstance(step.inputs.get(key), str) for key in ("text", "ticket", "document", "context")
        ) or (bool(question_choices) and isinstance(step.inputs.get("prompt"), str))
        compatibility = "requires_text_adapter" if text_evidence else "requires_mapping"
        blockers.append("The current native bridge requires typed numeric observations and authored hypotheses.")
        changes.append("Design an explicit text-evidence adapter." if text_evidence else
                       "Map decision evidence to a supported representation without discarding its meaning.")
        if set(hypothesis_map) != set(choices):
            changes.append("Author and review a hypothesis for every eligible choice.")
    if native_request is not None and not native:
        blockers.append("The supplied decision_request does not match the pinned native request shape.")
    status = "candidate" if native and not conflict and not mixed else "needs_mapping"
    return finish(AdvisorFinding(
        category=category, status=status, strategy="evaluate_openjev", signals=signals,
        reasons=["An explicit bounded choice set makes this selection worth evaluating."],
        blockers=blockers, required_changes=changes, choices=choices, partial_substitution=mixed,
        native_compatibility=compatibility,
    ))
