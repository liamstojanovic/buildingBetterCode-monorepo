# Live model handlers and baseline/governed comparisons

`canopy.models` connects real model calls to the existing synchronous handler
registry. The comparison runner executes the same effective `Task` and provider
configuration four times: baseline, repeated baseline, governed cold, and
governed warm. It prints complete `RunResult` records and a compact usage summary.
CLI reports also include credential-free `provider_configuration` settings for
reproduction. Python callers can supply that optional dictionary to `run_comparison()`.
The built-in task is an **invented support ticket**, not a customer dataset or a
quality benchmark. Successful execution normally makes three live calls: one
for each baseline run and one for governed cold. Governed warm can reuse the
completed answer without another provider request.

## Run a comparison

Install the core from the repository root:

```powershell
.venv\Scripts\python.exe -m pip install -e "core[models,dev]"
```

Supply the selected provider's API key through its environment variable, then
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
| `jev` | TypeSafe JEV System One | `JEV_API_KEY` | Optional origin, defaults to `https://api.typesafe.ai` |

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
.venv\Scripts\python.exe core\examples\live_comparison.py --provider jev --model YOUR_JEV_MODEL_ID
.venv\Scripts\python.exe core\examples\live_comparison.py --provider openai-compatible --model YOUR_DEPLOYMENT_ID --base-url https://your-endpoint.example/v1 --api-key-env MY_PROVIDER_KEY
```

The default databases are temporary. `--output-dir` retains `report.json`, separate
baseline and governed databases, and their component records under a new unique
subdirectory on **every** invocation. Existing results cannot preload a new
comparison's cold cache. Reports and traces contain the workload and model
outputs; store them as workload data. A non-completed run remains in the report
with its status and reason, and the CLI exits with code 1.

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

Every request includes canonical JSON containing the step inputs, direct
dependency outputs, and all declared transitive evidence snapshots. Put task
instructions and business data in `inputs`, and declare evidence dependencies
through `evidence_ids`; unrelated task metadata is not sent to the provider.
Evidence timestamps and versions come from the caller. The runner does not fetch
sources or refresh expired evidence automatically. A saved sample expires four
hours after generation, so generate a new fixture or provide a genuinely updated
source snapshot when testing later.

JEV produces typed decisions rather than generated prose. Its steps require
`inputs.questions`, as in the sample:

```json
{
  "questions": {
    "priority": {
      "type": "choice",
      "instructions": "Classify the ticket using the supplied support policy.",
      "criteria": {
        "high": "A workflow is blocked with a deadline within 48 hours.",
        "normal": "A workaround exists without a near-term deadline."
      }
    }
  }
}
```

JEV receives the complete canonical request as its state and the typed questions
separately. Its output contains validated typed answers, while text providers
return a `text` field. A scorer must evaluate the provider's actual output shape;
the text adapters do not enforce a JSON output schema. JEV does not expose the
generation output-token limit used by the text providers, so
`--max-output-tokens` is not sent to JEV. JEV temperature configuration is rejected.

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
before a connection failure. `--timeout-seconds` sets the HTTP request timeout.

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
or typed-decision requests only and never execute provider-suggested tools.

The deterministic tests use local handlers or mocked HTTP transports. They
verify accounting, cache isolation, wire formats, and failure handling without
provider keys; they do not establish live account availability or model quality.

Live smoke verification on October 17, 2026 completed all four comparison runs
with `gpt-4.1-mini-2025-04-14`, `claude-haiku-4-5-20251001`, and
`gemini-2.5-flash-lite` using the invented support task. Each provider made three
successful live requests; its governed warm run reused one step with zero new
model calls or tokens. This validates connectivity and accounting for those
accounts and models, not comparative quality or general performance. JEV and
arbitrary compatible endpoints have mocked transport verification; no JEV key
was available for live verification.

Wire contracts follow the official [OpenAI Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create),
[Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic Messages reference](https://platform.claude.com/docs/en/api/messages/create),
[Gemini generateContent reference](https://ai.google.dev/api/generate-content), and
[TypeSafe JEV API](https://docs.typesafe.ai/api). Anthropic input counts include
uncached, cache creation, and cache read tokens; Gemini output counts include
candidate and thought tokens. Unknown dollar cost and energy are never inferred
from those token counts.
