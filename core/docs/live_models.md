# Live model handlers and baseline/governed comparisons

`canopy.models` connects real model calls to the existing synchronous handler
registry. The comparison runner executes the same effective `Task` and provider
configuration four times: baseline, repeated baseline, governed cold, and
governed warm. It prints complete `RunResult` records and a compact usage summary.
CLI reports also include credential-free `provider_configuration` settings for
reproduction. Python callers can supply that optional dictionary to `run_comparison()`.
The hosted-provider sample is an **invented support ticket**; the `pollard-jev`
sample is an **invented numeric replenishment decision**. Neither is a customer
dataset or a quality benchmark. Successful execution normally makes three model
dispatches: one for each baseline run and one for governed cold. Governed warm can reuse the
completed answer without another provider request.

## Run a comparison

Install the core from the repository root:

```powershell
.venv\Scripts\python.exe -m pip install -e "core[models,dev]"
```

For hosted providers, supply the API key through its environment variable, then
choose an explicit model ID that your account can access. The CLI has no default
model and accepts `CANOPY_MODEL` instead of `--model`.

```powershell
.venv\Scripts\python.exe core\examples\live_comparison.py --provider openai --model YOUR_MODEL_ID --output-dir .canopy\comparisons
```

| `--provider` | API | Default key environment variable | `--base-url` |
| --- | --- | --- | --- |
| `openai` | OpenAI Responses | `OPENAI_API_KEY` | Optional origin, defaults to `https://api.openai.com` |
| `openai-compatible` | Chat Completions | `OPENAI_API_KEY` | Required API prefix, for example `https://your-endpoint.example/v1` |
| `anthropic` | Anthropic Messages | `ANTHROPIC_API_KEY` | Optional origin, defaults to `https://api.anthropic.com` |
| `gemini` | Gemini generateContent | `GEMINI_API_KEY` | Optional origin, defaults to `https://generativelanguage.googleapis.com` |
| `pollard-jev` (`jev` alias) | Local `pollard-jev` package and OpenJEV worker | None | No hosted endpoint |

Use `--api-key-env MY_PROVIDER_KEY` to select a different environment variable.
No API key is accepted on the command line or stored in `ModelConfig`.
`--temperature` optionally sets sampling for supported text models;
`--cache-namespace` identifies a provider deployment/revision scope.
`CANOPY_PROVIDER` and `CANOPY_BASE_URL` can supply provider and endpoint defaults.
Compatible endpoints must implement the adapter's Chat Completions subset;
provider-specific extensions need a custom adapter.

```powershell
.venv\Scripts\python.exe core\examples\live_comparison.py --provider anthropic --model YOUR_MODEL_ID
.venv\Scripts\python.exe core\examples\live_comparison.py --provider gemini --model YOUR_MODEL_ID
.venv\Scripts\python.exe core\examples\live_comparison.py --provider openai-compatible --model YOUR_DEPLOYMENT_ID --base-url https://your-endpoint.example/v1 --api-key-env MY_PROVIDER_KEY
```

The default databases are temporary. `--output-dir` retains `report.json`, separate
baseline and governed databases, and their component records under a new unique
subdirectory on **every** invocation. Existing results cannot preload a new
comparison's cold cache. Reports and traces contain the workload and model
outputs; store them as workload data. A non-completed run remains in the report
with its status and reason, and the CLI exits with code 1.

## Run the native Pollard-Jev package

`--provider pollard-jev` and `--provider jev` both use the `pollard-jev` Python
package in a separate worker environment. This integration loads local OpenJEV
weights and evaluates decision candidates. It requires no API key or hosted
service. The explicit supported model ID is
`AlexWortega/openjev/qwen3.5-4b-nli-v2`.

Create a separate worker environment so the package's pinned `pollard==1.6.0`
does not replace Canopy core's `pollard==1.6.2`:

```powershell
python -m venv .venv-pollard-jev
.venv-pollard-jev\Scripts\python.exe -m pip install "pollard-jev[openjev]==0.13"
```

