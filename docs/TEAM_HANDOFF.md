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
- [Offline advisor guide](../core/docs/advisor.md): runnable discovery for business
  decisions, tool selection and next-step choices, with a strict JSON report.
- [Advisor design](ADVISOR_DESIGN.md): implemented discovery and later evaluation
  and activation boundaries.
- [JEV and communication demo](JEV_COMMS_DEMO.md): offline presentation, actual
  local classifier comparisons, shared-context batches and reproduction commands.

## Capability checkpoint

| Capability | Current availability | Demo source |
| --- | --- | --- |
| Recording, cache/replay, budgets, freshness, policy/approvals | Integrated runtime and local HTTP API | Synthetic API or Python workload; live handlers require server registration |
| Drift probes/containment and compact messaging/deduplication | Python components, CBOR-LD batches and persistent mailbox | Component smoke example plus measured communication replay; no dedicated HTTP dashboard endpoints |
| OpenAI, compatible endpoints, Anthropic, Gemini | Implemented live handlers | Explicit provider/model and credentials; `live_comparison.py` |
| Pollard-Jev/OpenJEV | Isolated native worker plus explicit text mappings for all three sample categories | Actual local classifier comparison and offline HTML; separate environment and cached pinned weights needed only to rerun inference |
| Business/tool/next-step optimization advisor | Offline Python/CLI discovery and JSON report | [Retained hosted recordings](../core/examples/recordings/advisor-live-2026-10-17/README.md) across three providers; `core/examples/advisor_demo.py` for synthetic coverage; no advisor HTTP endpoint |
| Automatic backend switching or dynamic agent loop | Not implemented | No production control or success claims in the demo |

Use the current commit's test/CI results as integration evidence, not business
quality or savings evidence.
Prior hosted smoke runs exercised real calls; the local API example still uses
synthetic handlers. Preserve provenance for each displayed result.

## Ready-to-demo hosted recordings

The [hosted advisor demo](../core/examples/recordings/advisor-live-2026-10-17/README.md)
contains sanitized comparison exports, frontend playback bundles and the combined
advisor report for business decisions, tool selection and next-step choices across
OpenAI, Anthropic and Gemini. Its artifact index identifies every source and model.
The committed files work without credentials or local databases. Imported runs
are historical playback; they do not populate the local HTTP service's run store.

Across the nine comparisons there are 36 runs, 63 hosted model calls and 21 warm
reuses. The new tool/next-step tasks account for 54 of those calls. Their authored
fixture checks show 53 expected choices after normalization, but only 24 choices
also met the requested bare-JSON contract. Warm reuses and the old business case
are excluded from that check; it is not an independent quality benchmark.

The historical advisor report has 21 provider-specific groups: three business,
nine tool selection and nine next step. Its saved findings suggest OpenJEV evaluation and require mapping;
only the business groups are partial substitutions because their calls also
generate explanations. Keep that discovery snapshot unchanged and link the new
explicit mapping evaluation beside it.

The inputs are invented sample data and the inference is real: preserve both
`sample_data: true` and `synthetic: false` in the explanation. Tool names were
selected without execution, and next steps were selected from authored choices
without changing the runtime DAG. The findings identify opportunities to evaluate;
they do not establish successful JEV replacement, measured savings or independent
business accuracy. The separate local JEV report evaluates these same sample
inputs and retains its raw predictions and gate outcomes.

Include the retained OpenAI next-step error in the demo: `choose_retrieval` chose
`respond` in its first baseline despite missing approved evidence. The fixture
checks also distinguish normalized choice matches from bare-JSON compliance.
These outcomes remain visible; subsequent answer variation is not evidence that
governance corrected quality. Those historical runs had no transition-eligibility
gate; the new JEV mapping applies an explicit gate without changing raw results.

The frontend can import the supplied comparison bundles now. Its separate advisor
report view still needs the integration described in the frontend handoff; use
the saved advisor JSON until it is built. The benchmark teammate can reanalyze
the full comparison exports and extend the cases with independently labeled
outcomes. The artifact guide includes fresh-task and reanalysis commands.

