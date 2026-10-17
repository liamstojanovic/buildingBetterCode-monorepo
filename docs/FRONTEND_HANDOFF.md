# Frontend integration and demo handoff

The frontend teammate owns `web/`. This guide separates working backend features
from the proposed JEV/OpenJEV advisor so the demo can show both accurately.
Start with the [team handoff](TEAM_HANDOFF.md), [HTTP API](../core/docs/api.md),
and [live model guide](../core/docs/live_models.md).

## What can be built now

| Surface | Available today | Frontend treatment |
| --- | --- | --- |
| Run browser and workflow graph | Run list/detail, static task DAG, ordered events | Live API integration |
| Baseline, cold/warm reuse, evidence change, replay | Full run records and synthetic demo | Live API or imported actual report |
| Approvals | Exact bound proposal, decision endpoint, single-use approval | Live local demo |
| Budgets | Admission/refusal/settlement events and measured usage | Read-only authority and outcome display |
| Hosted models and native Pollard-Jev | Python registry and comparison CLI | Import actual comparison report; custom service wiring needed for live HTTP execution |
| Drift containment and compact messaging | Component APIs and smoke report | Imported component report, clearly labeled synthetic |
| Optimization advisor | Discussion/design only | Visibly labeled prototype fixtures; no live advisor routes |

The runtime executes a predefined DAG sequentially in deterministic order. It
does not dynamically select tools or generate next steps. The local HTTP example
registers only synthetic handlers. Provider selection in a browser cannot change
that registry; a trusted backend must register and configure live handlers first.

Suggested screen priority:

1. **Runs and comparison:** outcome, mode, data origin, current usage, reused and
   invalidated steps; open any run to inspect its actual output.
2. **Workflow detail:** graph from `Task.steps[].depends_on`, step status, evidence,
   reason, usage, and reuse provenance; ordered event timeline beside the graph.
3. **Review drawer:** exact action/evidence binding, expiry, reviewer decision,
   consumed status, and explicit execution of the approved request as a new run.
4. **Component evidence:** drift probe result and message-byte comparison imported
   from the component smoke report.
5. **Advisor prototype:** business decisions, tool selection, and next-step
   candidates with explanations and evaluation requirements, labeled proposed.

## Local service and runnable inputs

After the environment setup in the team handoff, run from the repository root:

```powershell
.venv\Scripts\python.exe core\examples\local_api.py
```

The server binds to `http://127.0.0.1:8000`. Its interactive schema is `/docs`;
the live OpenAPI document is `/openapi.json`. Generate a fresh request body:

```powershell
$request = .venv\Scripts\python.exe core\examples\local_api.py --task-json | ConvertFrom-Json
$body = $request | ConvertTo-Json -Depth 100
$cold = Invoke-RestMethod http://127.0.0.1:8000/runs -Method Post -ContentType 'application/json' -Body $body
$warm = Invoke-RestMethod http://127.0.0.1:8000/runs -Method Post -ContentType 'application/json' -Body $body
```

Reuse the **same body** for the warm run. Regenerating the fixture changes its
evidence timestamps. Evidence expires after one hour; generate a fresh snapshot
for a later demo. The fixed tenant is `demo-tenant` and the fixture's three steps
are `read_catalog`, its dependent `summarize_quote`, and independent `greeting`.

To demonstrate a relevant source change, clone the original request and update
`task.evidence[0].version` to `"2"` and its `data.amount_minor` to `3100`, then
post it. The two catalog-dependent steps rerun while the greeting can be reused.
Keep the original request for replay and approval demonstrations.

For a baseline use `{task, mode: "baseline"}`. For historical replay use
`{task, mode: "replay", replay_run_id: sourceRunId}` with the original task from
that terminal source run. Replay accepts an otherwise identical task with a new
`task_id`; it does not execute handlers or test conformance with a newer policy.

For ready-to-import full records covering all six scenarios:

```powershell
New-Item -ItemType Directory -Force .canopy | Out-Null
.venv\Scripts\python.exe core\examples\synthetic_demo.py | Set-Content -Encoding utf8 .canopy\frontend-demo.json
.venv\Scripts\python.exe core\scripts\component_smoke.py | Set-Content -Encoding utf8 .canopy\frontend-components.json
```

The first report has `{synthetic, description, runs}` where `runs` contains
`baseline`, `governed`, `warm`, `source_changed`, `budget_refused`, and `replay`.
These are actual executions of synthetic Python handlers, not live LLM results.
The default script starts a fresh temporary store. Use `--db PATH` to retain
records; reusing an existing database may already have a warm cache.

The component report has `monitor_states`, `synthetic_probe_calls`,
`live_provider_calls`, `message_bytes`, `duplicate_message_inserted`, and compact
`runs` summaries. These are not `RunResult` objects; give this report its own
import adapter. There are no HTTP drift-monitor or mailbox endpoints today.

For real hosted inference with configured credentials:

```powershell
.venv\Scripts\python.exe core\examples\live_comparison.py --provider openai --model YOUR_MODEL_ID --output-dir .canopy\comparisons
```

Each invocation writes a fresh subdirectory with `report.json`. It contains
`task`, `provider_configuration`, `configuration`, `execution_order`, `summary`,
and full `runs` keyed by `baseline_first`, `baseline_repeat`, `governed_cold`,
`governed_warm`. Support import of this report without assuming that the current
HTTP instance owns those run IDs. Linking to an HTTP run needs a matching store
and tenant. Hosted sample inputs are invented support-ticket data.

Anthropic, Gemini, OpenAI-compatible endpoints, and native `pollard-jev`/`jev`
are documented in the live model guide. Native Pollard-Jev needs a separate
Python environment and prepared model weights; its sample is a numeric inventory
decision. Actual local OpenJEV inference has not yet been verified here. Contract
tests using a synthetic encoder must never appear as a real inference demo.

## HTTP integration details

| Request | Response / behavior |
| --- | --- |
| `POST /runs` | Body exactly `{task, mode?, cache_enabled?, replay_run_id?, approval_ids?}`; synchronous `RunResult` response |
| `GET /runs?limit=100&offset=0` | Tenant-scoped `RunSummary[]`, newest first; limit 1–100 |
| `GET /runs/{id}` | `RunDetail`: summary fields plus `task`, nullable `result`, and `steps` |
| `GET /runs/{id}/events?after_sequence=-1&limit=1000` | Ordered `RunEvent[]`; exclusive sequence cursor; limit 1–1000 |
| `GET /approvals/{id}` | Binding snapshot/digest, status, reviewer, timestamps, reason, consumed timestamp |
| `POST /approvals/{id}/decision` | Body exactly `{approved: boolean, reason?: string}`; updated approval detail |

There is no async-start acknowledgment, SSE, WebSocket, cancel, resume, advisor,
provider-list, or configuration-mutation endpoint. Show a submitting state while
`POST /runs` is pending; it returns the run ID with the final result. Do not show
fabricated live token streaming or percentage completion. A list refresh can
discover in-progress records, but concurrent submissions cannot safely be matched
to a browser request from task ID alone. Prefer the returned run ID.

For a known run, optional polling every 0.5–1 second is a UI choice, not a server
guarantee. Fetch events after the last sequence; deduplicate by `(run_id,
sequence)`. Drain full 1,000-event pages, refresh detail on terminal completion,
then stop polling. Unknown event types should remain inspectable as JSON.

The fixed service context is **not HTTP authentication**. Anyone reaching this
loopback service acts as the configured principal, including demo approvals.
There is no CORS middleware. Use a local frontend development proxy with a
same-origin browser path forwarded to `127.0.0.1:8000`, or a separately configured
trusted host. A different port is a different browser origin. Do not place model
API keys in browser code or public frontend environment variables.

Actor, tenant privileges, permissions, policy, budgets, and model/prompt authority
are server-owned. The frontend cannot change them with request-body fields or
headers. Budget sliders and policy editors must stay read-only/prototype until
an authorized configuration interface exists. Default local-demo budget is ten
steps; the zero-step refusal scenario is supplied by `synthetic_demo.py`.