Prepare the pinned model cache separately using the package's
[OpenJEV setup and helper review instructions](https://github.com/jemsbhai/pollard-jev/blob/main/docs/openjev.md).
For the supported model, the explicit download command is:

```powershell
.venv-pollard-jev\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='AlexWortega/openjev', revision='552759daad712f1af6c4c13dabcb1e047886fc9c', cache_dir='YOUR_CACHE', allow_patterns=['modeling_openjev.py', 'qwen3.5-4b-nli-v2/*'])"
```

Then provide the worker executable and that cache path:

```powershell
.venv\Scripts\python.exe core\examples\live_comparison.py --provider pollard-jev --model AlexWortega/openjev/qwen3.5-4b-nli-v2 --worker-python .venv-pollard-jev\Scripts\python.exe --model-cache-dir YOUR_CACHE --device cpu --timeout-seconds 600 --output-dir .canopy\comparisons
```

`CANOPY_POLLARD_JEV_PYTHON` and `CANOPY_OPENJEV_CACHE` can supply those two paths.
`--device` chooses the local inference device; `--max-context-tokens` defaults to
4096 per NLI input pair. The worker uses `from_local_cache`: model weights are
never downloaded during a comparison. Missing packages or weights produce a
failed recorded run, with no hosted fallback. Install the base
`pollard-jev==0.13` package for contract-only worker tests; actual inference
requires its `openjev` extra and prepared model weights.

The first local inference includes worker startup and model loading, which can
be substantial. The worker stays loaded across the four runs, so governed cold
means an empty Canopy result cache, not a freshly loaded model. Set a timeout
that allows loading on the selected hardware and report that cold-start cost
separately when analyzing results. The local model
is an entailment scorer: its candidate scores are independent, not a probability
distribution over actions, generated prose, or authorization to execute an action.
The sample does not import or exercise robotics policies. No replenishment order
is placed.

## Supply actual tasks

Generate a complete editable task without credentials or network calls:

```powershell
.venv\Scripts\python.exe core\examples\live_comparison.py --write-sample-task support-task.json
```

Replace the invented inputs and evidence with your workload, then run:

```powershell
.venv\Scripts\python.exe core\examples\live_comparison.py --provider openai --model YOUR_MODEL_ID --task-file support-task.json --output-dir .canopy\comparisons
```

The CLI accepts complete `Task` JSON, including multi-step DAGs. Each step must
use `kind: "model"` and `handler: "model.generate"`. It binds those steps'
`handler_version` to the selected adapter configuration before any execution;
the report includes the exact effective Task used in all four runs. The input
file is not modified. Set `metadata.sample_data` to `false` for actual workloads.
If your task mixes tools or multiple named model handlers, build a trusted
registry in Python and call the example's `run_comparison()` with it.

Hosted text requests include canonical JSON containing the step inputs, direct
dependency outputs, and all declared transitive evidence snapshots. Put task
instructions and business data in `inputs`, and declare evidence dependencies
through `evidence_ids`; unrelated task metadata is not sent to the provider.
Evidence timestamps and versions come from the caller. The runner does not fetch
sources or refresh expired evidence automatically. A saved sample expires four
hours after generation, so generate a new fixture or provide a genuinely updated
source snapshot when testing later.

For the native package, generate the package-shaped numeric decision sample:

```powershell
.venv\Scripts\python.exe core\examples\live_comparison.py --provider pollard-jev --write-sample-task replenishment-task.json
```

Native steps carry `inputs.decision_request`, a real package `DecisionRequest`
JSON value containing the declared decision inputs, candidates, and validity.
Use this exported structure as the starting point for your actual decision
workload. The CLI calls `bind_pollard_jev_task(task, handler.version)` before
execution to bind the model identity and exact native-request validity into
Canopy evidence. The effective Task in the report includes this binding. Native
task files take the same binding path as the native sample, so cold and warm
comparisons enforce the declared evidence consistently.

The native adapter reads this explicit `decision_request`; it does not convert
arbitrary text inputs or dependency outputs into package observations. Construct
the numeric observations and candidates yourself, or use a custom trusted
handler to perform that conversion for a more complex workflow.

Native output contains the package's candidate evaluations and proposal, while
text providers return a `text` field. A scorer must evaluate the provider's
actual output shape; the text adapters do not enforce a JSON output schema.
`--max-output-tokens` is irrelevant to native entailment scoring and is not used
as a generation limit. Native temperature configuration is rejected.

For the retained business-priority, tool-selection and next-step cases, use the
separate [versioned text mappings](jev-mappings.md) and
[JEV comparison demo](../../docs/JEV_COMMS_DEMO.md). Mapped tasks carry
`inputs.jev_text_request`; the same registered handler and isolated worker call
the package-loaded OpenJEV encoder on a text premise and explicit hypotheses.
This is a Canopy text bridge, not a numeric package `DecisionRequest`. It keeps
mapping identity and source hashes in the result. It does not automatically
translate arbitrary prompts or deploy a new workflow route.

## Read the results fairly

- `baseline_first` and `baseline_repeat` disable Canopy result caching. Policy
  checks and admission budgets still apply.
- `governed_cold` starts with a separate empty store; it performs current
  freshness/policy checks and eligible model dispatches.
- `governed_warm` repeats the same effective Task against that governed store.
  It can reuse completed results when their identities, policy, and evidence
  remain valid. An earlier failed or blocked run is not a warm-cache success.
- `summary` includes status, tokens, model/tool calls, elapsed time, reused
  steps, and `provider_cached_input_tokens`. Full results retain outputs,
  per-step usage, provenance, and failure reasons.
- `model_calls` counts attempted model-handler dispatches. Invalid local input,
  absent credentials, or a transport failure is not proof of paid inference.
  Provider-reported tokens establish measured usage when available.
- Provider prompt caching and Canopy reuse are different measurements. A live
  request can have provider-cached input tokens and still count as a model call.
  A Canopy-reused step records zero new model usage. Missing token measurements
  produce `tokens_known: false` and `null` summary token counts, not measured zero.
- Native Pollard-Jev input tokens measure the complete unpadded NLI input pairs
  evaluated locally. Its output tokens are zero because it does not generate
  text. These are local inference measurements, not hosted-provider billing
  units. Do not combine or compare them as equivalent billable tokens.

This is a fixed-order execution comparison, not a statistical harness. Provider
prompt caches, network latency, sampling, and changing model aliases can affect
live runs. Repeat across representative tasks with an independent correctness
scorer, preserve output checks, and report cold and warm results separately.
Reusing one previous answer does not establish answer quality or general savings.
The runner calculates no quality score, cost estimate, energy estimate, or
percentage improvement. Cost and energy remain unknown when not measured.

The default admission limit is `max_steps=len(task.steps)` per run. Override it
with `--max-steps`. `--max-output-tokens` defaults to 512 for text providers and is
a provider generation limit, **not** a complete input-plus-output token estimate
or a dollar spending cap. No automatic request retries are made. Missing usage
after a failed request remains unknown; a provider may have processed a request
before a connection failure. `--timeout-seconds` bounds hosted HTTP requests or
the native worker operation; it is not a whole-comparison deadline.

## Use the registry directly

```python
from canopy.contracts import BudgetLimits, RunConfig, Step, Task
from canopy.handlers import HandlerRegistry
from canopy.models import ModelConfig, register_model_handler
from canopy.runtime import create_runtime

registry = HandlerRegistry()
handler = register_model_handler(
    registry,
    ModelConfig(provider="openai", model="YOUR_MODEL_ID"),
    cacheable=True,
)
task = Task(
    task_id="triage-001", tenant_id="team-a", workflow_id="ticket-triage",
    steps=[Step(
        step_id="classify", kind="model", handler="model.generate",
        handler_version=handler.version,
        inputs={"prompt": "Classify this invented ticket: the export button fails."},
    )],
)
config = RunConfig(
    permissions=["execute:*"], model_fingerprint=handler.fingerprint,
    budget=BudgetLimits(max_steps=1),
)
runtime = create_runtime(".canopy/live.sqlite3", registry=registry)
try:
    result = runtime.run_task(task, config)
finally:
    runtime.close()
    handler.close()
```

Model registration defaults to `cacheable=False`. Opting in explicitly accepts a
previous completed answer for the same declared inputs and evidence; model
sampling does not make consecutive live outputs deterministic. The comparison
runner opts in to exercise reuse. Provider/model identity, adapter revision,
instructions, generation options, and `cache_namespace` determine the handler
version. Use pinned model versions when possible and change `cache_namespace`
when credentials, deployments, or provider revisions change the result context.
Credential values are excluded from this identity.
For a workflow with several provider configurations, each handler has its own
version; set the run's `model_fingerprint` to a stable identity of that complete
configuration. A handler version mismatch is rejected before dispatch or reuse.

Custom providers implement `ModelAdapter` with a stable `revision` string and
`generate(ModelRequest, ModelConfig) -> HandlerResult`, then pass the adapter to
`register_model_handler(..., adapter=adapter)`. Report actual usage through
`Usage`; omitted tokens must remain unknown. The supplied adapters issue text
requests or evaluate native decision candidates and never execute
provider-suggested tools or proposed actions.

The deterministic tests use local handlers or mocked HTTP transports. They
verify accounting, cache isolation, wire formats, and failure handling without
provider keys; they do not establish live account availability or model quality.

Live smoke verification on October 17, 2026 completed all four comparison runs
with `gpt-4.1-mini-2025-04-14`, `claude-haiku-4-5-20251001`, and
`gemini-2.5-flash-lite` using the invented support task. Each provider made three
successful live requests; its governed warm run reused one step with zero new
model calls or tokens. This validates connectivity and accounting for those
accounts and models, not comparative quality or general performance. Arbitrary
compatible endpoints have mocked transport verification. Native Pollard-Jev
verification is separate from these hosted-model smoke results.

Wire contracts follow the official [OpenAI Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create),
[Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic Messages reference](https://platform.claude.com/docs/en/api/messages/create),
[Gemini generateContent reference](https://ai.google.dev/api/generate-content).
The local integration uses the [Pollard-Jev package](https://pypi.org/project/pollard-jev/).
Anthropic input counts include
uncached, cache creation, and cache read tokens; Gemini output counts include
candidate and thought tokens. Unknown dollar cost and energy are never inferred
from those token counts.