## Actual JEV evaluation and communication demo

Open the [demo start page](../core/examples/recordings/index.html) and follow the
[presenter guide](JEV_COMMS_DEMO.md). The separate JEV page compares the seven
distinct recorded cases with actual local OpenJEV outputs. Inspect original
source hashes, text premises, hypotheses, full NLI scores, raw choices and
acceptance outcomes. The reviewed mappings do not execute tools or transitions.
Business priority still needs its reason; deterministic rules already solve the
explicit Boolean next-step cases without a model call. No route is activated.

The actual run made 14 local classifier calls (two trials per case) and no new
hosted calls. Both trials selected all seven expected answers; the gate accepted
six per trial. Business priority correctly chose `high` but deferred because its
0.1150 margin was below the fixed 0.15 threshold. All three tool and three
next-step selections were accepted. Present the abstention as part of the result,
not as a failed demo to hide. There are seven distinct cases, not 14 independent
examples, and these outcomes do not validate production replacement.

The communication page measures retained workflow events replayed through the
actual `cbor-ld-ex` codec and SQLite mailbox. `MessageBatchCodec` shares repeated
context inside each self-contained batch; `Mailbox.deliver_batch` handles
idempotent delivery. It is a measured local transport replay, not evidence of
distributed network throughput or reduced model-token consumption. Show byte
counts, roundtrip integrity, duplicate suppression and serialization/delivery
timings separately. The smaller-representation fallback remains visible when
JSON beats CBOR for a payload.

For 618 retained events in 36 batches, the measured full bodies were 771,411 B
as compact JSON, 585,595 B as individual CBOR messages and 356,491 B as shared
CBOR batches: 53.79% fewer bytes than JSON and 39.12% fewer than individual CBOR.
All 618 messages were delivered and acknowledged; 36 duplicate redeliveries were
suppressed. One local SQLite sample reduced commits from 618 to 36 and delivery
from 872.85 ms to 204.52 ms. JSON encoding/decoding remained cheaper in CPU time
than batched CBOR, so present size and CPU as separate tradeoffs.

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
`live_comparison.py` compares reuse within one selected backend. The separate
`jev_recorded_comparison.py` now evaluates explicit mappings against retained
hosted outputs on seven sample cases. This smoke evaluation does not replace the
benchmark teammate's held-out study. Keep selection, argument generation,
explanation and tool execution costs visible.

## Frontend boundary

The implemented offline advisor has an importable
[AdvisorReport schema](../core/schemas/AdvisorReport.schema.json). Generate the
demo with `python core/examples/advisor_demo.py --output-dir .canopy/advisor-demo`.
Display actual findings, observed usage, provenance, limits and missing evaluation.
Current states are `candidate`, `needs_mapping`, `unsupported`; every finding is
unevaluated in that discovery contract. Explicit text mappings and a separate
evaluation report now exist; no live advisor endpoint, activation or automatic
routing was added. See the frontend guide for the distinct report adapters and
offline HTML fallback.

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
with zero new model/tool calls. The local API and synthetic examples use fixture
handlers; the retained hosted demo above contains real model-call recordings on
invented inputs. Keep those origins distinct.

For the expanded demo, show run comparison and controls, then review the generated
offline advisor report: business classification, choosing a known tool, and
choosing an authored next step. Show why each finding was suggested, then open
the separate actual JEV evaluation and its raw versus gated outcomes. Show the
communication replay's measured full-body byte counts and delivery checks.
Use the synthetic advisor demo for broader detector cases. Representative
replacement quality and dollar/energy savings remain unmeasured. Neither the
advisor nor the mappings implement a dynamic agent loop.

## Team ownership and coordination

Core owns backend contracts, handler/evidence mappings and versioning. The frontend
owns `web/`; the benchmark teammate owns its application and correctness scorer.
Agree on one workload, outcome schema, fixture/report format and provider setup
before building cross-backend charts. Frontend mock records should carry an
explicit fixture/proposed marker outside current core API objects; current strict
contracts reject invented fields. Do not invent an advisor HTTP endpoint.
