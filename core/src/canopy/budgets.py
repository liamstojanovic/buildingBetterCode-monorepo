"""Pollard-backed budgets shared by all workers of one in-process run.

Reservations gate dispatch, not the execution of arbitrary provider code. Actual
overruns are retained, reported, and block future admission. See docs/budgets.md.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from types import MappingProxyType
from uuid import uuid4

from pollard import Budget, MemoryStore, Node, NodeKind, Store
from pollard.governor import check_budget, exhausted_after_settle
from pollard.meters import StepMeter, TokenMeter

from .contracts import BudgetLimits, Usage

_ZERO = Decimal(0)
_METRICS = ("tokens", "usd", "energy_j")


@dataclass(frozen=True)
class BudgetEstimate:
    """Nullable estimates; a zero is an affirmative estimate, not unknown."""

    tokens: int | None = None
    cost_usd: int | float | Decimal | None = None
    energy_j: int | float | Decimal | None = None
    elapsed_ms: int | float | Decimal | None = None

    def __post_init__(self) -> None:
        if self.tokens is not None and (
            isinstance(self.tokens, bool) or not isinstance(self.tokens, int) or self.tokens < 0
        ):
            raise ValueError("tokens must be a nonnegative integer or None")
        for name in ("cost_usd", "energy_j", "elapsed_ms"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
                    raise ValueError(f"{name} must be a finite nonnegative number or None")
                number = Decimal(str(value))
                if not number.is_finite() or number < 0:
                    raise ValueError(f"{name} must be a finite nonnegative number or None")

    @classmethod
    def from_usage(cls, usage: Usage) -> BudgetEstimate:
        """Keep omitted token/duration fields unknown instead of assuming zero."""
        usage = _validated_usage(usage)
        return cls(
            tokens=usage.input_tokens + usage.output_tokens if usage.tokens_known else None,
            cost_usd=usage.cost_usd,
            energy_j=usage.energy_j,
            elapsed_ms=usage.elapsed_ms if usage.elapsed_known else None,
        )

    def _charges(self) -> dict[str, Decimal]:
        values = {"tokens": self.tokens, "usd": self.cost_usd, "energy_j": self.energy_j}
        return {name: Decimal(str(value)) for name, value in values.items() if value is not None}


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    meter: str | None = None
    requested: Decimal | None = None
    remaining: Decimal | None = None
    detail: str = ""


class BudgetRefused(RuntimeError):
    """A typed pre-dispatch refusal; no reservation was granted."""

    def __init__(self, decision: BudgetDecision, *, trace_id: str | None = None) -> None:
        self.decision = decision
        self.reason = decision.reason
        self.meter = decision.meter
        self.trace_id = trace_id
        super().__init__(decision.detail or decision.reason)


@dataclass(frozen=True)
class BudgetAdmission:
    reservation_id: str
    step_id: str
    estimate: BudgetEstimate
    trace_id: str


@dataclass(frozen=True)
class BudgetSnapshot:
    usage: Usage
    spent: Mapping[str, Decimal]
    reserved: Mapping[str, Decimal]
    admitted_steps: int
    settled_steps: int
    pending_steps: int
    unknown_metrics: tuple[str, ...]
    exceeded: tuple[str, ...]


@dataclass(frozen=True)
class BudgetSettlement:
    ok: bool
    exceeded: tuple[str, ...]
    unknown_metrics: tuple[str, ...]
    reason: str | None
    trace_id: str
    snapshot: BudgetSnapshot


def _validated_usage(usage: Usage) -> Usage:
    if not isinstance(usage, Usage):
        raise TypeError("usage must be canopy.contracts.Usage")
    # Revalidate mutated/model_construct instances without losing field presence.
    return Usage.model_validate(usage.model_dump(exclude_unset=True))


class BudgetController:
    """Atomic admission and settlement for a shared, process-local run.

    All run workers must share this instance. Pollard supplies budget arithmetic,
    token/step meters and trace records; this adapter owns reservation locking,
    missing-measurement handling and a monotonic elapsed deadline.
    """

    def __init__(
        self,
        limits: BudgetLimits,
        *,
        run_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        store: Store | None = None,
    ) -> None:
        if not isinstance(limits, BudgetLimits):
            raise TypeError("limits must be canopy.contracts.BudgetLimits")
        self._limits = BudgetLimits.model_validate(limits.model_dump())
        self._clock = clock
        self._started = self._read_clock()
        self._last_clock = self._started
        self._lock = RLock()
        self._budget = Budget(
            tokens=self._limits.max_tokens,
            steps=self._limits.max_steps,
            usd=None if self._limits.max_cost_usd is None else str(self._limits.max_cost_usd),
            extra=(
                None if self._limits.max_energy_j is None
                else {"energy_j": Decimal(str(self._limits.max_energy_j))}
            ),
        )
        self._caps = self._budget.limits()
        self._spent: dict[str, Decimal] = {}
        self._unknown: set[str] = set()
        self._accounting_failed = False
        self._totals = Usage()
        self._pending: dict[str, BudgetAdmission] = {}
        self._settled: dict[str, tuple[BudgetAdmission, object, BudgetSettlement]] = {}
        self._step_meter = StepMeter()
        self._token_meter = TokenMeter()
        self.store = MemoryStore() if store is None else store
        root = Node.make(
            kind=NodeKind.ROOT,
            parent=None,
            payload={
                "canopy_budget": "1",
                "run_id": str(uuid4()) if run_id is None else run_id,
                "controller_id": str(uuid4()),
            },
            result={"limits": self._limits.model_dump(mode="json")},
        )
        self.store.put(root)
        self.trace_root_id = root.id

    def _read_clock(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError("clock must return finite monotonic seconds")
        return float(value)

    def _elapsed(self) -> float:
        now = self._read_clock()
        if now < self._last_clock:
            raise ValueError("clock moved backwards; elapsed enforcement unavailable")
        self._last_clock = now
        return (now - self._started) * 1000

    @staticmethod
    def _estimate(estimate: BudgetEstimate | Usage | None) -> BudgetEstimate:
        if estimate is None:
            return BudgetEstimate()
        if isinstance(estimate, Usage):
            return BudgetEstimate.from_usage(estimate)
        if not isinstance(estimate, BudgetEstimate):
            raise TypeError("estimate must be BudgetEstimate, Usage, or None")
        return estimate

    def _reserved(self) -> dict[str, Decimal]:
        reserved: dict[str, Decimal] = {}
        for admission in self._pending.values():
            for meter, value in self._estimate_charges(admission.estimate).items():
                reserved[meter] = reserved.get(meter, _ZERO) + value
        return reserved

    def _estimate_charges(self, estimate: BudgetEstimate) -> dict[str, Decimal]:
        charges = estimate._charges()
        charges["steps"] = Decimal(self._step_meter.precheck_estimate("tool_call", {}))
        return charges

    def _check(self, estimate: BudgetEstimate) -> BudgetDecision:
        elapsed = self._elapsed()
        if self._accounting_failed:
            return BudgetDecision(
                False, "accounting_unavailable",
                detail="a settlement trace write failed; this controller is closed to new dispatch",
            )
        if self._limits.max_elapsed_ms is not None:
            remaining = Decimal(str(self._limits.max_elapsed_ms)) - Decimal(str(elapsed))
            if remaining <= 0:
                return BudgetDecision(
                    False, "deadline_exceeded", "elapsed_ms", remaining=remaining,
                    detail="run elapsed deadline reached",
                )
        required_unknown = self._unknown.intersection(self._caps)
        if required_unknown:
            meter = sorted(required_unknown)[0]
            return BudgetDecision(
                False, "unknown_actual", meter,
                detail=f"prior {meter} usage is unmeasured; cannot enforce its cap",
            )
        charges = self._estimate_charges(estimate)
        for meter in self._caps:
            if meter not in charges:
                return BudgetDecision(
                    False, "unknown_estimate", meter,
                    detail=f"a known {meter} estimate is required by the configured cap",
                )
        if self._limits.max_elapsed_ms is not None:
            if estimate.elapsed_ms is None:
                return BudgetDecision(
                    False, "unknown_estimate", "elapsed_ms",
                    detail="a duration estimate is required by the configured deadline",
                )
            duration = Decimal(str(estimate.elapsed_ms))
            if duration > remaining:
                return BudgetDecision(
                    False, "limit_exceeded", "elapsed_ms", duration, remaining,
                    "estimated duration does not fit before the run deadline",
                )
        committed = dict(self._spent)
        for meter, value in self._reserved().items():
            committed[meter] = committed.get(meter, _ZERO) + value
        check = check_budget(
            budget=self._budget,
            spent=committed,
            estimates=charges,
            exhausted=exhausted_after_settle(budget=self._budget, spent=self._spent),
        )
        if not check.ok:
            return BudgetDecision(
                False, "limit_exceeded", check.meter, check.requested, check.remaining,
                f"{check.meter} budget has {check.remaining} remaining; requested {check.requested}",
            )
        return BudgetDecision(True, "allowed")

    def precheck(
        self, estimate: BudgetEstimate | Usage | None = None, *, step_id: str | None = None,
    ) -> BudgetDecision:
        """Return an advisory decision. This method does not reserve capacity."""
        del step_id  # Accepted for runtime call-site symmetry; no trace mutation.
        with self._lock:
            return self._check(self._estimate(estimate))

    def admit(
        self, step_id: str, estimate: BudgetEstimate | Usage | None = None,
    ) -> BudgetAdmission:
        """Atomically check and reserve one dispatch, or raise BudgetRefused."""
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError("step_id must be a nonempty string")
        estimate = self._estimate(estimate)
        with self._lock:
            decision = self._check(estimate)
            reservation_id = str(uuid4())
            node = Node.make(
                kind=NodeKind.NOTE if decision.allowed else NodeKind.REFUSAL,
                parent=self.trace_root_id,
                payload={"reservation_id": reservation_id, "step_id": step_id},
                result={
                    "phase": "admitted" if decision.allowed else "refused",
                    "reason": decision.reason,
                    "meter": decision.meter,
                    "requested": None if decision.requested is None else str(decision.requested),
                    "remaining": None if decision.remaining is None else str(decision.remaining),
                    "estimates": {
                        **{key: str(value) for key, value in estimate._charges().items()},
                        "elapsed_ms": (
                            None if estimate.elapsed_ms is None else str(estimate.elapsed_ms)
                        ),
                    },
                },
            )
            self.store.put(node)
            if not decision.allowed:
                raise BudgetRefused(decision, trace_id=node.id)
            admission = BudgetAdmission(reservation_id, step_id, estimate, node.id)
            self._pending[reservation_id] = admission
            return admission

    def _exceeded(self, spent: dict[str, Decimal], elapsed: float) -> tuple[str, ...]:
        exceeded = exhausted_after_settle(budget=self._budget, spent=spent)
        if self._limits.max_elapsed_ms is not None and elapsed > self._limits.max_elapsed_ms:
            exceeded.add("elapsed_ms")
        return tuple(sorted(exceeded))

    def settle(
        self, admission: BudgetAdmission | str, actual: Usage | None,
    ) -> BudgetSettlement:
        """Retain actual charges even on overrun; never replace them by estimates.

        Missing required measurements and overspend produce a non-ok receipt,
        not a post-dispatch exception. Repeating the same report is idempotent.
        """
        if not isinstance(admission, BudgetAdmission | str):
            raise TypeError("admission must be BudgetAdmission or its reservation_id")
        actual = None if actual is None else _validated_usage(actual)
        signature = None if actual is None else actual.model_dump(exclude_unset=True)
        reservation_id = admission if isinstance(admission, str) else admission.reservation_id
        with self._lock:
            prior = self._settled.get(reservation_id)
            if prior is not None:
                self._check_admission(admission, prior[0])
                if prior[1] != signature:
                    raise ValueError("conflicting actual usage for an already settled admission")
                return self._copy_settlement(prior[2])
            if reservation_id not in self._pending:
                raise ValueError("admission does not belong to this controller")
            issued = self._pending[reservation_id]
            self._check_admission(admission, issued)
            elapsed = self._elapsed()
            unknown = set(_METRICS)
            charges = {"steps": Decimal(self._step_meter.charge("tool_call", {}, None, {}))}
            if actual is not None:
                known = BudgetEstimate.from_usage(actual)._charges()
                unknown.difference_update(known)
                charges.update(known)
                # Partial token counts are a measured lower bound, never a complete total.
                charges["tokens"] = Decimal(self._token_meter.charge(
                    "tool_call", {}, {"usage": actual.model_dump()}, {},
                ))
            spent = dict(self._spent)
            for meter, value in charges.items():
                spent[meter] = spent.get(meter, _ZERO) + value
            aggregate_unknown = self._unknown | unknown
            exceeded = self._exceeded(spent, elapsed)
            missing_required = aggregate_unknown.intersection(self._caps)
            reason = "unknown_actual" if missing_required else "limit_exceeded" if exceeded else None
            node = Node.make(
                kind=NodeKind.NOTE,
                parent=issued.trace_id,
                payload={"reservation_id": reservation_id, "phase": "settled"},
                result={
                    "usage": None if actual is None else actual.model_dump(mode="json"),
                    "usage_fields": [] if actual is None else sorted(actual.model_fields_set),
                    "unknown_metrics": sorted(unknown),
                    "exceeded": list(exceeded),
                    "elapsed_ms": elapsed,
                    "reason": reason,
                    "charges_exact": {key: str(value) for key, value in charges.items()},
                },
                meta={"charges": {
                    key: int(value) if value == value.to_integral_value() else float(value)
                    for key, value in charges.items()
                }},
            )
            # The write outcome may be uncertain. Keep reservations, but never
            # admit more work against estimates after observing unrecorded actuals.
            try:
                self.store.put(node)
            except Exception:
                self._accounting_failed = True
                raise
            self._spent = spent
            self._unknown = aggregate_unknown
            if actual is not None:
                for field in (
                    "input_tokens", "output_tokens", "provider_cached_input_tokens",
                    "model_calls", "tool_calls",
                ):
                    setattr(self._totals, field, getattr(self._totals, field) + getattr(actual, field))
            del self._pending[reservation_id]
            snapshot = self._snapshot(elapsed, newly_settled=1)
            receipt = BudgetSettlement(
                reason is None, exceeded, tuple(sorted(aggregate_unknown)), reason, node.id, snapshot,
            )
            self._settled[reservation_id] = (issued, signature, receipt)
            return self._copy_settlement(receipt)

    @staticmethod
    def _check_admission(provided: BudgetAdmission | str, issued: BudgetAdmission) -> None:
        if isinstance(provided, BudgetAdmission) and provided != issued:
            raise ValueError("admission does not match the issued reservation")

    @staticmethod
    def _copy_settlement(receipt: BudgetSettlement) -> BudgetSettlement:
        # Usage is a mutable shared contract. Never expose the stored receipt's copy.
        from dataclasses import replace

        return replace(receipt, snapshot=replace(
            receipt.snapshot, usage=receipt.snapshot.usage.model_copy(deep=True),
        ))

    def _snapshot(self, elapsed: float, *, newly_settled: int = 0) -> BudgetSnapshot:
        usage = self._totals.model_copy(deep=True)
        usage.tokens_known = "tokens" not in self._unknown
        usage.cost_usd = None if "usd" in self._unknown else float(self._spent.get("usd", _ZERO))
        usage.energy_j = (
            None if "energy_j" in self._unknown else float(self._spent.get("energy_j", _ZERO))
        )
        usage.elapsed_ms = elapsed
        usage.elapsed_known = True
        settled = len(self._settled) + newly_settled
        return BudgetSnapshot(
            usage=usage,
            spent=MappingProxyType(dict(self._spent)),
            reserved=MappingProxyType(self._reserved()),
            admitted_steps=settled + len(self._pending),
            settled_steps=settled,
            pending_steps=len(self._pending),
            unknown_metrics=tuple(sorted(self._unknown)),
            exceeded=self._exceeded(self._spent, elapsed),
        )

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return self._snapshot(self._elapsed())

    @property
    def usage(self) -> Usage:
        return self.snapshot().usage
