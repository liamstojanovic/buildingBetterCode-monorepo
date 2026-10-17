# Integration notes from the coordinator

## Token completeness contract

`Usage.tokens_known` now survives JSON roundtrips. It is inferred true when both
input/output token fields are explicitly supplied and no flag is supplied;
`Usage()` has `tokens_known=False`. JSON serialization retains the flag.

Budget and runtime integrations must consult this flag rather than relying only
on Pydantic `model_fields_set`: serialization fills default numeric fields and
therefore destroys omission information. Unknown actual tokens are not measured
zero, and unknown estimates must not authorize work under a token cap. Synthetic
and live handlers that supply actual counts explicitly automatically have true.
Zero-call cache/replay usage can explicitly provide zero counts.

The analogous `Usage.elapsed_known` flag now preserves estimated/measured elapsed
time completeness; `Usage()` is unknown, explicit `elapsed_ms=0` is known zero.
BudgetEstimate conversion must use it rather than field presence. Runtime timing
via `model_copy(update=...)` must explicitly set `elapsed_known=True` with the
measured elapsed value (Pydantic copy does not rerun validators).

## Pure handler cache boundary

Cache identity binds declared step inputs, relevant evidence and dependency
outputs. A reusable handler must only observe those declared values. Avoid giving
a pure computation unrestricted access to unrelated Task evidence, task/run IDs,
budgets or current time, unless those values also enter its cache identity.
Handler registration should explicitly opt into deterministic/cache-safe behavior
or provide a narrowed context. Side-effect handlers are never cached.

## Final checks

Compare direct Python input and JSON-roundtripped task/config for identical
budget admission. Test tenant isolation for every run/event/replay/approval/cache
lookup. Test approval binding after policy/evidence/input mutation, and confirm
each replay path has zero new dispatches. Include absence of token usage in a
failed provider call. A cheaper blocked task is not a completed efficiency result.
