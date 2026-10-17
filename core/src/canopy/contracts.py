"""Versioned boundary shared by the runtime, benchmark adapter and frontend.

Timestamps are timezone-aware UTC. Monetary usage is in USD; business money should
remain integer minor units in task payloads. Unknown measurements stay null.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Evidence(Contract):
    evidence_id: str
    tenant_id: str
    source: str
    version: str
    observed_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    half_life_seconds: float | None = Field(default=None, gt=0)
    quality: float = Field(default=1, ge=0, le=1)
    data: dict[str, Any] = Field(default_factory=dict)


class BudgetLimits(Contract):
    max_tokens: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_elapsed_ms: float | None = Field(default=None, ge=0)
    max_energy_j: float | None = Field(default=None, ge=0)


class Usage(Contract):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    provider_cached_input_tokens: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    energy_j: float | None = Field(default=None, ge=0)
    elapsed_ms: float = Field(default=0, ge=0)
    tokens_known: bool = False
    elapsed_known: bool = False

    @model_validator(mode="before")
    @classmethod
    def retain_token_measurement_completeness(cls, value: Any) -> Any:
        """Preserve omitted-vs-measured tokens across a JSON round trip."""
        if isinstance(value, dict):
            value = dict(value)
            value.setdefault(
                "tokens_known", "input_tokens" in value and "output_tokens" in value
            )
            value.setdefault("elapsed_known", "elapsed_ms" in value)
        return value


class Policy(Contract):
    version: str = "1"
    allowed_tools: list[str] = Field(default_factory=list)
    approval_required_tools: list[str] = Field(default_factory=list)
    max_amount_minor: int | None = Field(default=None, ge=0)
    freshness_threshold: float = Field(default=0.5, ge=0, le=1)
    drift_action: Literal["allow", "review", "deny"] = "review"


class Step(Contract):
    step_id: str
    kind: Literal["model", "tool"]
    handler: str
    handler_version: str = "1"
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    side_effect: bool = False
    estimated_usage: Usage = Field(default_factory=Usage)


class Task(Contract):
    schema_version: Literal["1"] = "1"
    task_id: str
    tenant_id: str
    workflow_id: str
    steps: list[Step]
    evidence: list[Evidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunConfig(Contract):
    mode: Literal["baseline", "governed", "replay"] = "governed"
    actor_id: str = "demo-operator"
    permissions: list[str] = Field(default_factory=list)
    policy: Policy = Field(default_factory=Policy)
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    model_fingerprint: str = "fixture-v1"
    prompt_version: str = "1"
    cache_enabled: bool = True
    replay_run_id: str | None = None


class HandlerResult(Contract):
    output: Any
    usage: Usage = Field(default_factory=Usage)


class StepResult(Contract):
    step_id: str
    status: Literal["completed", "reused", "blocked", "awaiting_approval", "failed"]
    output: Any = None
    reason: str | None = None
    cache_key: str | None = None
    source_run_id: str | None = None
    usage: Usage = Field(default_factory=Usage)


class RunEvent(Contract):
    schema_version: Literal["1"] = "1"
    run_id: str
    sequence: int = Field(ge=0)
    timestamp: AwareDatetime = Field(default_factory=utcnow)
    event_type: str
    step_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RunResult(Contract):
    schema_version: Literal["1"] = "1"
    run_id: str
    task_id: str
    tenant_id: str
    mode: Literal["baseline", "governed", "replay"]
    status: Literal["completed", "blocked", "awaiting_approval", "failed"]
    started_at: AwareDatetime
    finished_at: AwareDatetime
    output: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    policy_version: str
    evidence_versions: dict[str, str] = Field(default_factory=dict)
    reused_steps: int = Field(default=0, ge=0)
    invalidated_steps: int = Field(default=0, ge=0)
    replay_source_run_id: str | None = None
    synthetic: bool = False
    reason: str | None = None
