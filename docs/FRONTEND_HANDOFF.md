# Frontend integration and demo handoff

The frontend teammate owns `web/`. This guide separates live HTTP features,
importable offline advisor reports, and future backend routing.
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
| Optimization advisor | Implemented offline discovery and strict JSON report | Import generated report; findings are unevaluated; no live advisor routes |

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
5. **Offline advisor:** business decisions, tool selection, and next-step findings
   with reasons, required changes and evaluation gaps, imported from actual analysis.

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

## Retained hosted demo: all three advisor categories

The [retained hosted demo](../core/examples/recordings/advisor-live-2026-10-17/README.md)
contains committed, credential-free exports from actual OpenAI, Anthropic and
Gemini calls on invented business, tool-selection and next-step cases. Follow its
artifact index for the frontend comparison bundles, sanitized comparison exports
and combined advisor report. These are historical recordings: playback and
offline analysis require neither provider credentials nor OpenJEV weights.

The retained set contains nine comparisons, 36 runs and 84 step observations:
63 hosted model calls and 21 warm reuses. Each provider contributes the existing
business-priority case plus three tool-selection and three next-step cases.
The 54 new selection calls matched the authored expected choice 53 times after
normalization; only 24 also met the requested bare-JSON contract. These are
fixture checks, not measured JEV quality. See the saved
[fixture checks](../core/examples/recordings/advisor-live-2026-10-17/fixture-checks.json)
for individual outcomes; the old business-priority recordings are excluded from
those 54 checks.

Use **Import recording** to load one `canopy-comparison/v1` bundle. Compare cold
execution and unchanged warm reuse, then open each step's recorded output and
usage. Each bundle preserves one provider and one workload; import another bundle
to change either. Do not combine providers into baseline/Canopy lanes or treat
their different recordings as a controlled provider-performance comparison.

For a useful failure example, open OpenAI's next-step bundle and choose the cold
comparison. Inspect `choose_retrieval` in the baseline lane (`baseline_cold` in
the bundle, `baseline_first` in the core export): the model chose `respond`
although the recorded state had no approved evidence and the authored expectation
was `retrieve`. Preserve this result in the demo. Different choices in later runs
do not show governance fixing decision quality; the runtime does not enforce
transition eligibility. The committed fixture checks separately assess normalized
choice and requested bare-JSON format; fenced JSON can satisfy the former while
failing the latter. Those checks are not an independent quality benchmark, and
the frontend bundles' `evaluations` remain empty.

Next open the combined `AdvisorReport` and filter its findings by business
decision, tool selection and next step. The current frontend supports recording
import; its advisor view is still an integration task described below. Until that
view is implemented, use the saved advisor JSON for this part of the demo rather
than feeding it to **Import recording**. Show selected choices, reasons, mapping
gaps, measured work and `not_evaluated` side by side.

The [combined advisor report](../core/examples/recordings/advisor-live-2026-10-17/advisor-report.json)
contains 21 provider-specific groups over seven distinct cases: three business,
nine tool-selection and nine next-step groups. All have `evaluate_openjev` /
`needs_mapping`; only the three business groups have `partial_substitution: true`.
These are suggestions for evaluation, not 21 validated replacement decisions.
All nine frontend bundles have passed the current `parseBundle` and
`RecordedRunClient` path: 36 run playbacks and 618 events, with network disabled.

Keep the two provenance labels visible: `task.metadata.sample_data: true` means
the business cases are invented, while `RunResult.synthetic: false` means the
handler made actual model calls. Warm results reuse those real outputs. None of
the tool names was executed and the next-step choices did not modify the static
DAG. These are hosted selection recordings, not an autonomous agent trajectory.
The support-priority result also includes a generated explanation; replacing its
selection alone would leave that explanation work. All JEV recommendations still
need mapping and independent replacement evaluation.

To export a new retained comparison for the current frontend, use its existing
converter while the original SQLite stores and events are available:

```powershell
.venv\Scripts\python.exe web\scripts\import-comparison.py .canopy\comparisons\YOUR_RUN\report.json --output web\public\recordings\my-comparison.json
```

That script writes only inside `web/`; leave this conversion to the frontend
owner. It accepts repeated reports only for the same task snapshot and model
fingerprint, so run it separately for different providers or categories. The
committed demo bundles already include the recorded events and need no database.
The sanitized comparison JSON alone cannot reconstruct those events through this
converter. Reanalyze a comparison with the advisor CLI instead, as shown in the
demo guide.

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

## Offline advisor: implemented report import

Generate the demo from the repository root:

```powershell
.venv\Scripts\python.exe core\examples\advisor_demo.py --output-dir .canopy\advisor-demo
```

