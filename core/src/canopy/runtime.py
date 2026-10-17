"""Synchronous DAG execution using Canopy's real component adapters.

The Python caller is a trusted application boundary. HTTP clients use ``api``
which builds this configuration from service-owned identity and permissions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import TYPE_CHECKING, Any

from pollard import MemoryStore, Node, SQLiteStore, Store

from .budgets import BudgetController, BudgetRefused
from .cache import CacheIntegrityError, ResultCache
from .contracts import Evidence, HandlerResult, RunConfig, RunResult, StepResult, Task, Usage, utcnow
from .governance import ApprovalStore, ExecutionContext, PolicyGate
from .handlers import HandlerContext, HandlerRegistry, synthetic_registry
from .recording import ExecutionStore, canonical_json
from .temporal import (
    TemporalValidator,
    affected_steps,
    changed_evidence_ids,
    relevant_evidence_ids,
    validate_task,
)

if TYPE_CHECKING:
    from .drift import DriftMonitor


def _zero_usage() -> Usage:
    return Usage(input_tokens=0, output_tokens=0, cost_usd=0)


class Runtime:
    """Run validated tasks with explicit registry, durable recording and checks."""

    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        store: ExecutionStore,
        cache: ResultCache | None = None,
        approvals: ApprovalStore | None = None,
        drift: DriftMonitor | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.cache = cache
        self.approvals = approvals
        self.drift = drift
        self._memory_budget_trace = MemoryStore()

    def run_task(
        self,
        task: Task,
        config: RunConfig,
        *,
        tenant_id: str | None = None,
        approval_ids: Mapping[str, str] | None = None,
    ) -> RunResult:
        """Execute in dependency order; malformed requests raise ``ValueError``.

        Refusals, approval waits and handler failures are recorded run outcomes.
        No automatic retries are made, including for side effects with unknown
        outcomes. ``approval_ids`` supplies exact approved bindings by step ID.
        """
        with self._budget_storage() as budget_store:
            return self._execute(
                task, config, tenant_id=tenant_id, approval_ids=approval_ids,
                budget_store=budget_store,
            )

    @contextmanager
    def _budget_storage(self) -> Iterator[Store]:
        # Pollard SQLite connections are thread-affine: create them on each
        # request's own thread, and always close them before returning.
        if self.store.trace_path is None:
            yield self._memory_budget_trace
        else:
            with SQLiteStore(self.store.trace_path) as trace:
                yield trace

    def budget_trace(self, tenant_id: str, run_id: str) -> list[Node]:
        """Read the actual persisted Pollard budget nodes, scoped through a run."""
        events = self.store.events(tenant_id, run_id)
        root = next(
            (item.payload["budget_trace_id"] for item in events
             if item.event_type == "runtime_started"), None,
        )
        if root is None:
            return []
        with self._budget_storage() as trace:
            return list(trace.walk(root))

    def _execute(
        self, task: Task, config: RunConfig, *, tenant_id: str | None,
        approval_ids: Mapping[str, str] | None, budget_store: Store,
    ) -> RunResult:
        if not isinstance(task, Task) or not isinstance(config, RunConfig):
            raise TypeError("task and config must be Task and RunConfig instances")
        task, config = task.model_copy(deep=True), config.model_copy(deep=True)
        order = validate_task(task)
        canonical_json(task)
        canonical_json(config)
        if tenant_id is not None and task.tenant_id != tenant_id:
            raise ValueError("task tenant does not match the trusted execution context")
        if not task.tenant_id or not config.actor_id:
            raise ValueError("tenant_id and actor_id must be nonempty")
        approval_ids = dict(approval_ids or {})
        if set(approval_ids) - set(order):
            raise ValueError("approval_ids contains unknown step IDs")
        if config.mode == "replay":
            return self._replay(task, config)
        if config.replay_run_id is not None:
            raise ValueError("replay_run_id is valid only in replay mode")
        if self.drift is not None and self.drift.tenant_id != task.tenant_id:
            raise ValueError("drift monitor tenant does not match the task tenant")

        steps = {step.step_id: step for step in task.steps}
        registrations = {
            step_id: self.registry.resolve(steps[step_id].handler, steps[step_id].handler_version)
            for step_id in order
        }
        for step_id, registration in registrations.items():
            if registration.kind != steps[step_id].kind:
                raise ValueError(f"step {step_id!r} kind disagrees with registered handler")
            if registration.side_effect != steps[step_id].side_effect:
                raise ValueError(f"step {step_id!r} side_effect disagrees with registered handler")

        started_at, clock_start = utcnow(), perf_counter()
        invalidated = self._invalidated_steps(task) if config.mode == "governed" else frozenset()
        run_id = self.store.create_run(task, config, started_at=started_at)
        budgets = BudgetController(config.budget, run_id=run_id, store=budget_store)
        gate = PolicyGate(self.approvals)
        temporal = TemporalValidator(threshold=config.policy.freshness_threshold)
        context = ExecutionContext(
            tenant_id=task.tenant_id,
            actor_id=config.actor_id,
            permissions=frozenset(config.permissions),
        )
        results: dict[str, StepResult] = {}

        def event(event_type: str, step_id: str | None = None, **payload: Any) -> None:
            self.store.append_event(
                task.tenant_id, run_id, event_type, step_id=step_id, payload=payload
            )

        def record(result: StepResult) -> None:
            self.store.record_step(task.tenant_id, run_id, result)
            results[result.step_id] = result

        event("runtime_started", mode=config.mode, budget_trace_id=budgets.trace_root_id)
        if invalidated:
            event("evidence_invalidated", step_ids=sorted(invalidated))
        for step_id in order:
            step = steps[step_id]
            registration = registrations[step_id]
            failed_dependencies = [
                dependency
                for dependency in step.depends_on
                if results[dependency].status not in {"completed", "reused"}
            ]
            if failed_dependencies:
                reason = f"dependencies did not complete: {', '.join(failed_dependencies)}"
                record(StepResult(step_id=step_id, status="blocked", reason=reason))
                event("step_blocked", step_id, reason=reason)
                continue
            dependencies = {dependency: results[dependency].output for dependency in step.depends_on}
            evidence_ids = relevant_evidence_ids(task, step_id)
            evidence = [item for item in task.evidence if item.evidence_id in evidence_ids]
            if config.mode == "governed":
                freshness = temporal.validate_step(task, step_id)
                failures = [decision for decision in freshness if not decision.valid]
                event(
                    "evidence_checked", step_id,
                    evidence=[{
                        "evidence_id": item.evidence_id,
                        "valid": item.valid,
                        "status": item.status,
                        "reason": item.reason,
                    } for item in freshness],
                )
                if failures:
                    reason = "; ".join(item.reason for item in failures)
                    record(StepResult(step_id=step_id, status="blocked", reason=reason))
                    event("step_blocked", step_id, reason=reason)
                    continue

            hit = None
            if (self.cache is not None and registration.cacheable
                    and config.mode == "governed" and config.cache_enabled):
                try:
                    hit = self.cache.lookup(task, step, config, dependencies)
                except CacheIntegrityError:
                    reason = "cache integrity verification failed; dispatch refused"
                    record(StepResult(step_id=step_id, status="failed", reason=reason))
                    event("step_failed", step_id, reason=reason)
                    continue
            # The policy proposal binds the actual handler arguments and trusted
            # execution revisions together. Neither namespace can be supplied or
            # overwritten by client argument keys. Policy's recursive amount
            # checks still inspect every original argument.
            policy_step = step.model_copy(update={"inputs": {
                "handler_inputs": step.inputs,
                "runtime_context": {
                    "model_fingerprint": config.model_fingerprint,
                    "prompt_version": config.prompt_version,
                    "permissions": sorted(set(config.permissions)),
                    "mode": config.mode,
                },
            }})
            decision = gate.evaluate(
                policy_step, context=context, policy=config.policy, evidence=evidence,
                dependency_outputs=dependencies, approval_id=approval_ids.get(step_id),
                drift=self._drift_report(config),
            )
            event("policy_checked", step_id, status=decision.status, reason=decision.reason)
            if not decision.allowed:
                if decision.status == "approval_required":
                    approval_id = None
                    if self.approvals is not None:
                        approval = self.approvals.request(decision.binding)
                        approval_id = approval.approval_id
                    record(StepResult(
                        step_id=step_id, status="awaiting_approval", reason=decision.reason
                    ))
                    event(
                        "approval_required", step_id,
                        approval_id=approval_id, reason=decision.reason,
                    )
                else:
                    record(StepResult(step_id=step_id, status="blocked", reason=decision.reason))
                    event("step_blocked", step_id, reason=decision.reason)
                continue
            if hit is not None:
                record(hit.as_step_result(step_id).model_copy(update={"usage": _zero_usage()}))
                event(
                    "step_reused", step_id, cache_key=hit.key, source_run_id=hit.source_run_id,
                    historical_usage=hit.historical_usage.model_dump(mode="json"),
                )
                continue
            try:
                admission = budgets.admit(step_id, step.estimated_usage)
            except BudgetRefused as exc:
                reason = str(exc)
                record(StepResult(step_id=step_id, status="blocked", reason=reason))
                event("budget_refused", step_id, reason=reason, meter=exc.decision.meter)
                continue
            event("budget_admitted", step_id, trace_id=admission.trace_id)

            if decision.approval_id is not None:
                try:
                    if self.approvals is None:
                        raise ValueError("approval store is unavailable")
                    self.approvals.consume(decision.approval_id, decision.binding)
                except (ValueError, PermissionError) as exc:
                    budgets.settle(admission, _zero_usage())
                    reason = f"approval claim refused: {exc}"
                    record(StepResult(step_id=step_id, status="blocked", reason=reason))
                    event("step_blocked", step_id, reason=reason)
                    continue
            if not self.store.claim_step(task.tenant_id, run_id, step_id):
                budgets.settle(admission, _zero_usage())
                reason = "step was already claimed; dispatch outcome may be unknown"
                record(StepResult(step_id=step_id, status="blocked", reason=reason))
                event("step_blocked", step_id, reason=reason)
                continue
            event("step_dispatched", step_id, handler=step.handler, synthetic=registration.synthetic)
            dispatch_started = perf_counter()
            returned = None
            measured = Usage()
            try:
                returned = registration.handler(HandlerContext(
                    run_id=run_id, task=task.model_copy(deep=True), step=step.model_copy(deep=True),
                    config=config.model_copy(deep=True), inputs=deepcopy(step.inputs),
                    dependencies=deepcopy(dependencies),
                ))
                if not isinstance(returned, HandlerResult):
                    raise TypeError("registered handlers must return HandlerResult")
                measured = Usage.model_validate(returned.usage.model_dump(exclude_unset=True))
                # Dispatch and reuse must expose the same JSON value types to
                # dependents (for example aware timestamps normalize to strings).
                output = json.loads(canonical_json(returned.output))
                reason, status = None, "completed"
            except Exception as exc:
                output, status = None, "failed"
                reason = f"handler failed ({type(exc).__name__}): {exc}"
                if step.side_effect:
                    reason += "; external side-effect outcome may be unknown; no automatic retry"
            usage = measured.model_copy(update={
                "model_calls": max(measured.model_calls, int(step.kind == "model")),
                "tool_calls": max(measured.tool_calls, int(step.kind == "tool")),
                "elapsed_ms": (perf_counter() - dispatch_started) * 1000,
                "elapsed_known": True,
            })
            settlement = budgets.settle(admission, usage)
            if not settlement.ok:
                status = "failed"
                reason = "; ".join(item for item in (reason, settlement.reason) if item)
            event(
                "budget_settled", step_id, ok=settlement.ok, reason=settlement.reason,
                usage=usage.model_dump(mode="json"), trace_id=settlement.trace_id,
                unknown_metrics=list(settlement.unknown_metrics),
            )
            result = StepResult(
                step_id=step_id, status=status, output=output, reason=reason, usage=usage
            )
            if status == "completed" and self.cache is not None and registration.cacheable:
                try:
                    cached = self.cache.put(
                        task, step, config, dependencies, result, source_run_id=run_id
                    )
                    if cached is not None:
                        result = result.model_copy(update={"cache_key": cached.key})
                except CacheIntegrityError:
                    status = "failed"
                    reason = "cache persistence integrity check failed after dispatch"
                    result = result.model_copy(update={"status": status, "reason": reason})
            record(result)
            event("step_completed" if status == "completed" else "step_failed", step_id, reason=reason)

        statuses = {result.status for result in results.values()}
        status = next(
            (item for item in ("failed", "awaiting_approval", "blocked") if item in statuses),
            "completed",
        )
        reasons = [result.reason for result in results.values() if result.reason]
        usage = budgets.usage.model_copy(update={
            "elapsed_ms": (perf_counter() - clock_start) * 1000, "elapsed_known": True,
        })
        if not (usage.model_calls or usage.tool_calls):
            usage.energy_j = None  # Local cache/inspection energy has not been measured.
        result = RunResult(
            run_id=run_id, task_id=task.task_id, tenant_id=task.tenant_id, mode=config.mode,
            status=status, started_at=started_at, finished_at=utcnow(),
            output={key: item.output for key, item in results.items()
                    if item.status in {"completed", "reused"}},
            steps=list(results.values()),
            usage=usage,
            policy_version=config.policy.version,
            evidence_versions={item.evidence_id: item.version for item in task.evidence},
            reused_steps=sum(item.status == "reused" for item in results.values()),
            invalidated_steps=len(invalidated),
            synthetic=any(item.synthetic for item in registrations.values()),
            reason="; ".join(reasons) if reasons else None,
        )
        return self.store.finish_run(result)

    def _drift_report(self, config: RunConfig) -> dict[str, Any] | None:
        """Use current monitoring evidence without making unbudgeted probe calls."""
        if self.drift is None:
            return None
        latest = self.drift.latest()
        if latest is None:
            return {
                "status": "unknown", "containment": "review", "known_violations": [],
                "tenant_id": self.drift.tenant_id, "policy_version": self.drift.policy_version,
                "reason": "no drift observations have been recorded",
            }
        report = asdict(latest)
        if (latest.provider_fingerprint != config.model_fingerprint
                and latest.containment != "deny"):
            report.update(
                status="unknown", containment="review",
                reason="latest drift observations do not cover this provider fingerprint",
            )
        return report

    def _invalidated_steps(self, task: Task) -> frozenset[str]:
        """Explain evidence-driven misses against the latest successful workflow."""
        for previous in self.store.list_runs(task.tenant_id, limit=1000):
            if (previous.task.workflow_id != task.workflow_id or previous.result is None
                    or previous.result.mode != "governed" or previous.result.status != "completed"):
                continue
            # Normalize typed defaults (quality=1 versus reloaded 1.0) while
            # retaining strict JSON distinctions inside untyped evidence data.
            changed = changed_evidence_ids(
                [Evidence.model_validate(item.model_dump()) for item in previous.task.evidence],
                [Evidence.model_validate(item.model_dump()) for item in task.evidence],
            )
            return affected_steps(task, changed_evidence_ids=changed)
        return frozenset()

    def _replay(self, task: Task, config: RunConfig) -> RunResult:
        if not config.replay_run_id:
            raise ValueError("replay mode requires replay_run_id")
        source = self.store.get_run(task.tenant_id, config.replay_run_id)
        if canonical_json(task.model_copy(update={"task_id": source.task.task_id})) != canonical_json(
            source.task
        ):
            raise ValueError("replay task must match the recorded task except for task_id")
        historical = self.store.replay(task.tenant_id, config.replay_run_id, task_id=task.task_id)
        historical = historical.model_copy(update={
            "usage": _zero_usage(),
            "steps": [item.model_copy(update={"usage": _zero_usage()}) for item in historical.steps],
        })
        # Inspection does not establish conformance with a newly supplied policy.
        replay_config = config.model_copy(update={"policy": source.config.policy.model_copy(deep=True)})
        self.store.create_run(
            task, replay_config, run_id=historical.run_id, started_at=historical.started_at,
        )
        self.store.append_event(
            task.tenant_id, historical.run_id, "replay_loaded",
            payload={"source_run_id": config.replay_run_id,
                     "historical_policy_version": source.result.policy_version,
                     "inspection_policy_version": config.policy.version,
                     "historical_usage": source.result.usage.model_dump(mode="json")},
        )
        return self.store.finish_run(historical)

    def close(self) -> None:
        """Close the owned stores after callers have finished all requests."""
        self.store.close()
        if self.cache is not None:
            self.cache.close()
        if self.approvals is not None:
            self.approvals.close()


def create_runtime(
    db_path: str = ".canopy/canopy.sqlite3",
    *,
    registry: HandlerRegistry | None = None,
    reviewer_authorizer: Callable[[Any], Any] | None = None,
) -> Runtime:
    """Create durable local adapters. Default handlers are explicitly synthetic."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return Runtime(
        registry=registry if registry is not None else synthetic_registry(),
        store=ExecutionStore(db_path), cache=ResultCache(db_path),
        approvals=ApprovalStore(db_path, reviewer_authorizer=reviewer_authorizer),
    )


_default_runtime: Runtime | None = None
_default_lock = Lock()


def run_task(
    task: Task, config: RunConfig, *, runtime: Runtime | None = None,
    approval_ids: Mapping[str, str] | None = None,
) -> RunResult:
    """Benchmark-friendly adapter; inject a runtime to use custom handlers/stores."""
    global _default_runtime
    if runtime is None:
        with _default_lock:
            if _default_runtime is None:
                _default_runtime = create_runtime()
            runtime = _default_runtime
    return runtime.run_task(task, config, approval_ids=approval_ids)
