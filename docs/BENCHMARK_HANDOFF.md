# Benchmark teammate handoff

The benchmark owns representative workloads and independent correctness scoring.
The core supplies recorded execution, provider adapters and governed reuse. Start
with one shared workload and retain the complete outputs before expanding the
suite. The frontend consumes the resulting records; it should not calculate or
invent missing measurements.

## What runs today

The implemented entry point is
[`core/examples/live_comparison.py`](../core/examples/live_comparison.py).
Detailed provider setup is in
[`core/docs/live_models.md`](../core/docs/live_models.md), and Python runtime
contracts are in [`core/docs/api.md`](../core/docs/api.md).

From the repository root, using Python 3.11 or 3.12:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r core/requirements.lock
.venv\Scripts\python.exe -m pip install -e "core[dev,models]"
.venv\Scripts\python.exe core/examples/live_comparison.py --write-sample-task support-task.json
```

The last command writes an invented support task without making a model call.
Edit the workload and evidence, supply the provider key through its environment
variable, then choose a model explicitly:

```powershell
.venv\Scripts\python.exe core/examples/live_comparison.py --provider openai --model YOUR_MODEL_ID --task-file support-task.json --output-dir .canopy/comparisons
```

On macOS/Linux use `.venv/bin/python`. The CLI supports `openai`,
`openai-compatible`, `anthropic`, `gemini`, and `pollard-jev` (`jev` alias).
Compatible endpoints require `--base-url`; see the provider guide for key names
and configuration. Keep credentials and run databases out of Git.

The CLI accepts model-only DAGs whose steps use `handler: "model.generate"`.
It binds handler versions to the selected provider configuration. Mixed tools,
multiple providers or named model handlers require a trusted Python registry and
the example's `run_comparison(task, registry, model_fingerprint=..., ...)`.
The implemented adapters do not execute provider-suggested tools or actions.

Each invocation runs one effective Task/configuration in this fixed order:

| Run key | Behavior |
| --- | --- |
| `baseline_first` | Execute with Canopy result caching disabled |
| `baseline_repeat` | Execute the same task again with caching disabled |
| `governed_cold` | Execute against a separate empty Canopy store |
| `governed_warm` | Repeat against that governed store; eligible results may be reused |

Baseline still enforces authorization, explicit evidence expiry, approvals and
budgets. It is not an unrestricted agent. A normal one-step comparison makes
three model dispatches when governed warm successfully reuses its result.
Each `--output-dir` invocation creates a new unique subdirectory, preventing a
previous invocation from warming the new comparison's application cache.

The saved `report.json` includes `task`, credential-free
`provider_configuration`, `configuration`, `execution_order`, `summary`, and
complete `runs`. Step outputs, usage, failure reasons and provenance remain in
the full results and associated stores. Without `--output-dir`, the runner uses
temporary databases. Non-completed runs remain in the report; the CLI returns
exit code 1. No correctness score or percentage saving is calculated for you.

Hosted adapters return a `text` field and do not enforce a JSON output schema.
Define and version the benchmark's parser and invalid-output handling; do not
quietly repair a bad response with an unmetered LLM call. Saved sample evidence
expires after four hours. Use a genuinely current source snapshot, or an
explicitly generated synthetic fixture, rather than relabeling stale real data
as fresh to obtain a successful run.

## Retained hosted advisor cases

The [hosted advisor demo](../core/examples/recordings/advisor-live-2026-10-17/README.md)
and its artifact index provide credential-free comparison exports, frontend
playback bundles and advisor results across OpenAI, Anthropic and Gemini. The
recordings use invented business data (`sample_data: true`) with actual hosted
inference (`synthetic: false`). Playback and offline reanalysis make no new model
calls. The full source stores remain local; the committed sanitized comparison
exports can be imported by the advisor without those stores.

The retained set has nine comparisons, 36 runs, 84 step observations, 63 actual
model calls and 21 warm reuses. The six new tool/next-step comparisons account for
54 calls and 18 reuses; the three existing business comparisons supply the rest.
The new selection-only calls produced these
[authored fixture checks](../core/examples/recordings/advisor-live-2026-10-17/fixture-checks.json):

| Provider / recorded model | Calls | Normalized expected choice | Bare JSON object | Expected choice and strict requested JSON |
| --- | --- | --- | --- | --- |
| OpenAI / `gpt-4.1-mini-2025-04-14` | 18 | 17 | 18 | 17 |
| Anthropic / `claude-haiku-4-5-20251001` | 18 | 18 | 0 | 0 |
| Gemini / `gemini-2.5-flash-lite` | 18 | 18 | 7 | 7 |
| Total | 54 | 53 | 25 | 24 |

Expected choices were saved before inference. These counts exclude warm reuses
and the old business-priority case. All 29 fenced outputs matched the normalized
choice while violating the request for bare JSON. This is a small smoke study of
specific recorded models and cases, not a provider ranking.

The new tool-selection and next-step tasks each contain three distinct cases.
Their outputs select an identity from a declared finite set. No tool is executed,
no arguments are generated and no selected next step changes the static DAG.
The existing business-priority case also generates a reason; account for retained
explanation work when considering a partial substitution.
The next-step policy is an explicit ordered rule over supplied state, so include
a deterministic transition-rule baseline before investing in a JEV mapping.

The combined advisor report retains 21 provider-specific groups over seven cases:
three business, nine tool-selection and nine next-step groups. Every group is
`evaluate_openjev` / `needs_mapping` / `not_evaluated`; only the three business
groups are partial substitutions. Group count is not an independent case count
or evidence that JEV will outperform the original call or a deterministic rule.

Treat these cases as a reproducible connectivity, recording and detector demo.
Repeated baseline/cold/warm observations are repeated execution of the same
cases, not extra independent test cases. Authored expected choices are useful
fixture checks, not a held-out, independently labeled business-quality benchmark.
The fixed provider/run order is not a fair latency comparison; provider costs
remain unknown where no price was recorded. All JEV findings are unevaluated and
the demo performs no native OpenJEV inference or backend substitution.

The retained OpenAI `baseline_first` / `choose_retrieval` result chose `respond`
despite missing approved evidence; its authored expected choice is `retrieve`.
Keep this failure when reproducing the smoke study. Later choices differ, but
there is no runtime transition-eligibility check and no evidence that governance
caused a quality improvement. The fixture-check report separates normalized
choice matching from bare-JSON compliance: Markdown-fenced JSON can match the
choice while violating the requested output format. Keep the original text;
normalization must not erase those format failures. Frontend `evaluations` remain
empty because these are authored fixture checks, not independent scoring.

Use the demo guide's generation commands to create fresh invented source
snapshots before making a new metered comparison. Preserve the retained files
for playback; do not relabel their old evidence timestamps as current. Use the
exact recorded model identities in the artifact index when reproducing a result,
or record a model change explicitly. Extend these cases with independently
labeled missing-fact, no-tool, unavailable-tool, stop, refresh and review cases
before evaluating replacement quality.

## Current Pollard-Jev boundary

The native bridge uses `pollard-jev==0.13` in a separate interpreter because it
requires `pollard==1.6.0`; Canopy core uses `pollard==1.6.2`. Its supported model
is `AlexWortega/openjev/qwen3.5-4b-nli-v2`, pinned to Hugging Face revision
`552759daad712f1af6c4c13dabcb1e047886fc9c`.

Generate its distinct sample with:

```powershell
.venv\Scripts\python.exe core/examples/live_comparison.py --provider pollard-jev --write-sample-task replenishment-task.json
```

This sample contains invented numeric inventory observations and candidate
hypotheses. To execute it, first follow the separate environment, optional
`openjev` dependencies and explicit model-cache preparation instructions in the
[provider guide](../core/docs/live_models.md#run-the-native-pollard-jev-package).
The execution command, once those prerequisites exist, is:

```powershell
.venv\Scripts\python.exe core/examples/live_comparison.py --provider pollard-jev --model AlexWortega/openjev/qwen3.5-4b-nli-v2 --worker-python .venv-pollard-jev/Scripts/python.exe --model-cache-dir YOUR_CACHE --device cpu --timeout-seconds 600 --task-file replenishment-task.json --output-dir .canopy/comparisons
```

Runtime inference never downloads weights. A persistent worker is reused across
the four runs: `baseline_first` can include model loading, while
`governed_cold` refers to a cold Canopy cache, not a cold local model. Report both
startup and steady-state measurements. The worker serializes requests, so
throughput and queue delay need separate evaluation under concurrent load.

Native inputs must carry `inputs.decision_request`, including numeric
observations with units, validity and explicit choices/hypotheses. The CLI binds
these facts into Canopy evidence. The bridge does not turn arbitrary support
tickets, prose documents or dependency outputs into native observations.
General text classification or tool-selection evaluations need a designed
mapping or a future text-premise adapter; merely choosing `--provider jev` does
not make those tasks compatible.

Native output is a decision object with independent entailment scores and a
proposal. It is not generated prose or authorization. The package proposes an
argmax even for weak/tied scores, and its `evidence: "sufficient"` field is not a
validated acceptance rule. A future route needs independently calibrated
acceptance/abstention logic, not the package's robotics-policy defaults.

Real OpenJev model inference has **not** been verified in this workspace. Package
integration tests exercise actual contracts/provider/context checks with
explicitly synthetic encoders. Hosted smoke calls have verified connectivity
and accounting for selected OpenAI, Anthropic and Gemini models; they do not
establish comparative quality. Keep these claims distinct in the demo.

## Implemented offline advisor; evaluation owned by the benchmark

Offline discovery and strict recommendation reports are implemented. The
[advisor guide](../core/docs/advisor.md) documents Python functions, limits and
optional descriptors. Analyze a comparison without making another model call:

```powershell
.venv\Scripts\python.exe -m canopy.advisor_cli --report .canopy\comparisons\YOUR_RUN\report.json --tenant-id YOUR_TENANT --output .canopy\advisor-benchmark.json
```

Or use `--db PATH` for a recording store. Tenant is required and the output path
must be new. Imports are validated; only the store path verifies source recordings.
A credential-free example executes synthetic handlers and analyzes their records:

```powershell
.venv\Scripts\python.exe core\examples\advisor_demo.py --output-dir .canopy\advisor-demo
```

Output follows [AdvisorReport.schema.json](../core/schemas/AdvisorReport.schema.json).
States are `candidate`, `needs_mapping`, `unsupported`; each recommendation is
`not_evaluated`. Quality/savings are null and synthetic observations are counted.
No text adapter, replacement evaluation, calibrated abstention, backend switching
or fallback was added.

Discovery uses choices, enum schemas, catalogs, transitions and output clues.
Optional [DecisionDescriptor](../core/schemas/DecisionDescriptor.schema.json)
annotations identify template/version and explicitly named instance fields;
unlisted data stays exact and protected schema/catalog fields remain in the group.
Compare annotated and unannotated detection separately. Unannotated instances
may conservatively split instead of guessing prompt boundaries. Low output
cardinality is not proof of complete candidates. Saved reports allow comparison,
but there is no automatic recommendation cache or background invalidation.
Grouping fingerprints are analysis identities, never result-cache keys.

Evaluate the detector separately from the proposed replacement. A detector can
find a structurally plausible call whose OpenJev execution is inaccurate,
slower or more expensive. Independently label candidate groups as suitable,
partially suitable, unsuitable or requiring more information. Measure candidate
precision/recall, false positives, grouping errors and ranking benefit. Keep
discovery samples separate from the final held-out substitution test set.

Detection challenge cases should include short open-ended answers, missing or
incomplete choices, arbitrary arguments, required explanations, changed catalogs
under one template, changed prompt/model/schema versions, varying instance shapes,
malformed descriptors, different tenants, duplicate/replay records, mixed
synthetic/real observations, oversized inputs and unknown usage. Check that
mapping/argument/explanation gaps remain visible. Native-shape detection is not
successful inference.

Measure wall-clock time and memory around the entire command alongside
`analysis.elapsed_ms`. CLI elapsed time includes source setup/import, opening,
verification and analysis; it excludes serialization/publication. Python API
timing excludes caller setup. The `max_seconds` limit applies inside analysis,
cooperatively between reads/steps, not as hard I/O cancellation. Imports have a
separate 32 MiB size cap. Retain `limits`,
`limit_reasons`, skipped counts, samples, observation counts, source integrity
and analyzer version. Limit-reached reports do not establish exhaustive coverage:
`max_runs` is reported on reaching the cap without fetching an extra record to
check exhaustion, including when the source happens to contain exactly the cap.
Sweep input sizes and run/group/sample bounds; measure CPU/I/O separately from
the zero analysis model/tool-call counters. Price resources only with a stated
basis. Check that recording data/runtime behavior are unchanged. Replay is
excluded and reuse does not duplicate historical inference usage.

Future monitoring can evaluate stale recommendations, but there is no automatic
validity or drift mechanism in this release. See [the design](ADVISOR_DESIGN.md).

## Workloads and independent quality

Use versioned case sets with source provenance, expected outcomes, allowed
alternatives and explicit abstention conditions. Split related templates and
near-duplicates together to reduce train/validation/test leakage. Tune candidate
wording and acceptance thresholds on development/validation cases, then freeze
them before the held-out evaluation. LLM agreement alone is not ground truth;
use independent labels, domain rules where applicable and adjudicated ambiguous
cases. Record the labeling procedure and disagreements.

| Workload family | What to score | Required challenge cases |
| --- | --- | --- |
| Business classification | Correct label/set and abstention, plus any separately required explanation | Rare categories, paraphrases, missing facts, conflicting evidence, out-of-scope requests |
| Tool selection | Appropriate tool identity separately from valid arguments and safe execution | Correct tool absent, similar tool descriptions, changed schemas, unavailable or unauthorized tools, no tool needed |
| Next-step decisions | Allowed next step from the full declared state; eventual task success and total work | Stop/ask/refresh/review cases, repeated loops, stale state, prerequisites not met, newly needed actions outside the candidate set |

Choosing a tool name does not establish that JEV can extract its arguments,
invent a plan or safely execute it. A partial substitution must retain those
other steps and account for their cost. Next-step cases need the relevant prior
state and dependencies; evaluate trajectories as well as individual decisions
so locally plausible choices do not hide longer loops or worse final outcomes.

Track candidate-set coverage separately from accepted-case accuracy: how often
does the allowed candidate set contain an independently acceptable outcome?
Then report accepted coverage, abstention, error rate among accepted decisions,
false acceptance of unsupported actions, fallback rate and final workflow
success. Include all-neutral, ties and multiple-supported candidates.
Independent candidate entailment scores are not a categorical distribution or
calibrated probabilities of business correctness. A score margin only makes
sense for the declared decision semantics.

Use a fixed error taxonomy: candidate omitted, evidence missing/stale/conflicting,
incorrect evidence mapping, wrong selection, invalid output/arguments,
unsupported acceptance, unnecessary abstention, failed fallback, policy
violation, context overflow, provider/worker failure and failed final outcome.
Report task completion and policy adherence beside efficiency. A blocked or
incomplete task is not a successful saving; a correct refusal can still be the
expected outcome on a designated governance challenge case.

## Proposed cross-backend experiment

The four current run keys compare reuse within one provider configuration.
They are **not** these four proposed experimental arms:

| Backend strategy | Canopy result cache off | Canopy result cache on |
| --- | --- | --- |
| Original LLM workflow | LLM baseline | LLM plus reuse |
| JEV-assisted workflow, including retained steps and fallbacks | Substitution effect | Combined substitution and reuse |

Run a deterministic rules/function baseline wherever the task permits one. The
numeric replenishment sample admits straightforward arithmetic, so it cannot
alone establish that a model is the best replacement. Keep business inputs,
evidence, permitted outcomes and quality requirements equivalent across arms;
record provider-specific representations and mappings rather than claiming the
serialized requests are identical.

Use separate stores per arm, identical replay opportunities, representative
repetition rates and fresh case distributions. Preserve both cold and warm
measurements; counterbalance execution order and repeat trials to expose
network/provider caching variation. Separate application reuse, provider prompt
caching and historical replay. Record model revisions, generation options,
candidate ordering, worker device, hardware and concurrency. Current CLI fixed
ordering is useful for smoke checks, not a statistical experimental design.

Report medians and tail latency, throughput, sample counts and uncertainty for
quality and resource measurements. Include unchanged repeats, relevant source
changes, unrelated source changes and workload shifts. A single repeated answer
is insufficient evidence of production savings or acceptable accuracy.

## Account for the whole workflow

Keep measured values, estimates and unknowns separate. Include:

- Advisor trace capture, grouping, feature extraction and sample analysis;
  current offline discovery makes no analysis-model calls. Include any future
  semantic-analyzer calls separately. Report setup totals and amortization over
  an explicit observed or assumed number of executions.
- Candidate/hypothesis authoring and revision, evidence retrieval, input
  conversion, argument extraction and output normalization.
- Worker startup/loading, resident model resources, local inference, queueing,
  routing and acceptance checks, plus retained LLM explanation/planning calls.
- Both JEV and LLM attempts on fallback, failed calls, refreshes, monitoring,
  reevaluation and shadow comparisons. Shadow evaluation spends extra resources
  while gathering evidence; it is not itself a saving.
- Application cache lookup/storage and the same governance checks in all arms.

Local unpadded NLI input-pair tokens are not hosted billable token units. Zero
generated output tokens does not mean zero local computation, cost or energy.
`model_calls` counts attempted dispatches, including some failures before paid
inference; use provider usage where available. Unknown tokens use
`tokens_known: false`; unknown cost/energy remain null. Dollar/energy estimates
need a named rate/source, hardware basis, date and assumptions. Do not convert
missing measurements to zero or compare a local model's token count directly
with hosted billing as a saving.

Evaluate recommendations under the user's actual objective: quality constraints
first, then measured latency, cost or capacity. A lower-cost warm path may be
slower, require hardware, or lose its advantage at low volume. Report those
tradeoffs rather than collapsing them into an unexplained efficiency score.

## Traceability and drift

Retain the original business case ID, source/evidence versions, candidate set,
hypothesis wording/order, mapping version, handler/model identity, acceptance
rule version, policy version, chosen route, rejection/fallback reason, all
attempt usage and normalized final outcome. Recommendation state should point
to its evaluation dataset and results. These are proposed benchmark/report
fields, not fields already added to `canopy.contracts`.

Revalidate on prompt/schema/tool/candidate changes, model or mapping revisions,
source/policy changes, increased abstention/fallback or changed workload mix.
Observe sampled independently scored outcomes to detect confidently wrong
results; score shifts alone do not establish a quality problem or compliance.
Monitor route coverage as well as accuracy so a shrinking accepted set cannot
conceal degraded utility. Invalidate recommendation applicability separately
from existing execution-cache validity.

Freshness and permissions remain authoritative. A stale/prohibited input should
be refreshed, refused or reviewed under policy; routing to an LLM does not cure
that violation. Fallback must fit the remaining budget and preserve the same
tool authorization boundary. Keep prompt-injection/tool-misuse challenges in a
clearly labeled suite alongside ordinary quality cases.

For the first handoff, deliver a reproducible current-provider report, its
independent scorer and labeled cases, plus an advisor report with independently
labeled detection expectations and measured analysis overhead. Follow with the
cross-backend experiment specification. The core owner can then implement the
smallest required mapping/routing contract without taking over the benchmark.