Each invocation prints an `advisor_report_path` in a fresh `run-*` directory under
the selected output folder, with tenant `advisor-demo`. Import that
`advisor-report.json`; `demo.json` separately contains synthetic task/run records
and is not itself an `AdvisorReport`. The SQLite store and its Pollard sidecar
are retained for inspection/reanalysis. This is a real
analysis over recorded synthetic handlers. It needs no credentials or model
weights and covers business, tool-selection and next-step findings, plus retained
generation and deterministic alternatives. See the [advisor guide](../core/docs/advisor.md)
for analyzing an existing database or live-comparison report instead.

The demo contains seven recurring groups and four runs: 28 synthetic observations,
21 fixture dispatches and seven reuses. Start with `review_operations`, whose
typed numeric request yields `candidate` / `evaluate_openjev` /
`native_request_shape`. Its answer is predetermined synthetic data; the candidate
does not establish real OpenJEV inference or quality. Then inspect the text,
tool-selection and next-step mapping gaps, generation, deterministic threshold,
and unmapped-decision findings.

Give advisor reports their own import adapter, distinct from run and component
reports. Validate against [AdvisorReport.schema.json](../core/schemas/AdvisorReport.schema.json)
and check `report_type: "canopy.offline_advisor"`, `schema_version: "1"` and
`analyzer_version: "offline-advisor-v1"`. Render unfamiliar versions as unsupported
instead of interpreting arbitrary JSON as this schema.

### Integration points in the current frontend draft

The frontend draft integrated at `8b7d56b` has run comparison import but no advisor
import. These are the concrete places to extend in the frontend teammate's work:

1. [scripts/generate-contracts.ts](../web/scripts/generate-contracts.ts) currently
   lists only `Task`, `RunEvent`, `RunResult` and `RunConfig`. Add `AdvisorReport`
   to that explicit list; exporting a new Python schema does not automatically
   generate its TypeScript counterpart. Add `DecisionDescriptor` and `AdvisorLimits`
   as separate generated entry points if the UI needs them independently. From
   `web/`, use `bun run contracts` and then `bun run contracts:check`; generated
   files live under `src/api/generated/` and should not be edited by hand.
2. [src/api/validation.ts](../web/src/api/validation.ts) currently compiles only
   Task and RunResult with Ajv2020 and formats. Compile the advisor schema there
   (or in an equivalent advisor module) for actual runtime validation; generated
   TypeScript alone does not validate imported JSON. Export the report type through
   [src/api/types.ts](../web/src/api/types.ts) or a dedicated advisor type module.
3. [src/api/recorded.ts](../web/src/api/recorded.ts) exposes `parseBundle`, which
   accepts only `format: "canopy-comparison/v1"`, with `recordings` containing
   `task`, `result` and `events` and mandatory `baseline`/`warm` entries. It rejects
   advisor reports. Introduce a separate advisor parser/import path rather than
   casting an advisor report to `ComparisonBundle` or fabricating run events.
4. [App.tsx](../web/src/App.tsx) currently routes `importRecording` through
   `loadBundle`/`parseBundle` and resets the run-comparison state. Keep advisor
   report state separate from those baseline/Canopy lanes. Wire an advisor-specific
   import action/view through [Console.tsx](../web/src/components/Console.tsx),
   whose existing `onImport` means recording import and `onImportTask` means a live
   task. Preserve the existing import size/error treatment and display the parsed
   report's provenance and limits in the advisor view.

These three JSON formats are distinct; none is interchangeable merely because
it contains Canopy results:

| Artifact | Discriminator / structure | Current consumer |
| --- | --- | --- |
| Frontend session/core fixture bundle | `format: "canopy-comparison/v1"`, `recordings` with task/result/events | Frontend `parseBundle` |
| Core live comparison export | `schema_version: "1"`, task, effective configurations, four `runs` | Offline advisor CLI `--report`; `scripts/import-comparison.py` converts retained reports plus databases/events for the frontend |
| Offline advisor report | `report_type: "canopy.offline_advisor"`, recommendations/analysis/limits | New frontend advisor import to implement |

The frontend's `exportSession()` and `fixtures:core` bundle are **not** accepted by
the advisor CLI's `--report` option. Analyze their underlying Canopy database with
`--db` when available, or use a complete core live-comparison export. Likewise,
the advisor demo's `demo.json` manifest is not a frontend comparison bundle.

[HttpRunClient](../web/src/api/client.ts) is for run execution/observation and
approvals. Offline advisor import needs no new HTTP call or `RunClient.execute()`.
The client's optional async/SSE probes do not establish that those endpoints exist
in the current core server; they also do not supply an advisor service.

For frontend verification, use a generated seven-group report to check the three
decision families, the native-shaped candidate, mapping gaps and retained-work
findings. Include null resources, a limit-reached report and invalid/wrong-format
JSON. Keep existing comparison import/export behavior covered separately. Available
checks in `web/package.json` are `bun run contracts:check`, `bun run typecheck`,
`bun run test` and `bun run build`.