Errors: 403 means a tenant/permission denial; 404 includes missing or foreign
records; 409 includes replay integrity/incompleteness and approval conflicts;
422 means invalid input. Approval lookup can return 503 if no store is configured.
HTTP 200 can still mean `blocked`, `awaiting_approval`, or `failed`: use the
response status, not HTTP success, to determine the business outcome. Do not
automatically repost a timed-out run: external effects may have occurred.

## Data and metric rendering

Use [JSON Schemas](../core/schemas/) for `Task`, `RunResult`, `RunEvent`, and
`RunConfig`; use live OpenAPI for HTTP-only models. Core contracts use
`schema_version: "1"`; timestamps carry time zones.

| Model | Fields that matter to the UI |
| --- | --- |
| `RunSummary` | `run_id`, `task_id`, `tenant_id`, `mode`, `status`, `started_at`, nullable `finished_at` |
| `RunResult` | Summary fields plus `output`, `steps`, `usage`, `policy_version`, `evidence_versions`, `reused_steps`, `invalidated_steps`, nullable `replay_source_run_id`, `synthetic`, nullable `reason` |
| `StepResult` | `step_id`, `status`, `output`, nullable `reason`, `cache_key`, `source_run_id`, and `usage` |
| `RunEvent` | `run_id`, `sequence`, `timestamp`, `event_type`, nullable `step_id`, open JSON `payload` |

Run statuses are `completed`, `blocked`, `awaiting_approval`, `failed`; summaries
also use `running` while no terminal result exists. Step statuses additionally
include `reused` and do not include `running`. A dispatched step can be shown as
visually active based on events, without changing its stored status contract.

- Render token counts as unknown when `usage.tokens_known` is false, even when
  the underlying numeric fields are zero. Use `elapsed_known` similarly.
- `cost_usd` and `energy_j` can be null: show “not measured,” never `$0` or `0 J`.
- `model_calls` counts attempted model-handler dispatches, including synthetic
  handlers and failures before paid inference. Label synthetic runs prominently.
- Separate Canopy `reused_steps`, provider `provider_cached_input_tokens`, and
  historical replay. They are different mechanisms.
- Show historical usage from reuse/replay events as provenance, not current-run
  spend. Do not add event usage to `RunResult.usage`; that would double-count.
- OpenJEV local NLI input-pair tokens are not comparable billing units to hosted
  LLM tokens. Native output tokens are zero because no text is generated.
- The comparison CLI reports no independent quality score or savings percentage.
  Display a comparison only with outcome and measurement basis alongside usage.

## Event-to-screen mapping

| Existing event | UI use and actual payload |
| --- | --- |
| `runtime_started` | Run began; `mode`, `budget_trace_id` |
| `evidence_invalidated` | Highlight affected nodes from `step_ids` |
| `evidence_checked` | Evidence rows: `evidence_id`, `valid`, `status`, `reason`; no numeric freshness score in this event |
| `policy_checked` | `status` is `allow`, `deny`, or `approval_required`; show `reason` |
| `approval_required` | Open review drawer using non-null `approval_id`; show `reason` |
| `budget_admitted` / `budget_refused` | Admission `trace_id`; refusal `reason` and `meter` |
| `step_dispatched` | Active handler and `synthetic` flag |
| `budget_settled` | Actual `usage`, `ok`, `reason`, `trace_id`, `unknown_metrics` |
| `step_reused` | Reuse badge, `cache_key`, `source_run_id`, `historical_usage` |
| `step_completed` / `step_failed` / `step_blocked` | Outcome and `reason`; obtain output from recorded result |
| `replay_loaded` | `source_run_id`, `historical_policy_version`, `inspection_policy_version`, `historical_usage` |
| `canopy.step.claimed` | Durable claim, not proof of success |
| `canopy.step.recorded` | Authoritative step snapshot in `payload.result` |
| `canopy.run.finished` | Terminal marker with `result_digest`; fetch run detail for final result |

