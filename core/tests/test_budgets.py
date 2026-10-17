"""Synthetic measurements exercise the real installed Pollard implementation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from threading import Barrier

import pytest
from pollard import MemoryStore, NodeKind, recompute_charges, verify
from pydantic import ValidationError

from canopy.budgets import BudgetController, BudgetEstimate, BudgetRefused
from canopy.contracts import BudgetLimits, Usage


class Clock:
    """Explicit synthetic monotonic clock; no timing-sensitive sleeps."""

    def __init__(self):
        self.seconds = 0.0

    def __call__(self):
        return self.seconds

    def advance_ms(self, milliseconds):
        self.seconds += milliseconds / 1000


def measured(tokens=0, *, cost=0.0, energy=0.0, **kwargs):
    return Usage(input_tokens=tokens, output_tokens=0, cost_usd=cost, energy_j=energy, **kwargs)


@pytest.mark.parametrize(
    ("limits", "estimate", "meter"),
    [
        (BudgetLimits(max_tokens=10), BudgetEstimate(tokens=11), "tokens"),
        (BudgetLimits(max_cost_usd=0.3), BudgetEstimate(cost_usd=0.31), "usd"),
        (BudgetLimits(max_energy_j=5), BudgetEstimate(energy_j=5.01), "energy_j"),
        (BudgetLimits(max_steps=0), BudgetEstimate(), "steps"),
    ],
)
def test_refusal_has_reason_and_verifiable_pollard_trace(limits, estimate, meter):
    controller = BudgetController(limits)
    decision = controller.precheck(estimate)
    assert not decision.allowed
    assert decision.reason == "limit_exceeded"
    assert decision.meter == meter
    with pytest.raises(BudgetRefused) as caught:
        controller.admit("too-large", estimate)
    assert caught.value.decision == decision
    assert controller.store.get(caught.value.trace_id).kind == NodeKind.REFUSAL
    assert controller.snapshot().admitted_steps == 0
    assert verify(controller.store, controller.trace_root_id).ok


def test_exact_caps_and_cached_tokens_are_not_counted_twice():
    controller = BudgetController(BudgetLimits(
        max_tokens=10, max_steps=1, max_cost_usd=0.3, max_energy_j=2,
    ))
    admission = controller.admit("exact", BudgetEstimate(tokens=10, cost_usd=0.3, energy_j=2))
    actual = Usage(
        input_tokens=7, output_tokens=3, provider_cached_input_tokens=5,
        cost_usd=0.3, energy_j=2, model_calls=1,
    )
    receipt = controller.settle(admission, actual)
    assert receipt.ok
    assert receipt.exceeded == ()
    assert receipt.snapshot.spent == {
        "tokens": Decimal(10), "steps": Decimal(1), "usd": Decimal("0.3"), "energy_j": Decimal(2),
    }
    assert receipt.snapshot.usage.provider_cached_input_tokens == 5
    assert receipt.snapshot.usage.model_calls == 1
    assert recompute_charges(controller.store, controller.trace_root_id) == {
        "tokens": 10.0, "steps": 1.0, "usd": 0.3, "energy_j": 2.0,
    }
    assert verify(controller.store, controller.trace_root_id).ok


@pytest.mark.parametrize(
    ("limits", "estimate", "meter"),
    [
        (BudgetLimits(max_tokens=0), None, "tokens"),
        (BudgetLimits(max_tokens=1), Usage(), "tokens"),
        (BudgetLimits(max_tokens=1), Usage(input_tokens=0), "tokens"),
        (BudgetLimits(max_cost_usd=0), BudgetEstimate(tokens=0), "usd"),
        (BudgetLimits(max_energy_j=0), BudgetEstimate(tokens=0), "energy_j"),
        (BudgetLimits(max_elapsed_ms=10), BudgetEstimate(tokens=0), "elapsed_ms"),
    ],
)
def test_unknown_required_estimates_fail_closed(limits, estimate, meter):
    controller = BudgetController(limits, clock=Clock())
    decision = controller.precheck(estimate)
    assert (decision.allowed, decision.reason, decision.meter) == (False, "unknown_estimate", meter)
    with pytest.raises(BudgetRefused):
        controller.admit("unknown", estimate)


def test_explicit_zero_usage_is_distinct_from_omission():
    controller = BudgetController(BudgetLimits(max_tokens=0))
    assert controller.precheck(Usage(input_tokens=0, output_tokens=0)).allowed
    estimate = BudgetEstimate.from_usage(Usage(input_tokens=0, output_tokens=0, elapsed_ms=0))
    assert estimate == BudgetEstimate(tokens=0, elapsed_ms=0)


def test_token_measurement_completeness_survives_serialization():
    controller = BudgetController(BudgetLimits(max_tokens=10))
    unknown = Usage.model_validate_json(Usage().model_dump_json())
    assert not controller.precheck(unknown).allowed
    explicit_unknown = Usage(input_tokens=4, output_tokens=0, tokens_known=False)
    assert not controller.precheck(explicit_unknown).allowed
    known = Usage.model_validate_json(measured(3).model_dump_json())
    admission = controller.admit("known", known)
    assert controller.settle(admission, known).snapshot.usage.tokens_known
    second = controller.admit("unknown-actual", BudgetEstimate(tokens=0))
    receipt = controller.settle(second, unknown)
    assert not receipt.ok
    assert not receipt.snapshot.usage.tokens_known


def test_unknown_uncapped_estimates_allowed_and_unknown_actuals_remain_null():
    controller = BudgetController(BudgetLimits(max_steps=2))
    first = controller.admit("first")
    receipt = controller.settle(first, Usage(input_tokens=3, output_tokens=1))
    assert receipt.ok
    assert receipt.unknown_metrics == ("energy_j", "usd")
    assert controller.usage.cost_usd is None
    assert controller.usage.energy_j is None
    second = controller.admit("second")
    controller.settle(second, measured(2, cost=0.2, energy=4))
    assert controller.usage.cost_usd is None
    assert controller.usage.energy_j is None
    assert controller.snapshot().spent["usd"] == Decimal("0.2")


def test_admission_counts_pending_and_settlement_releases_unused_estimate():
    controller = BudgetController(BudgetLimits(max_tokens=10))
    assert controller.precheck(BudgetEstimate(tokens=10)).allowed
    first = controller.admit("first", BudgetEstimate(tokens=8))
    snapshot = controller.snapshot()
    assert snapshot.reserved == {"steps": Decimal(1), "tokens": Decimal(8)}
    with pytest.raises(BudgetRefused):
        controller.admit("second", BudgetEstimate(tokens=3))
    controller.settle(first, measured(4))
    second = controller.admit("second", BudgetEstimate(tokens=6))
    assert controller.snapshot().reserved["tokens"] == 6
    controller.settle(second, measured(6))
    snapshot = controller.snapshot()
    assert (snapshot.admitted_steps, snapshot.settled_steps, snapshot.pending_steps) == (2, 2, 0)
    assert snapshot.spent["tokens"] == 10


def test_simultaneous_admission_reserves_atomically():
    controller = BudgetController(BudgetLimits(max_tokens=10, max_steps=5))
    barrier = Barrier(12)

    def attempt(index):
        barrier.wait(timeout=10)
        try:
            return controller.admit(str(index), BudgetEstimate(tokens=2))
        except BudgetRefused:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(12)))
        admissions = [result for result in results if result is not None]
        assert len(admissions) == 5
        assert controller.snapshot().reserved == {"steps": Decimal(5), "tokens": Decimal(10)}
        receipts = list(pool.map(lambda item: controller.settle(item, measured(2)), admissions))
    assert all(receipt.ok for receipt in receipts)
    assert controller.snapshot().spent["tokens"] == 10
    assert controller.snapshot().spent["steps"] == 5
    assert verify(controller.store, controller.trace_root_id).ok


@pytest.mark.parametrize(
    ("limits", "estimate", "actual", "meter"),
    [
        (BudgetLimits(max_tokens=5), BudgetEstimate(tokens=2), measured(7), "tokens"),
        (BudgetLimits(max_cost_usd=0.3), BudgetEstimate(cost_usd=0.1), measured(cost=0.4), "usd"),
        (BudgetLimits(max_energy_j=2), BudgetEstimate(energy_j=1), measured(energy=3), "energy_j"),
    ],
)
def test_overspend_is_retained_and_future_admission_contained(limits, estimate, actual, meter):
    controller = BudgetController(limits)
    first = controller.admit("first", estimate)
    second = controller.admit("already-running", estimate)
    receipt = controller.settle(first, actual)
    assert not receipt.ok
    assert receipt.reason == "limit_exceeded"
    assert receipt.exceeded == (meter,)
    assert receipt.snapshot.spent[meter] > controller._budget.limits()[meter]
    with pytest.raises(BudgetRefused) as caught:
        controller.admit("later", BudgetEstimate(tokens=0, cost_usd=0, energy_j=0))
    assert caught.value.reason == "limit_exceeded"
    # Settling work admitted before the overrun must still record its actual usage.
    assert not controller.settle(second, actual).ok
    assert controller.snapshot().settled_steps == 2
    assert controller.snapshot().spent[meter] == receipt.snapshot.spent[meter] * 2


@pytest.mark.parametrize(
    ("limits", "estimate", "actual", "meter"),
    [
        (BudgetLimits(max_tokens=10), BudgetEstimate(tokens=2), Usage(input_tokens=3), "tokens"),
        (BudgetLimits(max_cost_usd=1), BudgetEstimate(cost_usd=0.1), Usage(), "usd"),
        (BudgetLimits(max_energy_j=1), BudgetEstimate(energy_j=0.1), None, "energy_j"),
    ],
)
def test_missing_capped_actual_blocks_future_dispatch(limits, estimate, actual, meter):
    controller = BudgetController(limits)
    receipt = controller.settle(controller.admit("first", estimate), actual)
    assert not receipt.ok
    assert receipt.reason == "unknown_actual"
    assert meter in receipt.unknown_metrics
    assert controller.snapshot().spent["steps"] == 1
    decision = controller.precheck(BudgetEstimate(tokens=0, cost_usd=0, energy_j=0))
    assert (decision.reason, decision.meter) == ("unknown_actual", meter)


def test_partial_token_actual_is_preserved_as_lower_bound():
    controller = BudgetController(BudgetLimits(max_tokens=10))
    controller.settle(controller.admit("partial", BudgetEstimate(tokens=3)), Usage(input_tokens=4))
    assert controller.snapshot().spent["tokens"] == 4
    assert controller.usage.input_tokens == 4
    assert not controller.usage.tokens_known
    assert "tokens" in controller.snapshot().unknown_metrics


def test_decimal_accounting_avoids_binary_float_sum_refusal():
    controller = BudgetController(BudgetLimits(max_cost_usd=0.3))
    controller.settle(controller.admit("a", BudgetEstimate(cost_usd=0.1)), measured(cost=0.1))
    second = controller.admit("b", BudgetEstimate(cost_usd=0.2))
    assert controller.settle(second, measured(cost=0.2)).ok
    assert controller.snapshot().spent["usd"] == Decimal("0.3")


def test_deadline_includes_idle_time_and_checks_estimated_completion():
    clock = Clock()
    controller = BudgetController(BudgetLimits(max_elapsed_ms=100), clock=clock)
    clock.advance_ms(40)
    decision = controller.precheck(BudgetEstimate(elapsed_ms=61))
    assert decision.reason == "limit_exceeded"
    assert decision.meter == "elapsed_ms"
    assert controller.precheck(BudgetEstimate(elapsed_ms=60)).allowed
    clock.advance_ms(60)
    assert controller.precheck(BudgetEstimate(elapsed_ms=0)).reason == "deadline_exceeded"


def test_parallel_durations_are_not_summed_and_caller_elapsed_is_not_authoritative():
    clock = Clock()
    controller = BudgetController(BudgetLimits(max_elapsed_ms=100), clock=clock)
    first = controller.admit("first", BudgetEstimate(elapsed_ms=80))
    second = controller.admit("second", BudgetEstimate(elapsed_ms=80))
    clock.advance_ms(80)
    assert controller.settle(first, measured(elapsed_ms=9000)).ok
    assert controller.settle(second, measured(elapsed_ms=9000)).ok
    assert controller.usage.elapsed_ms == 80


def test_overrun_is_detected_after_execution_without_fictional_cancellation():
    clock = Clock()
    controller = BudgetController(BudgetLimits(max_elapsed_ms=100), clock=clock)
    admission = controller.admit("slow", BudgetEstimate(elapsed_ms=10))
    clock.advance_ms(150)
    receipt = controller.settle(admission, measured(7))
    assert not receipt.ok
    assert receipt.exceeded == ("elapsed_ms",)
    assert receipt.snapshot.spent["tokens"] == 7
    assert receipt.snapshot.usage.elapsed_ms == 150


def test_zero_deadline_prevents_even_zero_duration_dispatch():
    controller = BudgetController(BudgetLimits(max_elapsed_ms=0), clock=Clock())
    with pytest.raises(BudgetRefused) as caught:
        controller.admit("zero", BudgetEstimate(elapsed_ms=0))
    assert caught.value.reason == "deadline_exceeded"


def test_completion_exactly_at_deadline_succeeds_but_new_admission_is_refused():
    clock = Clock()
    controller = BudgetController(BudgetLimits(max_elapsed_ms=100), clock=clock)
    admission = controller.admit("exact", BudgetEstimate(elapsed_ms=100))
    clock.advance_ms(100)
    assert controller.settle(admission, measured()).ok
    assert controller.precheck(BudgetEstimate(elapsed_ms=0)).reason == "deadline_exceeded"


def test_simultaneous_duplicate_settlement_charges_once():
    controller = BudgetController(BudgetLimits(max_tokens=10))
    admission = controller.admit("step", BudgetEstimate(tokens=5))
    barrier = Barrier(8)

    def settle(_index):
        barrier.wait(timeout=10)
        return controller.settle(admission, measured(4))

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(settle, range(8)))
    assert len({receipt.trace_id for receipt in receipts}) == 1
    assert controller.snapshot().spent["tokens"] == 4
    assert controller.snapshot().settled_steps == 1


def test_settlement_identity_and_idempotence():
    controller = BudgetController(BudgetLimits(max_steps=1))
    admission = controller.admit("step")
    other = BudgetController(BudgetLimits())
    with pytest.raises(ValueError, match="does not belong"):
        other.settle(admission, measured())
    with pytest.raises(ValueError, match="does not match"):
        controller.settle(replace(admission, step_id="forged"), measured())
    first = controller.settle(admission, measured(3))
    second = controller.settle(admission.reservation_id, measured(3))
    assert first == second
    assert controller.snapshot().spent["tokens"] == 3
    assert controller.snapshot().settled_steps == 1
    with pytest.raises(ValueError, match="conflicting"):
        controller.settle(admission, measured(4))


def test_limits_estimates_and_returned_receipts_cannot_mutate_accounting():
    limits = BudgetLimits(max_tokens=4)
    controller = BudgetController(limits)
    limits.max_tokens = 400
    with pytest.raises(BudgetRefused):
        controller.admit("too-large", BudgetEstimate(tokens=5))
    estimate = BudgetEstimate(tokens=3)
    with pytest.raises(FrozenInstanceError):
        estimate.tokens = 400
    admission = controller.admit("valid", estimate)
    actual = measured(3)
    receipt = controller.settle(admission, actual)
    actual.input_tokens = 1000
    receipt.snapshot.usage.input_tokens = 2000
    with pytest.raises(TypeError):
        receipt.snapshot.spent["tokens"] = Decimal(3000)
    assert controller.usage.input_tokens == 3
    assert controller.settle(admission, measured(3)).snapshot.usage.input_tokens == 3


def test_usage_estimates_are_copied_on_admission():
    controller = BudgetController(BudgetLimits(max_tokens=5))
    estimate = Usage(input_tokens=3, output_tokens=0)
    controller.admit("first", estimate)
    estimate.input_tokens = 0
    assert controller.snapshot().reserved["tokens"] == 3
    assert not controller.precheck(BudgetEstimate(tokens=3)).allowed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tokens": -1}, {"tokens": True}, {"tokens": 1.5},
        {"cost_usd": -1}, {"energy_j": float("nan")}, {"elapsed_ms": float("inf")},
        {"cost_usd": True}, {"cost_usd": "1"}, {"energy_j": Decimal("Infinity")},
    ],
)
def test_invalid_estimates_rejected(kwargs):
    with pytest.raises(ValueError):
        BudgetEstimate(**kwargs)


def test_invalid_mutated_actual_preserves_reservation_for_correct_report():
    controller = BudgetController(BudgetLimits(max_tokens=10))
    admission = controller.admit("step", BudgetEstimate(tokens=5))
    actual = measured(3)
    actual.input_tokens = -1
    with pytest.raises(ValidationError):
        controller.settle(admission, actual)
    assert controller.snapshot().pending_steps == 1
    assert controller.settle(admission, measured(3)).ok


def test_failed_trace_write_does_not_release_reservation_or_lose_actual_usage():
    class FailingStore(MemoryStore):
        fail = False

        def put(self, node):
            if self.fail:
                raise OSError("synthetic persistence failure")
            super().put(node)

    store = FailingStore()
    controller = BudgetController(BudgetLimits(max_tokens=5), store=store)
    admission = controller.admit("step", BudgetEstimate(tokens=5))
    store.fail = True
    with pytest.raises(OSError):
        controller.settle(admission, measured(4))
    assert controller.snapshot().pending_steps == 1
    assert controller.snapshot().spent == {}
    assert controller.precheck(BudgetEstimate(tokens=0)).reason == "accounting_unavailable"
    store.fail = False
    assert controller.settle(admission, measured(4)).ok
    assert controller.snapshot().spent["tokens"] == 4
    assert controller.precheck(BudgetEstimate(tokens=0)).reason == "accounting_unavailable"


def test_backwards_clock_is_rejected_instead_of_extending_deadline():
    clock = Clock()
    controller = BudgetController(BudgetLimits(max_elapsed_ms=100), clock=clock)
    clock.advance_ms(50)
    assert controller.precheck(BudgetEstimate(elapsed_ms=0)).allowed
    clock.advance_ms(-1)
    with pytest.raises(ValueError, match="backwards"):
        controller.precheck(BudgetEstimate(elapsed_ms=0))
