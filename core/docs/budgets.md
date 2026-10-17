# Budget admission and actual usage

## Public integration API (owner 2)

```python
from canopy.budgets import (
    BudgetController, BudgetEstimate, BudgetRefused,
    BudgetAdmission, BudgetDecision, BudgetSettlement, BudgetSnapshot,
)

BudgetController(
    limits: BudgetLimits, *, run_id: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    store: pollard.Store | None = None,
)
controller.precheck(
    estimate: BudgetEstimate | Usage | None = None, *, step_id: str | None = None,
) -> BudgetDecision
controller.admit(
    step_id: str, estimate: BudgetEstimate | Usage | None = None,
) -> BudgetAdmission                     # raises BudgetRefused on refusal
controller.settle(
    admission: BudgetAdmission | str, actual: Usage | None,
) -> BudgetSettlement
controller.snapshot() -> BudgetSnapshot
controller.usage -> Usage
controller.trace_root_id -> str
controller.store -> pollard.Store
```

`BudgetEstimate(tokens=None, cost_usd=None, energy_j=None, elapsed_ms=None)`
is an immutable estimate with explicit unknowns. Each admission reserves exactly
one step. `BudgetDecision.allowed`, `.reason`, `.meter`, `.requested`,
`.remaining`, and `.detail` describe a check. `BudgetRefused.decision` carries
the same decision. Prechecks are advisory; only admission reserves capacity.

`BudgetAdmission.reservation_id`, `.step_id`, `.estimate`, and `.trace_id`
identify an admitted attempt. Settlement accepts that object or its reservation
ID. It records actual usage even when it exceeds the estimate or cap.
`BudgetSettlement.ok`, `.exceeded`, `.unknown_metrics`, `.reason`, `.trace_id`,
and `.snapshot` expose the outcome. Runtime must mark a non-ok settlement as
incomplete/failed rather than reporting a budget-compliant success. Settlement
is idempotent for the same actual usage and rejects conflicting repeated reports.

`BudgetSnapshot.usage`, `.spent`, `.reserved`, `.admitted_steps`,
`.settled_steps`, `.pending_steps`, `.unknown_metrics`, and `.exceeded` expose
aggregate accounting. Decimal charge mappings use Pollard meter names `tokens`,
`steps`, `usd`, and `energy_j`. Unknown cost/energy totals remain null in Usage.

## Semantics and integration requirements

- Share **one controller object per run** among all workers. A lock makes the
  check/reserve/settle operations atomic within that object. Different controllers
  or processes do not share enforcement, even when given the same trace store.
- Actual Pollard `Budget`, `check_budget`, and `exhausted_after_settle` enforce
  additive caps; Pollard token/step meters account actual calls. Pollard nodes
  record admissions, settlements and refusals for trace linkage. The default
  store is Pollard MemoryStore. A caller-provided store must support the calling
  threads; a trace store alone is not a distributed budget arbiter.
- Token usage is input plus output. Provider cached input tokens are already part
  of input and are not counted twice. Call counts come from actual Usage; one
  governed step is charged for each admitted attempt, including failed attempts.
- A capped token estimate requires a known token total. Converting Usage honors
  `tokens_known`, which the shared contract infers when **both input_tokens and
  output_tokens are explicitly supplied**, including explicit zeroes. Setting
  `tokens_known=False` preserves an unknown measurement even with partial counts.
  Defaults in `Usage()` represent absent token measurements. Capped USD
  and joules require non-null estimates. Uncapped unknown estimates are allowed.
- `max_elapsed_ms` is a monotonic run deadline starting at construction, including
  idle time. Admission refuses at the deadline. A known duration estimate that
  cannot fit before the deadline is refused; an unknown duration estimate is
  refused when a deadline is configured. Concurrent durations are not summed.
  Settlement detects a deadline overrun, but cannot undo or cancel arbitrary
  Python/provider work. Providers need their own timeout/output limits for hard
  bounds; estimates only gate dispatch and do not guarantee actual consumption.
  Completion exactly at the deadline is accepted; no new work can be admitted
  at that instant.
- Always settle a dispatched attempt, even on errors. Pass `actual=None` when
  usage is unavailable. Unknown actual metrics required by a configured cap make
  settlement non-ok and prevent further admission. Unknown USD/energy propagate
  through run totals; known partial charges remain available in `.spent`.
- Energy values are caller-supplied measured joules. This component neither
  measures hosted-provider energy nor converts tokens into invented energy.
- New execution only belongs in this controller. Cache reuse and replay have no
  admissions and should keep historical usage separately. Do not settle estimates
  as if they were measurements.
- A settlement trace-write exception leaves the reservation intact and permanently
  closes this controller to new admission (`accounting_unavailable`). The caller
  can still settle pending work or retry persistence, but must fail the run and
  investigate the store; it cannot safely assume that an uncertain write failed.

## Measurement completeness and serialization

The shared `Usage.tokens_known` field preserves omitted-vs-measured token status
across JSON round trips. Aggregate Usage sets it false if any settled token
measurement is unknown; integer token fields then contain measured lower bounds.
Call counts are caller-supplied counters and remain lower bounds if usage was
unavailable. No contract change is currently required for token enforcement.

Duration estimates use the shared `Usage.elapsed_known` flag, which preserves
unknown duration across JSON round trips. `BudgetEstimate(elapsed_ms=None)` also
represents unknown duration explicitly. Actual run elapsed time always comes from
the controller's monotonic clock, regardless of caller usage, and is marked known.

## Verification

Focused tests cover caps and exact boundaries, competing reservations and parallel
settlement, actual overspend, unknown measurements and JSON round trips, cached
token accounting, Decimal cost arithmetic, deadline semantics, identity and
idempotence, defensive copies, validation and persistence failure recovery.
Fixtures and clocks are synthetic; they exercise the installed Pollard library.

Validated with `.venv/Scripts/python.exe -m pytest core/tests/test_budgets.py -q`
and `.venv/Scripts/python.exe -m ruff check core/src/canopy/budgets.py
core/tests/test_budgets.py`: 45 tests passed and Ruff passed.
