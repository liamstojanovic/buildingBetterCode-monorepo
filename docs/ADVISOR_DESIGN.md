# Workflow optimization advisor: discussion design

Status: **proposed, not implemented**. Updated October 17, 2026.
The user wants recommendations for business decisions, tool selection, and
agent next-step decisions, and asked to discuss the design before implementation.
This document is a planning reference, not an implemented API or an instruction
to build the feature without the next scope decision.

The advisor would identify recurring LLM operations that could use OpenJEV,
explain the transformation, and attach evidence of quality and resource savings.
Structural suitability, acceptable outcomes, and beneficial performance are
separate findings. A candidate recommendation establishes only the first.

## Current foundation and missing capabilities

Implemented: versioned handler registration, recorded Task/config/step outputs,
usage and events, validity-aware reuse, budgets, policy/approval checks, hosted
model adapters, and a native Pollard-Jev worker. See [live models](../core/docs/live_models.md).

The current runtime executes a declared DAG. It does not implement an adaptive
agent loop, conditional tool dispatch, a recommendation service, recommendation
endpoints, shadow evaluation, or automatic model switching. Hosted adapters do
not currently expose a provider tool-calling protocol. Tool names in a registry
are not automatically a semantic description of what those tools do.

The native worker uses pollard-jev 0.13, pollard 1.6.0, and a pinned OpenJEV v2
checkpoint. Its request accepts typed numeric observations and explicit candidate
hypotheses. Generic ticket/document text needs an explicit text-evidence design;
do not pretend it already works by numerically encoding arbitrary text or hiding
it in unrelated fields. Real OpenJEV weight execution remains unverified in this
workspace. Synthetic-encoder tests validate integration, not task accuracy.

## Detect opportunities without analyzing every request with another LLM

Use two paths: ordinary execution continues with its current handler, while a
bounded background analysis reads existing tenant-scoped recordings. The first
release should run as an explicit offline analysis command; a periodic service
could follow later. No analyzer network call belongs in the execution path.

1. **Collect existing evidence.** Start with run/step latency, known usage,
   handler identity, prompt configuration, inputs, output shapes, and repeat
   counts. Future framework adapters can attach tool descriptions, JSON output
   schemas, allowed choices and prompt-template identifiers automatically.
   Instrumented calls are observable; calls hidden inside arbitrary handlers or
   made outside Canopy are not automatically discovered.
2. **Group recurring call sites.** Group by tenant, workflow/call site, handler
   and prompt-template revision, model configuration, output/evidence schema,
   candidate or tool catalog revision, and applicable policy/mapping revision.
   Do not group by the entire instance prompt, which changes per ticket. Do not
   strip unknown prompt content to force a match: if template boundaries are
   unavailable, use conservative exact groups or mark the structure unknown.
   This is an analysis-group identity, never a result-cache key; different
   customer inputs still require distinct execution/reuse identities.
3. **Apply cheap structural checks.** Fixed enums, small explicit choice sets,
   selection-only output schemas, named tool selections, and stable transitions
   are positive signals. Open text generation, unconstrained arguments, unknown
   candidate discovery, or required information absent from the input are
   counter-signals. Short responses and low observed output diversity are only
   hints: rare valid choices may not appear in a sample.
4. **Prioritize valuable groups.** Rank by call volume and measured spend or
   latency. Keep unknown cost unknown; use measured latency/frequency instead.
   Start with groups that are both plausibly substitutable and material to the
   workflow. Explicit arithmetic/rules can receive a deterministic-function
   recommendation rather than a model recommendation.
5. **Inspect a bounded sample.** Review a stratified sample including unusual
   inputs, failures, long contexts, and different output categories. An optional
   LLM can analyze the template and sample once per version to propose task type,
   hypotheses, evidence mapping and blockers. Treat those proposals as untrusted
   suggestions, not executable mappings or correctness evidence. The semantic
   analyzer is optional; structural discovery should work without credentials.
6. **Cache the finding and re-evaluate deliberately.** Persist the recommendation
   against its versioned group. Revisit on relevant schema/template/catalog/model
   changes, an explicit request, or monitored workload changes. Do not re-run
   semantic discovery for every fresh instance or exact-repeat cache hit.

For example, 10,000 calls to one stable template could produce one group analysis
plus a capped sample review, instead of 10,000 analysis-model calls. This is a
design illustration, not a measured overhead or savings claim.

Analysis should have independent limits on groups, samples, tokens, elapsed time,
and monetary spend where known. Full queues defer analysis without delaying
business execution. Track analysis/setup cost and repeated evaluation cost
separately. A design target is zero added synchronous model calls for discovery;
actual recording/queue/lookup overhead must be measured before making a latency
claim. Existing records can be used offline with no new hot-path instrumentation.

## Eligibility by decision family

| Family | Positive evidence | Required separation or blocker |
| --- | --- | --- |
| Business decision | Explicit facts, bounded labels, downstream needs a choice | Text evidence needs a supported mapping; arithmetic may be better as code |
| Tool selection | Known tool catalog, state-specific eligible tools, output is tool identity | Selection is separate from generating arguments and executing the tool |
| Next step | Authored transitions and explicit state, e.g. fetch evidence, ask, respond, stop | Inventing a plan or new actions remains open-ended; stopping needs validation |
| Mixed decision and prose | A choice field and an explanation field have distinct consumers | Replace only the bounded choice; count retained generation and conversion |

