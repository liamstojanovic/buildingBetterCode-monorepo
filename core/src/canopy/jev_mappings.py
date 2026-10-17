"""Explicit, versioned NLI mappings for the retained support demo recordings.

This is a historical evaluation adapter, not an automatic routing policy. It
never reads provider answers or fixture expectations to construct model inputs.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import math
from typing import Any

from .contracts import Step, Task, utcnow
from .recording import canonical_json

TEXT_SCHEMA = "canopy-jev-text/v1"
MAPPING_VERSION = "1"
_TOOL_NAMES = ("knowledge_search", "incident_lookup", "ticket_lookup")
_NEXT_NAMES = ("stop", "escalate", "clarify", "retrieve", "respond")
_NEXT_ELIGIBILITY = (
    "The customer has confirmed resolution; the workflow is complete.",
    "Unresolved state explicitly requires a human specialist.",
    "Unresolved state is missing one or more required fields.",
    "Unresolved state has required fields but lacks approved evidence.",
    "Unresolved state has required fields and approved evidence.",
)
_NEXT_POLICY = (
    "Evaluate the supplied transition catalog in order and select the first "
    "eligible transition. A resolved workflow always stops. Otherwise human "
    "requirement takes precedence over missing fields, then missing evidence, "
    "then responding. Select only the transition; do not execute it."
)
_NEXT_HYPOTHESES = {
    "stop": "The customer has confirmed resolution, so the workflow is complete and should stop.",
    "escalate": "The workflow is unresolved and requires a human specialist, so it should escalate.",
    "clarify": (
        "The workflow is unresolved, does not require a human specialist, and is missing "
        "required fields, so it should ask the customer to clarify."
    ),
    "retrieve": (
        "The workflow is unresolved, does not require a human specialist, has all required "
        "fields, and lacks approved evidence, so it should retrieve evidence."
    ),
    "respond": (
        "The workflow is unresolved, does not require a human specialist, has all required "
        "fields and approved evidence, so it should respond to the customer."
    ),
}


@dataclass(frozen=True)
class JevMapping:
    category: str
    mapping_id: str
    mapping_version: str
    source_sha256: str
    output_field: str
    text_request: dict[str, Any]
    requires_explanation: bool
    source_evidence: list[dict[str, Any]]
    gate_state: dict[str, Any] | None
    tool_arguments: dict[str, dict[str, Any]]
    tenant_id: str
    source_step_id: str

    def as_task(self) -> Task:
        """An evaluation-only task with new bounded evaluation validity.

        The original evidence is retained in metadata, not relabeled as current
        operational evidence. bind_pollard_jev_task adds exact request validity.
        """
        return Task(
            task_id=f"jev-evaluation-{self.source_sha256[:16]}", tenant_id=self.tenant_id,
            workflow_id=f"jev-historical-{self.category}",
            steps=[Step(step_id=self.source_step_id, kind="model", handler="model.generate",
                        inputs={"jev_text_request": deepcopy(self.text_request)})],
            metadata={"sample_data": True, "historical_re_evaluation": True,
                      "mapping_id": self.mapping_id, "mapping_version": self.mapping_version,
                      "source_sha256": self.source_sha256,
                      "source_evidence": deepcopy(self.source_evidence)},
        )


def _state(state: Any) -> dict:
    fields = {"resolved", "human_required", "missing_required_fields", "approved_evidence_available"}
    if not isinstance(state, dict) or set(state) != fields:
        raise ValueError("Next-step mapping requires the complete versioned state")
    for field in fields - {"missing_required_fields"}:
        if type(state[field]) is not bool:
            raise ValueError("Next-step state flags must be explicit booleans")
    missing = state["missing_required_fields"]
    if (not isinstance(missing, list) or any(not isinstance(item, str) or not item for item in missing)
            or len(set(missing)) != len(missing)):
        raise ValueError("Missing fields must be unique nonempty names")
    return state


def next_step_rule(state: dict) -> str:
    """Independent deterministic baseline and execution gate; never an NLI answer."""
    state = _state(state)
    if state["resolved"]:
        return "stop"
    if state["human_required"]:
        return "escalate"
    if state["missing_required_fields"]:
        return "clarify"
    return "respond" if state["approved_evidence_available"] else "retrieve"


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} requires nonempty text")
    return value


def _schema(inputs: dict, field: str, choices: tuple[str, ...]) -> None:
    expected = {"type": "object", "properties": {field: {"type": "string", "enum": list(choices)}},
                "required": [field], "additionalProperties": False}
    if inputs.get("output_schema") != expected:
        raise ValueError("Mapping requires the reviewed output schema and candidate order")


def map_recorded_step(
    task: Task, step: Step, *, evaluated_at: datetime | None = None, validity_seconds: int = 3600,
) -> JevMapping:
    """Map one of the three reviewed recording shapes to text and hypotheses.

    Inputs and all referenced policy evidence bind the mapping hash. Historical
    timestamps are preserved separately. Runtime results and expected labels do
    not enter this interface. Unsupported catalogs/states fail closed.
    """
    if step.kind != "model" or step.handler != "model.generate" or step.side_effect or step.depends_on:
        raise ValueError("Only model selection steps without side effects can be mapped")
    if not any(item == step for item in task.steps):
        raise ValueError("Step must belong to the supplied task")
    if task.metadata.get("sample_data") is not True:
        raise ValueError("These mappings are scoped to explicitly labeled sample recordings")
    now = evaluated_at or utcnow()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Evaluation time must be timezone-aware")
    if type(validity_seconds) is not int or not 1 <= validity_seconds <= 86400:
        raise ValueError("Evaluation validity must be between 1 and 86400 seconds")
    evidence = [item.model_dump(mode="json") for item in task.evidence
                if item.evidence_id in step.evidence_ids]
    if not evidence or {item["evidence_id"] for item in evidence} != set(step.evidence_ids):
        raise ValueError("Mapping requires every referenced policy evidence item")
    if any(item["tenant_id"] != task.tenant_id for item in evidence):
        raise ValueError("Evidence must belong to the task tenant")
    inputs = deepcopy(step.inputs)
    gate_state, tool_arguments = None, {}
    if "questions" in inputs:
        question = inputs.get("questions", {}).get("priority", {})
        criteria = question.get("criteria")
        if (set(inputs.get("questions", {})) != {"priority"} or question.get("type") != "choice"
                or not isinstance(criteria, dict) or tuple(criteria) != ("high", "normal")):
            raise ValueError("Business mapping requires the reviewed high/normal priority question")
        category, field = "business-decision", "priority"
        facts = "Customer ticket: " + _text(inputs.get("prompt"), "Ticket")
        choices = [{"name": name, "hypothesis": _text(rule, "Priority criterion")}
                   for name, rule in criteria.items()]
    elif "tools" in inputs:
        catalog = inputs["tools"]
        if (not isinstance(catalog, list) or len(catalog) != 3
                or any(not isinstance(item, dict) for item in catalog)
                or tuple(item.get("name") for item in catalog) != _TOOL_NAMES):
            raise ValueError("Tool mapping requires the reviewed read-only tool catalog")
        _schema(inputs, "tool", _TOOL_NAMES)
        for item in catalog:
            if item.get("read_only") is not True or not isinstance(item.get("arguments"), dict):
                raise ValueError("Tool mapping needs read-only tools with prebound arguments")
            tool_arguments[item["name"]] = deepcopy(item["arguments"])
        category, field = "tool-selection", "tool"
        facts = "Customer request: " + _text(inputs.get("ticket"), "Ticket")
        choices = [{"name": item["name"], "hypothesis": (
            "The customer's request is to " + _text(item.get("description"), "Tool description")
            .rstrip(".").lower() + "."
        )} for item in catalog]
    elif "current_state" in inputs:
        gate_state = deepcopy(_state(inputs["current_state"]))
        catalog = inputs.get("allowed_next_steps")
        if (not isinstance(catalog, list) or len(catalog) != 5
                or any(not isinstance(item, dict) for item in catalog)
                or tuple(item.get("name") for item in catalog) != _NEXT_NAMES):
            raise ValueError("Next-step mapping requires the reviewed ordered transition catalog")
        if tuple(item.get("eligibility") for item in catalog) != _NEXT_ELIGIBILITY:
            raise ValueError("Next-step mapping cannot reinterpret changed eligibility rules")
        if (len(evidence) != 1 or evidence[0]["version"] != "1"
                or evidence[0]["data"] != {"sample_data": True, "policy": _NEXT_POLICY}):
            raise ValueError("Next-step mapping requires the reviewed precedence policy version")
        _schema(inputs, "next_step", _NEXT_NAMES)
        # Bind the supplied eligibility descriptions as evidence while expressing
        # the state in plain text. Do not put next_step_rule(state) in the premise.
        for item in catalog:
            _text(item.get("eligibility"), "Transition eligibility")
        category, field = "next-step", "next_step"
        facts = (
            f"The workflow is {'resolved' if gate_state['resolved'] else 'unresolved'}. "
            f"A human specialist is {'required' if gate_state['human_required'] else 'not required'}. "
            + ("Missing required fields: " + ", ".join(gate_state["missing_required_fields"]) + ". "
               if gate_state["missing_required_fields"] else "All required fields are available. ")
            + f"Approved evidence is {'available' if gate_state['approved_evidence_available'] else 'not available'}."
        )
        choices = [{"name": name, "hypothesis": _NEXT_HYPOTHESES[name]} for name in _NEXT_NAMES]
    else:
        raise ValueError("No reviewed JEV mapping for this step")
    mapping_id = f"canopy-support-{category}-v{MAPPING_VERSION}"
    source = {"tenant_id": task.tenant_id, "step_id": step.step_id, "inputs": inputs,
              "evidence": evidence}
    digest = sha256(canonical_json(source).encode()).hexdigest()
    # Keep policies/catalogs visible to the model without replaying LLM-format
    # instructions or hiding the evidence in an arbitrary numeric encoding.
    premise = facts + "\nSupplied policy evidence: " + canonical_json([item["data"] for item in evidence])
    if gate_state is not None:
        premise += "\nOrdered transition catalog: " + canonical_json(inputs["allowed_next_steps"])
    request = {"schema_version": TEXT_SCHEMA, "request_id": f"{mapping_id}:{digest[:16]}",
               "mapping_id": mapping_id, "mapping_version": MAPPING_VERSION,
               "source_sha256": digest, "evaluation_mode": "historical_re_evaluation",
               "created_at": now.isoformat(), "valid_until": (now + timedelta(seconds=validity_seconds)).isoformat(),
               "premise": premise, "choices": choices}
    return JevMapping(category, mapping_id, MAPPING_VERSION, digest, field, request,
                      category == "business-decision", evidence, gate_state, tool_arguments,
                      task.tenant_id, step.step_id)


def apply_mapping_result(
    mapping: JevMapping, provider_result: dict, *,
    minimum_entailment: float = 0.8, minimum_margin: float = 0.15,
) -> dict[str, Any]:
    """Project a raw NLI selection and report a separate conservative gate.

    Thresholds are uncalibrated demo heuristics. A rejected choice is never
    replaced by the deterministic answer, and no action is dispatched.
    """
    for value in (minimum_entailment, minimum_margin):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("Gate thresholds must be finite values between zero and one")
    names = [item["name"] for item in mapping.text_request["choices"]]
    if provider_result.get("request_id") != mapping.text_request["request_id"]:
        raise ValueError("JEV result does not match the mapped request")
    raw = provider_result.get("proposed_action")
    scores = provider_result.get("scores")
    if (raw not in names or not isinstance(scores, dict) or set(scores) != set(names)
            or any(isinstance(score, bool) or not isinstance(score, (int, float))
                   or not math.isfinite(score) or not 0 <= score <= 1 for score in scores.values())
            or raw != max(names, key=scores.__getitem__)):
        raise ValueError("JEV result needs complete scores and their stable maximum selection")
    selected = scores[raw]
    margin = selected - max((value for name, value in scores.items() if name != raw), default=0)
    reasons = []
    if selected < minimum_entailment:
        reasons.append("entailment_below_threshold")
    if margin < minimum_margin:
        reasons.append("entailment_margin_below_threshold")
    eligible = next_step_rule(mapping.gate_state) if mapping.gate_state is not None else None
    if eligible is not None and raw != eligible:
        reasons.append("transition_not_eligible")
    return {"raw_choice": raw, "projected_output": {mapping.output_field: raw},
            "accepted_choice": None if reasons else raw,
            "disposition": "defer" if reasons else "accept_selection",
            "reasons": reasons, "selected_entailment": selected, "entailment_margin": margin,
            "minimum_entailment": minimum_entailment, "minimum_margin": minimum_margin,
            "threshold_basis": "uncalibrated_demo_heuristic",
            "rule_baseline": eligible, "remaining_fields": ["reason"] if mapping.requires_explanation else [],
            "prebound_arguments": deepcopy(mapping.tool_arguments.get(raw)),
            "action_executed": False}
