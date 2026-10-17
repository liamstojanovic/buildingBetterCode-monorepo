"""Offline advisor reports; recommendations never authorize execution."""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .contracts import Contract, utcnow

DecisionCategory = Literal["business_decision", "tool_selection", "next_step", "unknown"]


class DecisionDescriptor(Contract):
    """Optional hints at Task.metadata['advisor']['steps'][step_id].

    Hints are application-supplied analysis evidence, not trusted permissions or
    proof of quality. They never change the runtime/cache contracts.
    """

    category: DecisionCategory = "unknown"
    template_id: str | None = Field(default=None, min_length=1, max_length=200)
    template_version: str | None = Field(default=None, min_length=1, max_length=200)
    instance_fields: list[str] = Field(default_factory=list, max_length=64)
    choices: list[str] = Field(default_factory=list, max_length=128)
    output_field: str | None = Field(default=None, min_length=1, max_length=200)
    evidence_kind: Literal["native", "text", "structured", "unknown"] = "unknown"
    candidate_hypotheses: dict[str, str] = Field(default_factory=dict, max_length=128)
    tool_catalog_version: str | None = Field(default=None, min_length=1, max_length=200)
    state_schema_version: str | None = Field(default=None, min_length=1, max_length=200)
    argument_source: Literal["existing", "generated", "unknown"] = "unknown"
    explanation_required: bool = False
    deterministic_rule: bool = False
    generation_required: bool = False

    @model_validator(mode="after")
    def coherent_hints(self):
        if (self.template_id is None) != (self.template_version is None):
            raise ValueError("template_id and template_version must be supplied together")
        if self.instance_fields and self.template_id is None:
            raise ValueError("instance_fields require a declared template and version")
        reserved = {"choices", "candidates", "allowed_labels", "tools", "allowed_tools",
                    "next_steps", "allowed_next_steps", "output_schema", "instructions",
                    "decision_request", "prompt", "messages", "system", "system_prompt",
                    "developer", "developer_prompt"}
        if (len(set(self.instance_fields)) != len(self.instance_fields)
                or any(not item.strip() or len(item) > 200 or item in reserved
                       for item in self.instance_fields)):
            raise ValueError("instance_fields must be unique bounded input keys, excluding catalogs")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("choices must be unique")
        if any(not item.strip() or len(item) > 200 for item in self.choices):
            raise ValueError("choices must be nonempty strings of at most 200 characters")
        if any(not key.strip() or len(key) > 200 or not value.strip() or len(value) > 4000
               for key, value in self.candidate_hypotheses.items()):
            raise ValueError("candidate hypotheses must have bounded, nonempty names and text")
        if self.choices and set(self.candidate_hypotheses) - set(self.choices):
            raise ValueError("candidate hypotheses must refer to declared choices")
        return self


class AdvisorLimits(Contract):
    max_runs: int = Field(default=1000, ge=1, le=100000)
    max_groups: int = Field(default=200, ge=1, le=10000)
    max_samples_per_group: int = Field(default=8, ge=1, le=100)
    max_steps_per_run: int = Field(default=200, ge=1, le=10000)
    max_step_chars: int = Field(default=65536, ge=256, le=10000000)
    max_seconds: float = Field(default=30, gt=0, le=3600)
    rank_by: Literal["model_calls", "elapsed_ms", "cost_usd"] = "model_calls"


class AdvisorFinding(Contract):
    category: DecisionCategory = "unknown"
    status: Literal["candidate", "needs_mapping", "unsupported"]
    strategy: Literal["evaluate_openjev", "deterministic_function", "retain_llm", "retain_backend"]
    signals: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    choices: list[str] = Field(default_factory=list)
    partial_substitution: bool = False
    native_compatibility: Literal[
        "native_request_shape", "requires_text_adapter", "requires_mapping", "not_applicable"
    ] = "requires_mapping"


class AdvisorSample(Contract):
    run_id: str
    step_id: str
    status: str
    mode: Literal["baseline", "governed", "replay"]
    synthetic: bool
    selected_choice: str | None = None


class AdvisorMetrics(Contract):
    observations: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    blocked: int = Field(default=0, ge=0)
    reused: int = Field(default=0, ge=0)
    awaiting_approval: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    elapsed_ms: float | None = Field(default=None, ge=0)
    unknown_token_observations: int = Field(default=0, ge=0)
    unknown_cost_observations: int = Field(default=0, ge=0)
    unknown_elapsed_observations: int = Field(default=0, ge=0)
    synthetic_observations: int = Field(default=0, ge=0)


class AdvisorRecommendation(Contract):
    recommendation_id: str
    group_fingerprint: str
    workflow_id: str
    step_id: str
    handler: str
    handler_version: str
    model_fingerprint: str
    prompt_version: str
    grouping_basis: Literal["declared_template", "exact_inputs"]
    finding: AdvisorFinding
    observed: AdvisorMetrics
    samples: list[AdvisorSample]
    sample_limit_reached: bool = False
    evaluation_status: Literal["not_evaluated"] = "not_evaluated"
    estimated_savings_usd: None = None
    measured_quality: None = None


class AdvisorAnalysis(Contract):
    scanned_runs: int = 0
    scanned_model_steps: int = 0
    skipped_replay_runs: int = 0
    skipped_incomplete_runs: int = 0
    skipped_duplicate_runs: int = 0
    skipped_oversized_steps: int = 0
    skipped_group_observations: int = 0
    limit_reasons: list[str] = Field(default_factory=list)
    elapsed_ms: float = Field(default=0, ge=0)
    model_calls: Literal[0] = 0
    tool_calls: Literal[0] = 0
    input_tokens: Literal[0] = 0
    output_tokens: Literal[0] = 0
    cost_usd: None = None
    energy_j: None = None


class AdvisorReport(Contract):
    schema_version: Literal["1"] = "1"
    report_type: Literal["canopy.offline_advisor"] = "canopy.offline_advisor"
    analyzer_version: Literal["offline-advisor-v1"] = "offline-advisor-v1"
    generated_at: AwareDatetime = Field(default_factory=utcnow)
    tenant_id: str = Field(min_length=1)
    source: Literal["recording_store", "comparison_report", "python_records"]
    source_integrity: Literal["verified_recordings", "validated_import", "caller_supplied"]
    limits: AdvisorLimits
    analysis: AdvisorAnalysis
    recommendations: list[AdvisorRecommendation]
    limitations: list[str] = Field(default_factory=list)
