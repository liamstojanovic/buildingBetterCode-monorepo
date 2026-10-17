# Offline advisor

The offline advisor inspects existing Canopy model-step recordings for business
decisions, tool selection and authored next-step choices that could merit OpenJEV
evaluation. It reports structural evidence and remaining work. It does not run a
model, evaluate accuracy, switch a backend or execute a tool.

The [JEV evaluation and communication demo](../../docs/JEV_COMMS_DEMO.md) is the
next step after discovery: explicit mappings, actual local classifier results,
and measured message batching. Those artifacts have separate formats and do not
change the original advisor report or activate a backend replacement.

## Run it

After installing `core`, run from the repository root:

```powershell
.venv\Scripts\python.exe -m canopy.advisor_cli --db .canopy\runs.db --tenant-id demo-tenant --output .canopy\advisor-report.json
```

The database must already contain Canopy recordings. Analysis reads it without
creating runs or altering the cache. Use a new output filename; the CLI refuses
to overwrite an existing report.

Alternatively analyze a full `report.json` from `live_comparison.py`:

```powershell
.venv\Scripts\python.exe -m canopy.advisor_cli --report .canopy\comparisons\YOUR_RUN\report.json --tenant-id YOUR_TENANT --output .canopy\advisor-comparison.json
```

Pass exactly one source and its tenant. Without `--output`, JSON goes to standard
output. An imported report is validated input, not independent proof of recording
integrity. Provider credentials and OpenJEV weights are unnecessary.
Comparison imports are bounded to 32 MiB and require the complete four-run export
with effective configuration; compact summaries or component reports are not
accepted. The output's parent directory must already exist.

Generate a credential-free demo using actual synthetic recorded executions:

```powershell
.venv\Scripts\python.exe core\examples\advisor_demo.py --output-dir .canopy\advisor-demo
```

Each invocation creates a new `run-*` subdirectory containing
`advisor-report.json`, `demo.json`, `recordings.sqlite3` and its Pollard sidecar.
The printed tenant is `advisor-demo`; use that tenant when reanalyzing its store.
The advisor report is a real analysis result over synthetic Python handlers.
Do not label the observations as hosted inference or successful OpenJEV inference.
The demo includes seven groups across four runs: native-shaped operational review,
text classification, tool selection, next-step selection, generation, an explicit
threshold rule and an unmapped decision. They produce 28 synthetic observations,
21 fixture dispatches and seven reuses. The native-shaped `review_operations`
finding is `candidate` / `evaluate_openjev`, with synthetic predetermined output;
it demonstrates structural detection, not real inference or measured quality.
See the paths printed by the example for import.

The [retained hosted demo](../examples/recordings/advisor-live-2026-10-17/README.md)
contains actual provider recordings on invented business, tool-selection and
next-step cases, portable frontend bundles and a combined advisor report. It
also preserves a wrong next-step selection and raw JSON-format violations.
Use its recorded outputs for playback; generate fresh fixtures for new calls.

On macOS/Linux use `.venv/bin/python`. There is no advisor HTTP endpoint; a
frontend imports JSON, or a trusted application invokes the Python API.

## Bound and prioritize analysis

| CLI option | Default | Meaning |
| --- | --- | --- |
| `--max-runs` | 1000 | Maximum inspected runs |
| `--max-groups` | 200 | Maximum retained operation groups |
| `--max-samples-per-group` / `--max-samples` | 8 | Maximum retained references per group |
| `--max-steps-per-run` / `--max-steps` | 200 | Maximum inspected steps per run |
| `--max-step-chars` | 65536 | Bound on an analyzed step representation |
| `--max-seconds` | 30 | Cooperative elapsed analysis budget |
| `--rank-by` | `model_calls` | Rank by `model_calls`, `elapsed_ms` or `cost_usd` |

Time limits apply inside analysis and are checked between reads/steps, not enforced
as operating-system I/O deadlines. The CLI's `analysis.elapsed_ms` includes source
setup/import, opening, validation/verification and analysis; it excludes output
serialization/publication. Python API timing starts inside the analysis function
and therefore excludes caller setup. Benchmark the entire command for end-to-end
overhead. The separate 32 MiB import cap does not create an import time deadline.

Render `analysis.limit_reasons`, skipped counters and `sample_limit_reached`.
Reaching `max_runs` conservatively adds that limit reason without probing another
record: further records may exist, even when the source happens to contain exactly
the cap. More observations may contribute metrics than fit in retained samples.
Limit-reached reports may be partial. Ranking reflects recorded work, not savings.

## Python API

`canopy.advisor.analyze_store(store, tenant_id=..., limits=...)` accepts an
`ExecutionStore`. `analyze_runs(records, tenant_id=..., limits=...)` accepts an
iterable of `RecordedRun` objects. Both return `AdvisorReport`; limits use
`AdvisorLimits`. Keep scheduling and retention outside runtime execution.

Store analysis verifies each read recording. Arbitrary Python input remains
caller-supplied. Inspect `source` and `source_integrity` before comparing origins.
Replay runs are excluded. Reused steps are counted without adding historical
inference usage again. An analysis fingerprint is never an execution-cache key.

## Optional declarations

