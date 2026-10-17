# Team integration handoff

The core is a Python package in `core/`. The frontend remains in `web/` and the
benchmark teammate can choose their own directory. Core component chats do not
edit those applications. The shared machine-readable models are in
`core/src/canopy/contracts.py`; the API owner maintains `core/docs/api.md`.
JSON Schema exports for non-Python clients live in `core/schemas/`. Regenerate
them with `python core/scripts/export_contracts.py` after contract changes.

## Local development

Use Python 3.11 or 3.12. The pinned Chronofy dependency uses NumPy 1.26.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r core/requirements.lock
.\.venv\Scripts\python.exe -m pip install -e "core[dev]"
.\.venv\Scripts\python.exe -m pytest core/tests -q
.\.venv\Scripts\python.exe -m ruff check core
```

On macOS/Linux use `.venv/bin/python` instead. No model credentials are required
for the synthetic example or tests. Live inference is opt-in and is metered
separately. Never commit credentials or run databases.

## Benchmark boundary

Keep the same workload, tool implementations, model configuration and task data
across compared adapters. Register workload handlers through the runtime API.
The benchmark owns correctness scoring independently of the core. Compare cold
execution, unchanged repeat, relevant source change and unrelated source change.
Keep adversarial/policy challenge cases separate from ordinary task performance.

Report successful task completion and policy adherence beside actual input and
output tokens, model calls, elapsed time and available cost measurements. A
blocked/incomplete task is not a successful efficiency result. Distinguish
application-cache reuse, provider cached input tokens and historical replay.
Give the baseline comparable history/reuse opportunities. Include monitoring and
refresh costs where incurred; missing cost/energy stays null, not zero.

## Frontend boundary

Render the current run status and ordered `RunEvent` records. The server supplies
usage and status; the browser must not invent successful operations or metrics.
Useful states include live execution, reused computation, stale evidence,
approval required, budget refusal, failed handler and completed task.

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