For tool selection, obtain eligible tools from trusted application state and
permissions, then evaluate authored descriptions/hypotheses. The execution gate
must still validate the chosen tool, arguments, permissions and approvals just
before dispatch. A score does not authorize a business write.

For next steps, bind the recommendation to a versioned transition vocabulary and
state representation. Include applicable stop, ask-for-evidence, or escalation
outcomes, and evaluate premature stopping and loops. Runtime state changes still
require normal dispatch checks. Creating the state summary or candidates with an
LLM every turn incurs cost and can remove the benefit; prefer existing structured
state and reusable candidate templates where they preserve the decision inputs.

Candidate filtering must preserve the correct option. Large catalogs can make
pairwise scoring expensive; evaluate shortlist recall as well as selector quality.
Abstain when evidence is insufficient or the valid action is outside the set.
Do not assume a small observed action set exhausts all valid actions.

## Proposed recommendation record, not a wire contract

The frontend can prototype this information using explicitly labelled fixtures.
No current JSON Schema, handler or HTTP route implements this record.

| Information | Purpose |
| --- | --- |
| Recommendation ID; tenant/workflow/step; versioned group identity | Identify the exact recurring operation |
| Category: business_decision, tool_selection, next_step | Explain which part is replaceable |
| Status: candidate, needs_mapping, evaluating, validated, not_beneficial, unsupported | Separate discovery from evidence-backed recommendation |
| Current backend and proposed strategy/checkpoint | Make the alternative specific |
| Reasons and observed sample/run references | Explain why the advisor flagged it |
| Required evidence/candidate/output mapping and blockers | Make the work needed reviewable |
| Evaluation version, case counts, quality/errors, coverage, fallback | Establish where the alternative works |
| Cold/warm latency, throughput, overhead and cost basis | Establish whether it saves resources |
| Validity scope, last evaluation, invalidation reasons | Prevent stale recommendations being presented as current |

Unknown measurements should be null/not evaluated. Mock fixtures must never show
invented savings as observed results. Do not use an unexplained suitability
probability; concrete reasons are more useful until such a probability is itself
validated. Lifecycle transitions and activation UI remain design decisions.

## Evaluation and later activation

Discover candidates on a development sample, choose mappings/thresholds using a
separate tuning set, and report on a held-out evaluation set. Repeated templates
and near-duplicate cases should not leak across the splits. Expected business
outcomes must be independent of either compared model.

Compare the original LLM and JEV-assisted versions, with and without Canopy reuse;
include a deterministic alternative where applicable. Preserve equivalent facts
and downstream output semantics. The existing four-run CLI is a within-backend
cache comparison, not this cross-backend quality benchmark. See
[benchmark handoff](BENCHMARK_HANDOFF.md) for the proposed evaluation matrix.

The package proposes the highest-entailment option; its proposal/evidence label
does not implement a business acceptance rule. A separate, workload-validated
accept/abstain rule must handle weak support, ties, all-neutral results and several
supported candidates. Scores are independent across hypotheses; a runner-up
margin alone is not a general correctness guarantee.

Later explicit activation could install a versioned JEV-first route with an
original-handler fallback. On ambiguous decisions, fallback remains subject to
policy and the remaining budget, and both attempts are charged. Missing/stale
evidence requires refresh or refusal; authorization failure is not repaired by a
different model. A worker failure and uncertain model result need distinct
reasons. A recommendation must not silently mutate a live registry or workflow.

Approximate per-request resource accounting:

`preparation + JEV inference + fallback rate * original LLM cost + retained work`

Add amortized analyzer/mapping/evaluation/setup cost. Measure end-to-end p95 latency
separately; an expected-cost formula does not predict tail latency or queueing.
Keep local classifier token counts distinct from hosted billing tokens, and
energy unknown until measured. Do not credit cache savings to model substitution.

## Suggested hackathon increment and ownership

First implement discovery and explanations for all three families. Validate one
bounded semantic workload deeply, then add tool identity and next-step examples
with existing arguments and authored transitions. Treat a full autonomous planner
or arbitrary tool-argument generator as separate scope.

- Core owner: analyzer/record contracts, bounded trace grouping, capability
  matching, mappings, evaluation hooks, and later accepted routing behavior.
- Benchmark teammate: independent cases/labels, scoring and resource comparison,
  candidate coverage, fallbacks, and measurement of analyzer overhead.
- Frontend teammate: opportunity cards, reasons/blockers, evidence-backed
  comparisons, and existing execution/approval/replay inspection. See
  [frontend handoff](FRONTEND_HANDOFF.md).

Open choices before implementation: text-evidence adapter shape; initial labeled
workloads; whether first delivery stops at offline recommendations; the optional
semantic analyzer's budget; acceptance criteria for each decision family.

## Source and implementation references

- [Current Canopy native bridge](../core/src/canopy/pollard_jev.py)
- [Current native worker](../core/src/canopy/_pollard_jev_worker.py)
- [Pollard-Jev request contracts](https://github.com/jemsbhai/pollard-jev/blob/main/src/pollard_jev/contracts.py)
- [Pollard-Jev OpenJEV provider](https://github.com/jemsbhai/pollard-jev/blob/main/src/pollard_jev/providers/openjev.py)
- [OpenJEV model card](https://huggingface.co/AlexWortega/openjev)

Upstream main can change. The current Canopy package/model pins are documented in
the live-model guide; future model migrations need their own evaluation identity.
