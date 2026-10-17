# Canopy core component ownership

The user authorized implementation of the core and direct pushes to main. The
human teammates own the benchmark application and web frontend. Preserve their
work. This checkout is shared by six component chats; each chat may spawn its own
subagents. The coordinator alone stages, commits, pulls/rebases and pushes.

## Shared boundary

Python 3.11+, package `core/src/canopy`, contracts in `contracts.py` (schema 1).
The coordinator owns contracts, package metadata, root documentation and CI.
Request contract changes from the coordinator; do not independently edit them.
The shared virtual environment is `.venv` at the repository root. Install commands
are coordinated centrally. Inspect installed dependency source and test actual
calls rather than guessing APIs. Existing package code is attributed as a dependency.

Every component must supply real library integration, focused meaningful tests,
and a concise component document. Fixtures are explicitly synthetic. No fabricated
savings, energy readings, statistical confidence or claims of general compliance.

## Owners and files

1. Recording/replay/cache: `recording.py`, `cache.py`, `tests/test_recording.py`,
   `tests/test_cache.py`, `docs/recording.md`. Durable SQLite run/event/result
   records, Pollard trace linkage, strict inspection/replay with no dispatch,
   tenant-scoped cache identity binding task inputs, dependency result digests,
   evidence, model/prompt/tool versions, policy and permissions. Side effects are
   never reusable computations. Expose `ExecutionStore` and `ResultCache`.
2. Budgets: `budgets.py`, `tests/test_budgets.py`, `docs/budgets.md`. Pollard-backed
   admission/settlement for shared run steps, tokens, USD, elapsed time, optional
   measured joules. Explicit estimates/actuals, unknown metrics, and refusal
   reasons. No fictional strict cancellation of arbitrary Python/model compute.
   Expose `BudgetController` and typed `BudgetRefused`.
3. Temporal validity: `temporal.py`, `tests/test_temporal.py`, `docs/temporal.md`.
   Chronofy-backed freshness plus explicit expiry, tenant/source/version checks,
   transitive dependency invalidation and cycle validation. Separate freshness
   from authority. Expose `TemporalValidator`, `FreshnessDecision`, and graph helpers.
4. Inter-agent communication: `messaging.py`, `tests/test_messaging.py`,
   `docs/messaging.md`. Real Python cbor-ld-ex encoding/decoding of versioned typed
   evidence envelopes, provenance references, tenant validation, precision bounds,
   size measurement. Local durable deduplicated mailbox and explicit sequence
   semantics if feasible. Do not call encoding distributed synchronization or
   equate bytes with LLM tokens. Expose `MessageCodec` and `Mailbox`.
5. Policy and drift: `governance.py`, `drift.py`, `tests/test_governance.py`,
   `tests/test_drift.py`, `docs/governance.md`. Deterministic tool/action policies,
   exact approval binding, persisted approval records, explicit policy revisions;
   Epicormic fixed policy probes, custom conformance scorer, independently checked
   baseline, unknown state and forward containment. Policy changes are not silent
   provider drift. Pollard-Jev's typed observation/proposal/decision pattern may
   inform this adapter; its robotics-specific policy must not masquerade as an
   enterprise policy. Expose `PolicyGate`, `ApprovalStore`, `DriftMonitor`.
6. Runtime/API integration: `runtime.py`, `api.py`, `handlers.py`,
   `tests/test_runtime.py`, `tests/test_api.py`, `docs/api.md`, `examples/`.
   Consume the components through small adapters. Provide `run_task(task, config)`
   and a handler registry for Python benchmark clients. Expose FastAPI run start,
   list/detail, ordered event access and approvals; create an explicit synthetic
   no-credential demo. Optional live provider adapter only when configured; never
   claim fixture calls are live LLM calls. The benchmark and frontend remain owned
   by the human teammates.

Each component owner should document exact public signatures early in their own
document. Integration can inspect those files as they appear and adapt locally.
Keep component dependencies narrow and avoid editing another owner's files.

## Execution semantics

`Task.steps` is a DAG with unique step IDs and registered handlers. Inputs to a
handler include explicit step inputs and dependency outputs; the application owns
scheduling. All governed model/tool dispatch must pass the checks. Validate task
and tenant boundaries before any dispatch. Permissions are enforced by a trusted
application context; client-supplied role labels do not establish authentication.

For a live governed step: validate dependencies/evidence, inspect eligible cache,
check current policy/approval, admit against budget, call handler, settle actual
usage, persist output/event/trace. Reuse must still meet current permissions and
policy. Source changes invalidate descendants, not unrelated work. External
side-effect retries need service-owned idempotency; a recording is not proof that
an unknown external action failed. Replay reads history and makes zero model or
tool calls. Baseline mode keeps the workload/handler equivalent and reports usage.

Maintain replay historical usage separately from new execution spend. Distinguish
application reuse from provider cached input tokens. Cost/energy remain null when
unmeasured. Report incomplete/blocked outcomes separately from successful work.
API demo access and production authentication boundaries must be explicit.

## Checks and delivery

From repository root: `.venv/Scripts/python.exe -m pip install -e "core[dev]"`.
Then `.venv/Scripts/python.exe -m pytest core/tests` and
`.venv/Scripts/python.exe -m ruff check core`.

Each chat reports changed files, public interface, actual tests and limitations.
Do not stage, commit or push from a component chat; the coordinator integrates,
runs all checks, preserves concurrent remote main changes and pushes normally.
