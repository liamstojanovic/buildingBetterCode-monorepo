# Explicit JEV mappings for the recorded demo

`canopy.jev_mappings` implements three reviewed mappings for the retained,
invented support cases. Each mapping supplies actual text evidence and named
hypotheses to the pinned OpenJEV NLI classifier. The mapping does not consume
recorded LLM answers or fixture expectations.

| Mapping ID | Classifier input | Selected field | Work still required |
| --- | --- | --- | --- |
| `canopy-support-business-decision-v1` | Ticket text, supplied policy, high/normal criteria | `priority` | A separate explanation still supplies `reason`. |
| `canopy-support-tool-selection-v1` | Customer request, supplied policy, read-only tool descriptions | `tool` | Arguments remain prebound. Nothing invokes the selected tool. |
| `canopy-support-next-step-v1` | Explicit state facts, reviewed ordered catalog and precedence policy | `next_step` | The eligibility gate checks the proposal. Nothing executes a transition. |

The next-step policy is completely specified by structured state. The independent
`next_step_rule` function supplies a deterministic baseline that needs no model
call. The JEV comparison preserves that baseline; it does not imply a classifier
is necessary for these three simple next-step fixtures.

## Package boundary and provenance

Pollard-Jev 0.13's native `DecisionRequest` accepts numeric observations. Its
`OpenJevProvider` loads the pinned OpenJEV text-pair encoder. The existing numeric
`infer` operation remains available with `input_representation=json-v1`.

For text evaluation, the isolated worker adds the explicit `infer_text`
operation. It calls the actual package-loaded encoder's `predict_hypotheses`
interface through Pollard-Jev's `ContextCheckedEncoder`. It then validates every
contradiction/entailment/neutral row with the package's validation and returns a
native `ProviderResult`. No text is replaced with fabricated numeric observations,
no score is scripted, and no generated explanation is attributed to JEV.

The text identity includes `input_representation=canopy-text-v1`, mapping ID,
mapping version and original-source SHA-256. The pinned dependency pair remains
`pollard-jev==0.13` / `pollard==1.6.0` in a separate Python environment. The model
is `AlexWortega/openjev/qwen3.5-4b-nli-v2`, revision
`552759daad712f1af6c4c13dabcb1e047886fc9c`. The normal Canopy environment keeps its
own Pollard version. The worker loads previously cached files offline and never
downloads weights implicitly.

Each response retains every candidate's NLI row, independent entailment scores,
the raw maximum-scoring selection, measured complete input-pair lengths,
`phase_timings_ms.model_load`, and `phase_timings_ms.inference`. A persistent
worker loads the model once. Pair-token counts include the repeated premise for
each hypothesis and are neither generated tokens nor hosted-provider billing.
The context guard rejects input overflow instead of silently truncating.

## Source validity and the registry

`map_recorded_step(task, step, evaluated_at=...)` hashes the original inputs and
all referenced evidence, including their original timestamps. It generates a
separate, bounded `historical_re_evaluation` request. This lets the demo evaluate
an old sample snapshot without claiming its policy is fresh for operational
execution. `mapping.as_task()` retains that distinction in metadata.

The mappings currently require sample-labeled, independent `model.generate`
steps without side effects. Missing evidence, another tenant's evidence,
dependency outputs, changed output schemas, unknown catalogs and unsupported
state shapes are rejected. Next-step eligibility wording, ordering, policy data
and policy version must match the reviewed v1 policy; changing semantics requires
a reviewed new mapping version.

For the existing handler registry:

```python
from canopy.handlers import HandlerRegistry
from canopy.jev_mappings import map_recorded_step, apply_mapping_result
from canopy.models import ModelConfig, POLLARD_JEV_MODEL, register_model_handler
from canopy.pollard_jev import PollardJevAdapter, bind_pollard_jev_task

mapping = map_recorded_step(recorded_task, recorded_task.steps[0])
adapter = PollardJevAdapter()
registry = HandlerRegistry()
handler = register_model_handler(
    registry,
    ModelConfig(
        provider="pollard-jev", model=POLLARD_JEV_MODEL,
        worker_python="/absolute/path/to/isolated/python",
        model_cache_dir="/absolute/path/to/pinned/cache", device="cuda",
        timeout_seconds=600,
    ),
    adapter=adapter, cacheable=True,
)
evaluation_task = bind_pollard_jev_task(mapping.as_task(), handler.version)
# Run with the registry and RunConfig(model_fingerprint=handler.fingerprint,
# permissions=["execute:*"]). Close the runtime and adapter when finished.
# The step output includes output["decision"], the raw ProviderResult.
projection = apply_mapping_result(mapping, raw_provider_result)
```

Binding captures the entire text request as exact validity evidence. Runtime
validation happens before warm reuse, so changing the premise, mapping metadata
or expiry cannot reuse an answer bound to different evidence. The adapter revision
also participates in the registered handler's fingerprint.

## Keep the raw result and gate separate

`apply_mapping_result` returns `raw_choice` and `projected_output` even when it
defers the choice. It never substitutes the rule baseline and labels that as the
model's answer. The default gate requires entailment at least `0.8` and a margin
over the next candidate of at least `0.15`. These are explicit, uncalibrated demo
heuristics, not measured confidence guarantees. The next-step gate additionally
requires the raw selection to match the deterministic eligible transition.

An accepted result has `disposition=accept_selection` and `accepted_choice`; a
deferred result has `disposition=defer`, reasons and `accepted_choice=null`.
Every result has `action_executed=false`. Business results retain
`remaining_fields=["reason"]`. A correct priority selection alone does not
replace the original priority-plus-explanation call.

## Reproduce and inspect

The [evaluation example](../examples/jev_recorded_comparison.py) reads the
[retained recordings](../examples/recordings/advisor-live-2026-10-17/README.md),
checks the source manifest, deduplicates seven distinct case inputs, and compares
actual local JEV trials with all retained hosted calls. It writes an offline HTML
demo, full JSON and a hash manifest into a new directory:

```powershell
.venv\Scripts\python.exe core/examples/jev_recorded_comparison.py `
  --worker-python .canopy/pollard-jev-env/Scripts/python.exe `
  --model-cache-dir <existing-pinned-cache> --device cuda `
  --repeats 2 --output-dir .canopy/jev-evaluation-new
```

Review raw choice accuracy, gate coverage, incorrect accepted choices, required
explanations, deterministic baseline and latency separately. The repeats measure
the same seven simple cases; they are not additional independent examples. Do not
interpret a retained failure as a successful replacement, tune on these same
cases and report it as held-out accuracy, or compare different machines' timings
as a controlled speed benchmark. Evaluating mappings does not automatically
activate routing or change the historical advisor recommendations.