### Advisor fields to render

| Report data | UI treatment |
| --- | --- |
| `tenant_id`, `source`, `source_integrity`, `generated_at` | Origin banner; import validation is not source-recording integrity verification |
| `analysis` and `limits` | Show scanned runs/model steps, elapsed analysis time, skipped counts and limit reasons; zero analysis model/tool calls |
| `recommendations[].finding.category` | Filter by `business_decision`, `tool_selection`, `next_step` or `unknown` |
| `finding.status` | Exactly `candidate`, `needs_mapping` or `unsupported` |
| `finding.strategy` | Evaluate OpenJEV, deterministic function, retain LLM or retain backend |
| `finding.reasons`, `signals`, `blockers`, `required_changes` | Explain the finding and work required; render as text, never HTML |
| `finding.choices`, `partial_substitution`, `native_compatibility` | Show finite options, retained work and adapter/mapping gaps |
| Identity/revisions and `group_fingerprint` | Explain which operation was grouped; support comparison between saved reports |
| `observed` and `samples` | Recorded work and bounded source references, not prospective savings |
| `sample_limit_reached`, `analysis.limit_reasons`, `limitations` | Visible partial-analysis notices; samples need not cover every observation |
| `evaluation_status`, `measured_quality`, `estimated_savings_usd` | Always not evaluated/null/null; show “Not evaluated” |

Map enum strategies `evaluate_openjev`, `deterministic_function`, `retain_llm` and
`retain_backend` to the labels above. Unknown metric totals stay unknown; do not
turn null cost into zero. Show `synthetic_observations` and total `observations`
together, since a report can contain mixed origins. `analysis.cost_usd` and
`energy_j` remain null despite zero model calls: CPU/I/O resources were not priced.
Observed calls can include failures and synthetic handlers; neither proves paid
inference or successful work. Reused-step usage is current reuse cost, not the
historical call's usage.

Use `recommendation_id`/`group_fingerprint` as report identities, not execution
cache keys. `samples` contain run/step references, mode, status, synthetic marker
and possibly a selected choice; they do not contain full prompts/evidence.
Only link a sample to `/runs/{id}` when the service store and tenant match the
source. An imported report does not populate that service's database.

Suggested interaction: import a report, filter by category/status, select a card,
then inspect reasons, blockers, choices, revisions, observed work and references.
The default sort is the report order, based on its recorded ranking metric.
Do not label that order as predicted ROI or suitability probability.

In CLI-generated reports, `analysis.elapsed_ms` includes source setup/import,
opening/verification and analysis but excludes output serialization. The Python
API (also used by the demo) measures its analysis function, excluding caller
setup. `max_seconds` bounds only cooperative analysis work; it is not a hard
deadline for imports or I/O. A `max_runs` limit reason means the cap was reached
and further records may exist; the analyzer does not read beyond it to confirm
exhaustion. Present “Limit reached; report may be partial,” not a definite claim
that extra records were skipped.

Tool choice is separate from generating arguments and execution. Next-step
selection is separate from inventing or executing a plan. Text leads need a
future adapter: native Pollard-Jev currently accepts numeric observations and
explicit hypotheses. Even `native_request_shape` is structural compatibility,
not proven inference quality or authorization.

No enable-route, evaluation-run or server-refresh control is wired up. The current
release has no advisor HTTP endpoint, scheduler, automatic report cache,
replacement evaluation, dynamic backend selection or fallback. Retaining two
reports permits comparison; it does not provide automatic validity monitoring.
`evaluating`, `validated` and `not_beneficial` are future concepts, not current enums.
See [advisor design](ADVISOR_DESIGN.md) and [benchmark handoff](BENCHMARK_HANDOFF.md).

## Demo sequence and completion checklist

Show cold execution, unchanged reuse, a catalog change that reruns only affected
work, then historical replay with zero new handler calls. Open an approval
binding and execute the approved task as a new run. Use the imported refusal and
component reports to show budget containment, probe-driven drift containment,
and actual message-byte counts/deduplication. Message byte savings can be negative
and say nothing by themselves about tokens, energy, or latency.

Finish with the retained hosted bundles above, then review their combined offline
advisor report in the advisor view when implemented, or as saved JSON. Open a
business, tool and next-step finding and show each missing mapping/evaluation.
Use the separate synthetic advisor demo to contrast retained generation with a
deterministic alternative. Explain the next independent replacement experiment.
The analyzer works now; neither the recorded selections nor the generic catalog
fixture implement adaptive agent routes.

Before demo day: check API/proxy connectivity, fresh evidence, matching tenant,
unknown metric labels, reuse provenance, approval rejection/consumption, and
replay labeling. Every displayed result must have a data origin: live API,
imported measured report, synthetic execution, or proposed prototype fixture.