## Approval flow to demonstrate

Start a separate local demo using `--require-approval` (optionally a distinct
`--port` and `--db`). Submit a fresh synthetic request. Read `approval_id` from
the `approval_required` event, then fetch `/approvals/{id}`. If the ID is null,
show approval service unavailable rather than an actionable review control.

Show the exact `binding` plus `binding_digest`, actor/tenant, handler/version,
arguments, dependency outputs, evidence/policy snapshot, `expires_at`, status,
and `consumed_at`. Original handler inputs are nested under
`binding.arguments.inputs.handler_inputs`; trusted runtime revisions sit beside
them under `runtime_context`. Do not hide these details behind a generic action
description. The server, not the browser, supplies reviewer identity.

Post `{approved: true, reason: "Reviewed synthetic demo proposal"}` or a denial.
Approval records use `pending`, `approved`, `denied`; expiry and consumption are
separate timestamp fields, not extra enum statuses. An approved record alone
does not prove successful execution. After approval, explicitly repost the
unchanged original task/mode with `approval_ids: {read_catalog: approvalId}`.
This creates a **new run**, rather than resuming the blocked one. Changed
bindings, expired approvals, and consumed approvals cannot authorize another
dispatch. Show the two run IDs separately.

## Advisor prototype: agreed direction, not implemented

See the [advisor design](ADVISOR_DESIGN.md) for detection and evaluation details
and the [benchmark handoff](BENCHMARK_HANDOFF.md) for the measurement boundary.

Use categories `business_decision`, `tool_selection`, and `next_step` with proposed
lifecycle states `candidate`, `needs_mapping`, `evaluating`, `validated`,
`not_beneficial`, `unsupported`. These are design labels, not current backend
enums or routes. Cards should identify the workflow/step and revisions, candidate
choices, observed evidence, recommendation reasons, missing mappings, and the
evaluation needed. Do not attach an automatic-enable action to the current API.

The proposed detector reads recorded calls in the background, groups recurring
operations, and checks inexpensive structural signals first. An optional bounded
semantic assessment runs on selected examples per operation version, not every
request. Display analyzed sample count, period, and analysis usage if eventually
measured; do not claim negligible overhead without measuring it.

Example fixture cards: classify a ticket into known queues; select a registered
read-only tool from a known set; choose `retrieve`, `clarify`, or `finish` from a
fixed agent state. Distinguish selecting a tool from generating its arguments,
and choosing a next step from generating or running an arbitrary plan. Current
native Pollard-Jev accepts numeric observations and explicit hypotheses; semantic
text mapping and dynamic agent execution both need core work. Entailment scores
are not calibrated correctness probabilities or permission to execute a tool.

Proposed evaluation views should show independent correctness, consequential
errors, coverage/abstention, fallback rate, latency including fallbacks, and
measured resource costs plus sample count. Candidate feasibility and validated
benefit are separate claims. A recommendation may prefer a deterministic function
or retained LLM when appropriate. Use null/pending for unavailable measurements;
fixture numbers must be labeled synthetic and cannot become headline savings.

## Demo sequence and completion checklist

Show cold execution, unchanged reuse, a catalog change that reruns only affected
work, then historical replay with zero new handler calls. Open an approval
binding and execute the approved task as a new run. Use the imported refusal and
component reports to show budget containment, probe-driven drift containment,
and actual message-byte counts/deduplication. Message byte savings can be negative
and say nothing by themselves about tokens, energy, or latency.

Finish with one real provider comparison report if available, then explicitly
switch to the proposed advisor view. Explain the next experiment to validate a
business decision, a tool choice, and a next-step decision. Do not imply that
the generic catalog synthetic fixture already implements those agent routes.

Before demo day: check API/proxy connectivity, fresh evidence, matching tenant,
unknown metric labels, reuse provenance, approval rejection/consumption, and
replay labeling. Every displayed result must have a data origin: live API,
imported measured report, synthetic execution, or proposed prototype fixture.
