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

## Proposed advisor evaluation: not implemented

Automatic candidate detection, recommendation records, a text-premise adapter,
dynamic backend selection, calibrated abstention and LLM fallback are proposed
extensions. The current comparison CLI does not implement them. Coordinate the
interface with the core owner using [the advisor design](ADVISOR_DESIGN.md).
Do not treat mock recommendations as measured results.

The cheap detection hypothesis is to inspect existing trace structure offline:
declared finite outputs, tool allowlists, stable candidate schemas, repeated
prompt versions and observed selection-only outputs. Analyze a capped,
stratified sample per workflow/template fingerprint, then reuse the finding
until relevant versions or behavior change. Small observed output cardinality
is a useful lead, not evidence that the candidate set is complete.

Evaluate the detector separately from the proposed replacement. A detector can
find a structurally plausible call whose OpenJev execution is inaccurate,
slower or more expensive. Independently label candidate groups as suitable,
partially suitable, unsuitable or requiring more information. Measure useful
candidate precision/recall, unsupported recommendations, analysis cost per
group, stale-recommendation rate and ranking benefit. Keep discovery samples
separate from the final held-out substitution test set.

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

- Advisor trace capture, grouping, feature extraction, sample analysis and any
  occasional analysis-model calls; report setup totals and amortization over an
  explicit observed or assumed number of executions.
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

For the first handoff, deliver one reproducible current-provider report, its
independent scorer and labeled cases, followed by an explicitly proposed
cross-backend experiment specification. The core owner can then implement the
smallest required mapping/routing contract without taking over the benchmark.
