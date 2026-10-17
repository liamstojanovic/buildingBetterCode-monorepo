"""Local HTTP adapter with an explicitly trusted, fixed service principal.

Bind example services to 127.0.0.1. This factory does not authenticate HTTP
clients: everyone who can reach an instance acts as its configured principal.
Production integrations must authenticate and resolve that principal outside
this local adapter. Client bodies can never override its authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from .contracts import (
    BudgetLimits,
    Contract,
    Policy,
    RunConfig,
    RunEvent,
    RunResult,
    StepResult,
    Task,
)
from .recording import IncompleteRun, RecordingIntegrityError

if TYPE_CHECKING:
    from .governance import ApprovalRecord
    from .recording import RecordedRun
    from .runtime import Runtime


class ServiceContext(Contract):
    """Server-owned identity and execution limits, never accepted over HTTP."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    permissions: frozenset[str] = Field(default_factory=frozenset)
    policy: Policy = Field(default_factory=Policy)
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    model_fingerprint: str = "fixture-v1"
    prompt_version: str = "1"


class StartRunRequest(Contract):
    task: Task
    mode: Literal["baseline", "governed", "replay"] = "governed"
    cache_enabled: bool = True
    replay_run_id: str | None = Field(default=None, min_length=1)
    approval_ids: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_replay_source(self) -> StartRunRequest:
        if self.mode == "replay" and self.replay_run_id is None:
            raise ValueError("replay_run_id is required in replay mode")
        if self.mode != "replay" and self.replay_run_id is not None:
            raise ValueError("replay_run_id is only accepted in replay mode")
        return self


class ApprovalDecisionRequest(Contract):
    approved: bool
    reason: str | None = Field(default=None, max_length=4096)


class ApprovalDetail(Contract):
    approval_id: str
    binding: dict[str, Any]
    binding_digest: str
    status: Literal["pending", "approved", "denied"]
    requested_at: AwareDatetime
    expires_at: AwareDatetime
    reviewer_id: str | None
    decided_at: AwareDatetime | None
    reason: str | None
    consumed_at: AwareDatetime | None


def _approval_detail(record: ApprovalRecord) -> ApprovalDetail:
    return ApprovalDetail(
        approval_id=record.approval_id,
        binding=record.binding.to_dict(),
        binding_digest=record.binding.digest,
        status=record.status,
        requested_at=record.requested_at,
        expires_at=record.expires_at,
        reviewer_id=record.reviewer_id,
        decided_at=record.decided_at,
        reason=record.reason,
        consumed_at=record.consumed_at,
    )


class RunSummary(Contract):
    run_id: str
    task_id: str
    tenant_id: str
    mode: Literal["baseline", "governed", "replay"]
    status: Literal["running", "completed", "blocked", "awaiting_approval", "failed"]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class RunDetail(RunSummary):
    task: Task
    result: RunResult | None
    steps: list[StepResult]


def _summary(record: RecordedRun) -> RunSummary:
    return RunSummary(
        run_id=record.run_id,
        task_id=record.task.task_id,
        tenant_id=record.task.tenant_id,
        mode=record.config.mode,
        status=record.result.status if record.result is not None else "running",
        started_at=record.started_at,
        finished_at=record.result.finished_at if record.result is not None else None,
    )


def create_app(runtime: Runtime, *, context: ServiceContext) -> FastAPI:
    """Create a synchronous local API using a fixed trusted service context.

    ``runtime.approvals`` must separately have a reviewer_authorizer configured
    to recognize this server-owned ServiceContext before decisions can succeed.
    No headers, cookies, task metadata, or body fields grant privileges.
    """

    trusted = context.model_copy(deep=True)
    app = FastAPI(
        title="Canopy local execution API",
        version="1",
        description=(
            "Local demonstration API with a fixed server-configured principal. "
            "This API does not authenticate network clients; bind to 127.0.0.1."
        ),
    )
    app.state.runtime = runtime

    def get_record(run_id: str) -> RecordedRun:
        try:
            return runtime.store.get_run(trusted.tenant_id, run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @app.post("/runs", response_model=RunResult)
    def start_run(request: StartRunRequest) -> RunResult:
        if request.task.tenant_id != trusted.tenant_id or any(
            item.tenant_id != trusted.tenant_id for item in request.task.evidence
        ):
            raise HTTPException(status_code=403, detail="Task tenant does not match service tenant")
        config = RunConfig(
            mode=request.mode,
            actor_id=trusted.actor_id,
            permissions=sorted(trusted.permissions),
            policy=trusted.policy.model_copy(deep=True),
            budget=trusted.budget.model_copy(deep=True),
            model_fingerprint=trusted.model_fingerprint,
            prompt_version=trusted.prompt_version,
            cache_enabled=request.cache_enabled,
            replay_run_id=request.replay_run_id,
        )
        try:
            return runtime.run_task(
                request.task,
                config,
                tenant_id=trusted.tenant_id,
                approval_ids=request.approval_ids,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except IncompleteRun as exc:
            raise HTTPException(
                status_code=409, detail="Run is incomplete and cannot be replayed"
            ) from exc
        except RecordingIntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="Run recording failed integrity checks"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/runs", response_model=list[RunSummary])
    def list_runs(
        limit: int = Query(default=100, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[RunSummary]:
        return [
            _summary(record)
            for record in runtime.store.list_runs(trusted.tenant_id, limit=limit, offset=offset)
        ]

    @app.get("/runs/{run_id}", response_model=RunDetail)
    def run_detail(run_id: str) -> RunDetail:
        record = get_record(run_id)
        return RunDetail(
            **_summary(record).model_dump(),
            task=record.task,
            result=record.result,
            steps=list(record.steps),
        )

    @app.get("/runs/{run_id}/events", response_model=list[RunEvent])
    def run_events(
        run_id: str,
        after_sequence: int = Query(default=-1, ge=-1),
        limit: int = Query(default=1000, ge=1, le=1000),
    ) -> list[RunEvent]:
        get_record(run_id)
        return runtime.store.events(
            trusted.tenant_id, run_id, after_sequence=after_sequence, limit=limit
        )

    def get_approval(approval_id: str) -> ApprovalRecord:
        if runtime.approvals is None:
            raise HTTPException(status_code=503, detail="Approval service is not configured")
        record = runtime.approvals.get(approval_id, tenant_id=trusted.tenant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        return record

    @app.get("/approvals/{approval_id}", response_model=ApprovalDetail)
    def approval_detail(approval_id: str) -> ApprovalDetail:
        return _approval_detail(get_approval(approval_id))

    @app.post("/approvals/{approval_id}/decision", response_model=ApprovalDetail)
    def decide_approval(approval_id: str, request: ApprovalDecisionRequest) -> ApprovalDetail:
        if "approvals:decide" not in trusted.permissions:
            raise HTTPException(status_code=403, detail="Approval decision permission required")
        get_approval(approval_id)
        try:
            record = runtime.approvals.decide(
                approval_id,
                reviewer_context=trusted,
                approved=request.approved,
                reason=request.reason,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Approval reviewer is not authorized") from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Approval not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _approval_detail(record)

    return app
