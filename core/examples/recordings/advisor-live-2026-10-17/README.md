# Recorded hosted decisions and offline advisor demo

These are actual hosted model responses on **invented support cases**, recorded
on 2026-10-17. The new tool-selection and next-step comparisons made 54 model
calls; the retained business comparisons contribute nine earlier calls.
The complete collection contains nine comparisons, 36 runs, 84 step observations,
63 model calls and 21 warm reuses. No selected tool or transition was executed.

Each comparison contains baseline first/repeat and governed cold/warm runs.
`synthetic: false` describes actual handler execution; `sample_data: true`
describes invented inputs. Repeated runs are not distinct evaluation cases.
Reported cost and energy remain unknown. Recorded outputs, including mistakes,
have not been rewritten or replaced by later attempts.

## Start the demo

Use **Import recording** in the frontend and select one `frontend-comparison.json`
below. This is recorded playback: credentials, SQLite files and new inference are
unnecessary. Select cold/warm comparison modes to inspect the corresponding
recorded runs. Each bundle holds one provider and one category.

| Category | OpenAI | Anthropic | Gemini |
| --- | --- | --- | --- |
| Business decision | [Recording](openai-business-decision/frontend-comparison.json) | [Recording](anthropic-business-decision/frontend-comparison.json) | [Recording](gemini-business-decision/frontend-comparison.json) |
| Tool selection | [Recording](openai-tool-selection/frontend-comparison.json) | [Recording](anthropic-tool-selection/frontend-comparison.json) | [Recording](gemini-tool-selection/frontend-comparison.json) |
| Next step | [Recording](openai-next-step/frontend-comparison.json) | [Recording](anthropic-next-step/frontend-comparison.json) | [Recording](gemini-next-step/frontend-comparison.json) |

Models were `gpt-4.1-mini-2025-04-14`, `claude-haiku-4-5-20251001` and
`gemini-2.5-flash-lite`. New selector calls used a 128-output-token limit.
The [manifest](manifest.json) indexes core comparison exports, frontend bundles,
per-comparison advisor reports, run identities and file hashes.

Open the [combined advisor report](advisor-report.json) alongside playback.
The current frontend recording importer does **not** accept advisor JSON; its
dedicated advisor view is frontend integration work described in the
[frontend handoff](../../../../docs/FRONTEND_HANDOFF.md).

## What the advisor found

| Category | Distinct cases | Provider-specific groups | Finding |
| --- | ---: | ---: | --- |
| Business decision | 1 | 3 | Priority `high` / `normal`; text mapping and retained explanation required |
| Tool selection | 3 | 9 | Knowledge search, incident lookup and ticket lookup; text mapping, reviewed hypotheses and execution gates required |
| Next step | 3 | 9 | `clarify`, `retrieve` and `stop` states; state mapping, reviewed hypotheses and transition validation required |

All 21 findings have `strategy: evaluate_openjev`, `status: needs_mapping`,
`evaluation_status: not_evaluated`, and null measured quality/estimated savings.
Only the three business groups are partial substitutions: their original calls
also generate reasons. New selector outputs contain only tool/transition identity
after JSON-fence normalization. Tool arguments were already supplied in the
catalog; choosing a name does not authorize execution. Next-step examples are
independent state snapshots, not an adaptive workflow or an executed trajectory.

The next-step policy is an explicit ordered rule. Include a deterministic function
baseline when evaluating JEV; selector detection does not establish that a model
is necessary. Missing-option, ambiguous-input, stale-state and looping trajectories
still need separate evaluation. No OpenJEV inference or replacement-quality
experiment was performed for these artifacts.

## Preserve the failures in the presentation

[Fixture checks](fixture-checks.json) compare only the 54 new, non-reused model
outputs with [expectations](expected-choices.json) saved before those calls.
These are authored smoke-test expectations, not an independent quality benchmark.
Business recordings and warm reuse are excluded from these checks.

| Provider | New calls | Correct normalized choice | Bare JSON object | Correct choice in requested bare JSON |
| --- | ---: | ---: | ---: | ---: |
| OpenAI | 18 | 17 | 18 | 17 |
| Anthropic | 18 | 18 | 0 | 0 |
| Gemini | 18 | 18 | 7 | 7 |
| Total | 54 | 53 | 25 | 24 |

Twenty-nine outputs were wrapped in Markdown JSON fences despite the bare-JSON
instruction. The advisor accepts an entire standalone JSON fence for inspection;
that normalization does not erase the original format violation.

In the OpenAI next-step cold comparison, inspect the baseline lane's
`choose_retrieval` step (core run `baseline_first`, frontend alias `baseline_cold`,
run `d604dc4e-3f0c-4adf-ae02-97b126eb2603`). It selected `respond` while
`approved_evidence_available` was false; the declared expected transition was
`retrieve`. Other repetitions selected `retrieve`. Do not attribute that variation
to governance: the runtime did not validate transition eligibility or execute the
transition. Frontend `evaluations` arrays intentionally remain empty.

## Reproduce with fresh evidence

Run from the repository root with the core installed and the chosen provider's
credential in its usual environment variable. These commands make new paid calls:

```powershell
$fixtures = .venv\Scripts\python.exe core\examples\advisor_live_tasks.py --output-dir .canopy\advisor-live-tasks | ConvertFrom-Json
.venv\Scripts\python.exe core\examples\live_comparison.py --provider openai --model gpt-4.1-mini-2025-04-14 --task-file $fixtures.task_files.'tool-selection' --max-output-tokens 128 --output-dir .canopy\live-advisor\openai\tool-selection
.venv\Scripts\python.exe core\examples\live_comparison.py --provider openai --model gpt-4.1-mini-2025-04-14 --task-file $fixtures.task_files.'next-step' --max-output-tokens 128 --output-dir .canopy\live-advisor\openai\next-step
```

Repeat with `--provider anthropic --model claude-haiku-4-5-20251001` and
`--provider gemini --model gemini-2.5-flash-lite`, using separate output folders.
Each three-step comparison normally makes nine new model calls and reuses three
steps on the warm run. Generate fresh fixtures when evidence expires; historical
task exports must not be silently refreshed or presented as new observations.

The existing recordings can be reanalyzed without credentials or inference:

```powershell
.venv\Scripts\python.exe -m canopy.advisor_cli --report core\examples\recordings\advisor-live-2026-10-17\openai-tool-selection\comparison.json --tenant-id sample-tenant --output .canopy\tool-advisor-new.json
```

Use a new output filename. To export another complete collection, pass its original
retained comparison reports to the offline exporter. Each source still needs its
adjacent baseline/governed databases and Pollard sidecars:

```powershell
.venv\Scripts\python.exe core\examples\export_advisor_live_demo.py PATH_TO_REPORT_1 PATH_TO_REPORT_2 --output-dir .canopy\portable-demo-new
```

The exporter verifies original recordings and task/configuration/result bindings,
then writes a new directory. It makes no model calls. Portable JSON contains no
Pollard proof, so advisor provenance remains `comparison_report` /
`validated_import`. File hashes support reproducibility, not independent
authentication. Local filesystem paths and credential-variable names are omitted;
the original workloads and provider outputs remain visible as sample data.

All nine bundles passed the current frontend's actual `parseBundle` validation and
`RecordedRunClient` playback (36 runs, 618 events, zero network calls). The combined
advisor report passed its JSON Schema validation. The fixture generator and
exporter also have automated tests that never call providers.
