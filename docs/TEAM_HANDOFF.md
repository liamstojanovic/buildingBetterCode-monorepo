# Team integration handoff

The core is a Python package in `core/`. The frontend remains in `web/` and the
benchmark teammate can choose their own directory. Core component chats do not
edit those applications. The shared machine-readable models are in
`core/src/canopy/contracts.py`; the API owner maintains `core/docs/api.md`.
JSON Schema exports for non-Python clients live in `core/schemas/`. Regenerate
them with `python core/scripts/export_contracts.py` after contract changes.

Updated October 17, 2026. Start with the role-specific guides:

- [Frontend integration and demo](FRONTEND_HANDOFF.md): current API/data sources,
  screen priorities, states, and a demo sequence.
- [Benchmark integration](BENCHMARK_HANDOFF.md): runnable comparisons, independent
  scoring, and the proposed backend-substitution evaluation.
- [Advisor discussion design](ADVISOR_DESIGN.md): low-overhead discovery for
  business decisions, tool selection and next-step choices. **Not implemented.**

## Capability checkpoint

| Capability | Current availability | Demo source |
| --- | --- | --- |
| Recording, cache/replay, budgets, freshness, policy/approvals | Integrated runtime and local HTTP API | Synthetic API or Python workload; live handlers require server registration |
| Drift probes/containment and compact messaging/deduplication | Python components and smoke example | `core/scripts/component_smoke.py`; no dedicated HTTP dashboard endpoints |
| OpenAI, compatible endpoints, Anthropic, Gemini | Implemented live handlers | Explicit provider/model and credentials; `live_comparison.py` |
| Pollard-Jev/OpenJEV | Implemented isolated native worker | Separate environment and cached pinned weights required; actual local inference unverified here |
| Business/tool/next-step optimization advisor | Discussion design only | Label any frontend prototype as a proposed feature/fixture |
| Automatic backend switching or dynamic agent loop | Not implemented | No production control or success claims in the demo |

The core at commit `a44c655` passed 546 tests locally and CI on Python 3.11/3.12.
Those checks establish integration behavior, not business quality or savings.
Prior hosted smoke runs exercised real calls; the local API example still uses
synthetic handlers. Preserve provenance for each displayed result.

## Local development

Use Python 3.11 or 3.12. The pinned Chronofy dependency uses NumPy 1.26.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r core/requirements.lock
.\.venv\Scripts\python.exe -m pip install -e "core[dev,models]"
.\.venv\Scripts\python.exe -m pytest core/tests -q
.\.venv\Scripts\python.exe -m ruff check core
```

On macOS/Linux use `.venv/bin/python` instead. No model credentials are required
for the synthetic example or tests. Live inference is opt-in and is metered
separately. Never commit credentials or run databases.

## Benchmark boundary

Real provider adapters and a ready comparison runner are documented in
[`core/docs/live_models.md`](../core/docs/live_models.md). Install `core[models]`,
select an explicit provider/model, and run `core/examples/live_comparison.py`.
Its `--task-file` accepts your Task JSON; Python `run_comparison()` accepts a
custom registry for mixed model/tool workloads. Every comparison starts separate
empty stores and saves full outputs plus provider-reported token usage.

For a cache comparison, keep the same workload, tool implementations, model
configuration and task data across runs. Register handlers through the runtime API.
The benchmark owns correctness scoring independently of the core. Compare cold
execution, unchanged repeat, relevant source change and unrelated source change.
Keep adversarial/policy challenge cases separate from ordinary task performance.

Report successful task completion and policy adherence beside actual input and
output tokens, model calls, elapsed time and available cost measurements. A
blocked/incomplete task is not a successful efficiency result. Distinguish
application-cache reuse, provider cached input tokens and historical replay.
Give the baseline comparable history/reuse opportunities. Include monitoring and
refresh costs where incurred; missing cost/energy stays null, not zero.

For proposed JEV substitution, use equivalent business inputs and a shared outcome
contract across backends, allowing the explicitly recorded backend/mapping change.
The current runner compares reuse within one selected backend; it does not yet
measure cross-backend quality. Keep selection, argument generation, explanation,
and tool execution costs visible. The benchmark guide specifies the new study.

## Frontend boundary

Render the current run status and ordered `RunEvent` records. The server supplies
usage and status; the browser must not invent successful operations or metrics.
Useful states include live execution, reused computation, stale evidence,
approval required, budget refusal, failed handler and completed task.

`POST /runs` is synchronous and returns a completed or blocked/failed/approval
result, not an immediate job ID. Events are available as ordered JSON records;
there is no SSE/WebSocket feed. Frontend run submission should show a pending
request state and render the returned recorded timeline. Detailed endpoint and
approval behavior is covered in the frontend guide.

For inspection show step input/dependency references, source and policy versions,
the reason for reuse/refusal, and original run provenance. Historical replay is
clearly labelled and does not perform business writes. Use actual API fixtures
from the runtime owner; keep temporary frontend-only mocks visibly labelled.

Approver identity and permissions come from the trusted service configuration,
not client role labels. The local demo is not production SSO. Read `core/docs/api.md`
for implemented endpoints and the explicit authentication boundary.

## Integration checkpoint

One workload should run through baseline and governed adapters and render in the
frontend before expanding the suite. Demonstrate unchanged reuse, refresh or
refusal after a relevant change, unaffected work remaining reusable, and replay
with zero new model/tool calls. All demonstration records are synthetic until a
real provider is explicitly selected.

For the expanded demo, first show the implemented run comparison and controls,
then an explicitly labelled advisor preview: business classification, choosing a
known tool, and choosing an authored next step. Show a candidate's reasons,
required mapping and missing evaluation rather than invented savings. A measured
advisor recommendation becomes a live demo only after its analyzer/mapping and
independent evaluation exist. The frontend guide separates those milestones.

## Team ownership and coordination

Core owns backend contracts, handler/evidence mappings and versioning. The frontend
owns `web/`; the benchmark teammate owns its application and correctness scorer.
Agree on one workload, outcome schema, fixture/report format and provider setup
before building cross-backend charts. Frontend mock records should carry an
explicit fixture/proposed marker outside current core API objects; current strict
contracts reject invented fields. Do not invent an advisor HTTP endpoint.
