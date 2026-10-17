"""Regressions at shared wire, budget, and runtime component boundaries."""

import pytest

from canopy.budgets import BudgetController, BudgetEstimate, BudgetRefused
from canopy.contracts import BudgetLimits, Usage


@pytest.mark.parametrize("wire_roundtrip", [False, True])
def test_unknown_estimates_remain_unknown_for_budget_admission(wire_roundtrip):
    usage = Usage()
    if wire_roundtrip:
        usage = Usage.model_validate_json(usage.model_dump_json())
    for limits in (BudgetLimits(max_tokens=0), BudgetLimits(max_elapsed_ms=1000)):
        controller = BudgetController(limits, clock=lambda: 0)
        with pytest.raises(BudgetRefused) as refusal:
            controller.admit("unknown", usage)
        assert refusal.value.reason == "unknown_estimate"


def test_measured_zero_estimates_remain_admissible_across_wire():
    usage = Usage(input_tokens=0, output_tokens=0, elapsed_ms=0)
    usage = Usage.model_validate_json(usage.model_dump_json())
    estimate = BudgetEstimate.from_usage(usage)
    assert estimate.tokens == 0
    assert estimate.elapsed_ms == 0
    controller = BudgetController(
        BudgetLimits(max_tokens=0, max_elapsed_ms=1000), clock=lambda: 0
    )
    admission = controller.admit("zero", estimate)
    assert controller.settle(admission, usage).ok
    assert controller.usage.elapsed_known
