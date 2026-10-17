# Offline workflow optimization advisor

Status: **implemented offline discovery**. Updated October 17, 2026.
The advisor identifies recorded business decisions, tool selection and next-step
choices that merit OpenJEV evaluation. See the [runnable guide](../core/docs/advisor.md).

Structural suitability, acceptable outcomes and beneficial performance are
separate findings. This release establishes structural leads. Recommendations
never authorize execution, validate quality or change a workflow.

## Implemented boundary

- Python analysis functions and a CLI read a recording store or a live-comparison
  report. Discovery is offline, tenant-scoped and bounded. No network, model or
  tool calls are made; provider credentials are unnecessary.
- Versioned grouping uses declared template boundaries where available and
  conservative exact-input identities otherwise. Group fingerprints are never
  execution-cache keys.
- Reports include reasons, blockers, required changes, observed resources,
  bounded sample references, source integrity and analysis limits/timing.
- Finding states are `candidate`, `needs_mapping` and `unsupported`.
  Every recommendation has `evaluation_status: not_evaluated`; quality and
  estimated savings remain null.

Not implemented: an advisor HTTP endpoint, background scheduler, semantic LLM
analyzer, persisted recommendation cache, replacement evaluation, general text
adapter, automatic backend switching, fallback route or adaptive agent loop.
Saving a report retains a snapshot; reruns recompute bounded heuristics. Report
retention does not implement automatic invalidation or drift monitoring.

The native bridge uses pollard-jev 0.13, pollard 1.6.0 and a pinned OpenJEV v2
checkpoint. It accepts numeric observations and explicit hypotheses. Text-based
recommendations identify an adapter gap. Real local OpenJEV inference remains
unverified here; synthetic-encoder tests cover integration, not task accuracy.
See [live models](../core/docs/live_models.md).

## Detection without a per-call analysis model

The analyzer inspects recorded model steps, not every hidden call inside arbitrary
handlers. It combines optional `DecisionDescriptor` declarations with structural
clues: finite choices, output enums, tool catalogs, allowed next steps and
selection-shaped results. Declarations are application-supplied analysis evidence,
not permission grants or proof that all valid candidates are included.

Output brevity, repeated labels and a small observed action set are only clues.
Open generation, missing mappings, required prose and generated arguments remain
visible as blockers or retained work. A candidate recommendation does not claim
that the replacement will be correct or efficient.

Grouping binds workflow/step, handler/model/prompt revisions, descriptor and
relevant structural inputs. Template ID/version plus explicitly named
`instance_fields` allow those top-level instance inputs to vary while retaining
their shape. Catalogs, choices, instructions and output schemas remain protected;
undeclared input values remain exact. Without template boundaries, exact-input
grouping sacrifices recall instead of guessing which prompt content is incidental.
Change template versions when meaning changes. The fingerprint identifies an
operation for review, not reusable output for different customer requests.

Limits cover runs, groups, retained samples, steps per run, step representation
size and elapsed time. Limit reasons and skipped counters identify potentially
partial reports. Reaching `max_runs` reports that cap without reading one extra
record to determine whether the source is exhausted. Time checks apply inside
analysis, cooperatively between reads/steps, not hard cancellation of slow I/O.
CLI elapsed time also includes source setup/import/verification and excludes
output serialization. Ranking uses observed calls, elapsed time or known cost,
not predicted savings. Unknown resources stay unknown; synthetic observations
are counted. Replay runs are excluded and reused steps do not duplicate historical
inference usage.

No analyzer enters runtime execution. Offline CPU/I/O still cost resources; measure
the full command before claiming negligible overhead. There is no repeated
analysis-model charge. Large input imports still require parsing and validation.

## Eligibility by decision family

| Family | Positive evidence | Remaining work or blocker |
| --- | --- | --- |
| Business decision | Supplied facts and bounded labels | Text needs an adapter; arithmetic/rules may suit ordinary code |
| Tool selection | Declared tools and selected tool identity | Argument generation, authorization, validation and execution remain separate |
| Next step | Authored transitions and explicit state | Open-ended plans, absent actions, loops and stopping need evaluation |
| Mixed choice and prose | Separable choice and explanation consumers | Retained generation and input conversion must be counted |

Tool eligibility must eventually come from trusted state and permissions. An
execution gate still needs to validate tool, arguments and approval before
execution. Scores cannot authorize a write. Next-step evaluation needs complete
state and transitions, including ask, refresh, review and stop where applicable.
Candidate filtering must preserve an acceptable option. If constructing state or
hypotheses requires another model call, include that work in the experiment.

## Implemented report contract

Wire models: [AdvisorReport](../core/schemas/AdvisorReport.schema.json),
[DecisionDescriptor](../core/schemas/DecisionDescriptor.schema.json), and
[AdvisorLimits](../core/schemas/AdvisorLimits.schema.json). The
[frontend handoff](FRONTEND_HANDOFF.md) covers import and rendering.

A finding explains category, strategy, choices, signals, reasons, blockers,
required changes, partial substitution and native compatibility. Its surrounding
recommendation carries versioned identity, observed metrics and bounded run/step
references. Reports omit full prompts and evidence payloads; choice labels and
identifiers remain application data belonging to the intended tenant.

`candidate` means a structural lead for evaluation; `needs_mapping` means
prerequisites are incomplete; `unsupported` preserves a negative finding.
`evaluating`, `validated` and `not_beneficial` are future evaluation states, not
current schema values. Do not invent an activation control in the frontend.

## Evaluation and later activation

The benchmark teammate evaluates discovery separately from substitution. Label
groups independently and measure precision/recall, false positives, ranking utility
and overhead. Then establish mappings, independent labels, held-out quality,
coverage, abstention and costs for a proposed replacement. See
[benchmark handoff](BENCHMARK_HANDOFF.md).

Compare original and JEV-assisted workflows with and without Canopy reuse;
include a deterministic baseline where appropriate. The current live comparison
CLI compares cache behavior within one backend, not cross-backend quality.

Pollard-Jev proposes the highest-entailment option even for weak/tied scores.
A future route needs a workload-validated acceptance rule. Independent entailment
scores are not correctness probabilities. Explicit activation and fallback would
remain subject to permissions, evidence validity and remaining budgets.

Approximate per-request cost is:

`preparation + JEV inference + fallback rate * original LLM cost + retained work`

Add amortized discovery, mapping, evaluation and startup costs. Measure tail
latency separately. Local NLI tokens and hosted billable tokens are different
units; energy remains unknown unless measured. Cache savings are not substitution
savings.

## Demo and ownership

Generate the real offline report from seven synthetic recorded groups across all
three families. Open the native-shaped `review_operations` candidate, then the
text/tool/next-step mapping gaps, retained generation and deterministic-rule
alternative. The native-shaped finding uses predetermined fixture output, not
OpenJEV execution. The analyzer runs for real; the data is synthetic and the
recommendations unevaluated.

- Core: analysis, grouping, contracts, CLI and reproducible examples.
- Benchmark teammate: independent detection labels, substitution quality,
  candidate coverage, fallback behavior and measured overhead.
- Frontend teammate: report import, opportunities, reasons/blockers, provenance,
  metric handling and existing execution/approval/replay screens.

Next design work includes text-evidence mapping, labeled substitution workloads,
acceptance criteria and explicit activation. Future monitoring must bind evaluated
recommendations to revisions and observed workload changes.