Automatic discovery uses recorded structure, including bounded enums/catalogs and
a single named `questions` entry with `type: "choice"` and a nonempty `criteria`
mapping. For example, `questions.priority.criteria` can declare `high` and `normal`
with authored criteria text. Ambiguous or conflicting contracts still require
mapping. Criteria prose is not automatically converted into native JEV evidence.
Output inspection accepts strict JSON, including one complete standalone Markdown
JSON fence; it does not extract JSON from surrounding prose or multiple fences.
A choice accompanied by a generated reason remains a partial substitution.

Applications can declare their known decision contract under
`Task.metadata.advisor.steps[step_id]`. For example,
this is a `Task.metadata` value, not a complete task:

```json
{
  "advisor": {
    "steps": {
      "choose_tool": {
        "category": "tool_selection",
        "template_id": "support.tool-router",
        "template_version": "1",
        "instance_fields": ["ticket"],
        "choices": ["lookup_order", "search_help", "ask_customer"],
        "output_field": "tool",
        "evidence_kind": "text",
        "tool_catalog_version": "1",
        "argument_source": "existing"
      }
    }
  }
}
```

Here `ticket` must be a top-level input field containing instance data; instructions
and tool definitions stay outside it. Template ID and version must be supplied
together, and `instance_fields` requires that template declaration. Explicitly
listed instance fields may vary while their shape remains part of the group.
Unlisted inputs stay exact. Protected catalogs and schema fields, including
`questions`, and instruction-bearing inputs such as `prompt`, `messages`,
`system`, `system_prompt`, `developer` and
`developer_prompt`, cannot be stripped to force grouping. Keep ticket/text/state
data separate from instructions; unknown monolithic prompts remain exact. Without
declarations, grouping is conservative. Update versions when operation meaning
changes. Model, handler, prompt and structural revisions remain in the fingerprint.

For a recognized native `decision_request` under a declared template, the analyzer
already knows its typed instance fields: values, request identity and timestamps
may vary while question, choices, observation features/units and value shapes
remain in the group. This exception does not generalize to arbitrary prompt text.

Hints are analysis evidence, not permission grants, executable mappings or output
validation. Categories are `business_decision`, `tool_selection`, `next_step` and
`unknown`; evidence kinds are `native`, `text`, `structured` and `unknown`.
Optional `candidate_hypotheses` supplies hypothesis text. `explanation_required`,
`generation_required` and `deterministic_rule` identify work a selector may not
replace. `state_schema_version` describes next-step state; `argument_source`
distinguishes `existing`, `generated` and `unknown` tool arguments. See the strict
[DecisionDescriptor schema](../schemas/DecisionDescriptor.schema.json).

## Read findings and metrics

Recommendations contain operation identity and revisions, `grouping_basis`, a
`finding`, `observed` metrics and bounded sample references. Findings include
category, status, strategy, choices, signals, reasons, blockers, required changes,
`partial_substitution` and `native_compatibility`.

| Field/value | Interpretation |
| --- | --- |
| `status: candidate` | Structural lead that still needs independent evaluation |
| `status: needs_mapping` | Evidence/candidate/output or retained-work mapping incomplete |
| `status: unsupported` | Current evidence does not justify the proposed substitution |
| `strategy: evaluate_openjev` | Investigate an OpenJEV alternative |
| `strategy: deterministic_function` | Evaluate ordinary deterministic logic |
| `strategy: retain_llm` / `retain_backend` | Retain generation/current processing |
| `native_request_shape` | Structural native match, not proven successful inference |
| `requires_text_adapter` / `requires_mapping` | Current bridge cannot consume the workload directly |

Choosing a tool is separate from generating arguments and executing it. Choosing
an authored transition is separate from inventing a plan. Native Pollard-Jev
accepts numeric observations; a text-classification lead does not add a text
adapter. Partial substitutions must retain and account for remaining work.

Observed metrics cover recorded calls, outcomes, resources, unknown counters and
`synthetic_observations`. Null resource totals mean not fully known; render them
as unknown. Samples reference original runs and steps; importing an advisor report
does not import those runs into an HTTP store. Full prompts, raw evidence and
hypothesis text are omitted, but identifiers/choice labels remain application data.

Each recommendation has `evaluation_status: not_evaluated`, `measured_quality:
null` and `estimated_savings_usd: null`. Analysis records its elapsed time and zero
model/tool calls; dollar and energy cost remain unknown. No suitability probability
or savings estimate is calculated.

Wire contracts: [AdvisorReport](../schemas/AdvisorReport.schema.json),
[AdvisorLimits](../schemas/AdvisorLimits.schema.json). See the
[frontend handoff](../../docs/FRONTEND_HANDOFF.md) for rendering and demo guidance.

## Remaining work

There is no scheduler, persisted recommendation cache, semantic analysis model,
HTTP route, replacement evaluation, text adapter, automatic routing or fallback.
Saving reports supports manual comparison; reruns recompute bounded heuristics.
Changes to a template/catalog/model require fresh analysis and any later
substitution evaluation. The [benchmark handoff](../../docs/BENCHMARK_HANDOFF.md)
separates detection quality from replacement quality and whole-workflow efficiency.
The [design reference](../../docs/ADVISOR_DESIGN.md) describes later activation.
