# Canopy end-to-end demo handoff

Use this runbook on **Windows + Docker Desktop with WSL2**, with the supplied
NVIDIA RTX 4080 reporting **12,282 MiB VRAM**. Run the numbered steps in order
before demo day. You need no hosted-provider key for steps 1–10: they combine live
synthetic workflow execution, retained actual LLM calls, fresh offline analysis,
fresh communication measurements, and actual local OpenJEV inference.

The [latest published evidence](core/examples/recordings/container-benchmarks-2026-10-17/README.md)
and [results page](core/examples/recordings/container-benchmarks-2026-10-17/index.html)
provide a ready-to-open fallback: the CPU rerun against `28a276b` completed at
**20:09:26 UTC on 17 October 2026**, alongside the latest retained GPU rehearsal.
This refresh made **zero hosted calls ($0 provider spend)**. Follow the steps
below to produce your own hardware's measurements; retained playback does not
renew its original operational evidence.

**Shell convention:** step 1 uses Windows PowerShell. Subsequent commands are
**Bash inside your Ubuntu WSL terminal**, from the repository root, unless a block
explicitly says PowerShell. Leave Docker
Desktop running. You do not need Python, Node, Bun, or CUDA Toolkit installed in
Ubuntu; the Dockerfiles supply the application runtimes.

## What you will demonstrate

| Demo | What actually runs | Where to show it |
| --- | --- | --- |
| Recording, reuse and freshness | Live Python handlers through the Canopy API | React frontend, **Live API** |
| Budgets, replay, drift and deduplication | Fresh deterministic component executions | Generated CPU results |
| Three-provider LLM baseline vs governed | Playback of actual OpenAI, Anthropic and Gemini calls on invented data | **Import recording** / retained evidence hub |
| Offline advisor | Reanalyzes nine retained comparisons without another model call | React **Optimization** imports and generated reports, all three categories |
| JEV mappings and gates | Actual local OpenJEV inference on seven complete mapped cases, twice | Generated JEV results page |
| Efficient agent messages | Actual `cbor-ld-ex` encoding, roundtrips and SQLite mailbox measurements | Generated communication results page |

The initial seven-step PortMiami screen is a **frontend mock**. **Live API** loads
the real three-step catalog/quote/greeting fixture instead. The API registers
synthetic handlers; selecting a model in the browser does not enable hosted
calls. JEV is an evaluated, explicit candidate replacement here; automatic
backend switching and real tool/action execution are not activated.

## 1. Prepare Windows and WSL2 once

Install/update [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
and the Windows NVIDIA driver. Docker Desktop GPU support requires its **WSL2
backend and Linux containers**. Follow [Docker's GPU prerequisites](https://docs.docker.com/desktop/features/gpu/).
Use a current Docker Desktop release, **at least 4.31** for the newer NVIDIA
driver integration. Older releases can show a working `nvidia-smi` while CUDA
initialization fails; NVIDIA documents the fix in
[its WSL2 container issue](https://github.com/NVIDIA/nvidia-container-toolkit/issues/520).

In **Windows PowerShell**:

```powershell
wsl --update
wsl --version
wsl --list --verbose
nvidia-smi
```

Your Ubuntu distribution must show version **2**. If you do not have Ubuntu yet,
run `wsl --install -d Ubuntu`, reboot if Windows requests it, then finish Ubuntu's
username/password setup. For an existing version-1 Ubuntu distribution, run
`wsl --set-version Ubuntu 2` (use its exact name from the listing).

In Docker Desktop:

1. Settings → General → enable **Use the WSL 2 based engine**.
2. Settings → Resources → WSL Integration → enable your Ubuntu distribution.
3. Apply changes. If the tray menu says **Switch to Linux containers**, select it.

[Docker's WSL guide](https://docs.docker.com/desktop/features/wsl/) requires WSL
2.1.5 or later and recommends current WSL. Use Docker Desktop's integrated engine;
do not install a second Docker Engine or Linux GPU driver inside Ubuntu for this
runbook.

Prepare substantial free disk space: budget **at least 40 GB** for images, build
cache and model weights, with more headroom if Docker already holds large images.
The checkpoint alone is about **9.1 GB**. Plan around **24 GB of RAM available to
WSL** for the GPU path on a host with enough physical RAM; exact host memory needs
depend on loading and other applications. Close other GPU workloads. The supplied
driver/CUDA banner alone does not establish that this model fits 12 GB; step 7
tests complete inputs on your actual GPU.

## 2. Clone and check Docker from Ubuntu

Open **Ubuntu** from the Windows Start menu. Keep the checkout under your Linux
home directory for this setup:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/liamstojanovic/buildingBetterCode-monorepo.git
cd buildingBetterCode-monorepo
git switch main
git pull --ff-only
mkdir -p .canopy/demo-results
docker version
docker version --format '{{.Server.Platform.Name}}'
docker compose version
docker info --format '{{.OSType}}'
docker compose -f docker-compose.demo.yml --profile '*' config --quiet
```

If you already have the repository, enter it and run the `git` update and Docker
checks without cloning again. `docker version` must show both **Client and Server**;
the platform line identifies the **Docker Desktop** version, distinct from the
Engine and Compose versions. The OS check must print **linux**. Compose v2 is required; this configuration uses
the GPU reservation syntax supported by Compose 2.24.6 and later.

For the rest of this terminal session define a short command:

```bash
dc() { docker compose -f docker-compose.demo.yml "$@"; }
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
export RUN_ID
printf 'This rehearsal ID: %s\n' "$RUN_ID"
```

If you reopen Ubuntu, return to the repository and repeat these three definitions.
Use a fresh `RUN_ID` for another run: result files intentionally refuse overwrites.

## 3. Build and start the frontend and API

```bash
dc build api web
dc up -d --wait
dc ps -a
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/api/runs
```

Expected: `fixtures` exits **0**, while `api` and `web` are **healthy**. The fixture
job generates current evidence plus real synthetic recordings before the frontend
starts. It makes no provider requests. The health response is `ok`; `/api/runs`
returns JSON (initially an empty run list).

Open these in your **Windows browser**:

- **Frontend:** <http://127.0.0.1:8080/>
- **Combined retained evidence hub:** <http://127.0.0.1:8080/evidence/>
- **API schema:** <http://127.0.0.1:8000/docs>
- **Fresh output directory:** <http://127.0.0.1:8080/generated/>

Only host loopback is published. This API supplies a fixed demo operator identity;
keep this local setup on your machine.

## 4. Rehearse live workflow execution

In the frontend:

1. Select **Live API** as the data source. Confirm the three catalog/quote/greeting
   steps and synthetic-data label.
2. Select **Same task, again** and click **Run comparison**. A visible
   governed preparation run first warms the cache. The measured baseline then
   executes **3 steps**, while the governed repeat **reuses 3**.
3. Click **Exit focus** if you need the full console. Select **Relevant source
   updated**, then **Run comparison**. The changed catalog source reruns its two
   dependent steps; the independent greeting remains reused.
4. Select **Unrelated source updated** and run again. All three steps remain reusable.
5. Inspect a node to show evidence, dependency provenance and why it executed or reused.

Explain: “Reuse is scoped to the inputs and evidence a step actually depends on.
Changing a relevant source invalidates its dependents.” These are actual runtime
events from a Python fixture; they are not measured LLM token savings.

**Before every rehearsal, and whenever more than one hour has passed**, refresh
the task and select **Load core task** in the full console:

```bash
dc run --rm fixtures
```

The refreshed files are served from a shared volume, so there is no frontend
rebuild. Existing browser tasks retain their original timestamps until reloaded.

## 5. Generate fresh advisor, governance and communication results

One command runs the synthetic budget/replay example, the drift/deduplication
smoke checks, the offline advisor on **all nine** retained provider/category
comparisons, and the communication measurements:

```bash
dc run --rm tools python deploy/demo/run_cpu_demo.py \
  --output-dir "/results/cpu-$RUN_ID"
printf 'Open http://127.0.0.1:8080/generated/cpu-%s/index.html\n' "$RUN_ID"
```

Open the printed URL. All files also appear in
`.canopy/demo-results/cpu-$RUN_ID/` in your checkout. This service has networking
disabled. Expected advisor totals: **9 reports, 21 recommendation groups, 0 new
model calls**. The groups cover business decisions, tool selection and next-step
decisions across the three providers; they represent **7 distinct authored cases**.

To show the fresh findings in React, click **Exit focus**, select **Optimization**
in the left navigation, then **Import advisor report**. Use `explorer.exe .` from
Ubuntu to locate `.canopy/demo-results/cpu-$RUN_ID/advisor/` in Windows Explorer.
Import one JSON per category, for example `openai-business-decision.json`,
`openai-tool-selection.json` and `openai-next-step.json`. Select a group to inspect
its choices, signals, blockers, observed usage and source samples; expand
**Analysis coverage and limits** to show the analyzer's resource measurements.

**Load advisor demo** is a separate, bundled seven-group synthetic demonstration.
It is useful for the broader UI tour, but use the freshly generated reports above
when demonstrating discovery from actual retained hosted calls. Imported reports
stay in browser memory until reload. Switching back to **Execution** preserves
the workflow state.

The generated CPU page also links each raw JSON. Inspect `finding.category`,
`finding.signals`, `finding.choices`, `finding.blockers`, and `observed.model_calls`.
These retained calls produce `needs_mapping` / `not_evaluated` discovery findings.
The advisor does not automatically ingest the separately authored mapping or JEV
evaluation results. Step 8 demonstrates that separate evaluation.

Show **Budget, reuse and replay**: the `budget_refused` run stops under a zero-step
limit; replay returns recorded output without dispatching handlers. Show **Drift
containment and message deduplication**: the controlled policy regression blocks
execution and duplicate insertion is false. These use synthetic policy probes;
they demonstrate containment, not general compliance certification. The browser's
mock budget-refusal scenario is not a control over the live API's server budget.

Open **fresh communication measurements**:

1. Compare compact JSON, independent CBOR messages and shared-context CBOR batches.
2. Show **618 event messages in 36 batches**, successful lossless decoding and
   **36 duplicate message redeliveries** suppressed without duplicate mailbox entries.
3. Open the actual wire-frame export and the mailbox timings.
4. Show the dense-float counterexample: JSON is smaller, so automatic format
   selection keeps JSON. Serialization CPU and byte size are reported separately.

The retained reference payload totals are JSON **771,411 bytes**, independent CBOR
**585,595 bytes**, shared CBOR **356,491 bytes** (53.79% smaller than JSON). Compare
your actual rerun rather than asserting the retained timing. The demo measures
encoding and local mailbox operations; it does not measure internet transport,
token, energy or end-to-end workflow savings. The communication component is
opt-in and is not silently inserted into every API request.

## 6. Show actual hosted-model recordings

The evidence hub contains all three categories across OpenAI, Anthropic and
Gemini. Those are actual retained hosted calls using invented support data:
**63 calls and 21 governed warm reuses**, not new requests made during playback.

To play one in React, click **Exit focus**, then **Import recording**, and select:

```text
core/examples/recordings/advisor-live-2026-10-17/openai-next-step/frontend-comparison.json
```

Use `explorer.exe .` from Ubuntu to open the repository in Windows Explorer and
find the file for the browser file picker. The same layout exists for
`openai`, `anthropic`, `gemini` × `business-decision`, `tool-selection`, `next-step`.

Choose **First encounter** for the preserved OpenAI next-step failure:
`choose_retrieval` selected `respond`. A useful demo retains that error. Across all
63 retained calls, **62 normalized choices** match the authored expectations, but
only **28 are both correct and in strict requested bare-JSON format**. This small
fixture collection is not a general model ranking.

Advisor, JEV and communication JSON are different report formats. Use
**Optimization → Import advisor report** for advisor JSON; use the dedicated
pages for JEV and communication reports. None belongs in **Import recording**.

## 7. Build the GPU image and verify this 12 GB machine

Do this well before presenting: the first GPU build downloads large CUDA/PyTorch
packages. The separate image keeps core Pollard 1.6.2 and the Pollard-Jev worker's
Pollard 1.6.0 in isolated Python environments.

```bash
dc build jev
dc run --rm gpu-check /opt/jev-env/bin/python deploy/demo/gpu_preflight.py \
  --device-only --output "/results/device-$RUN_ID.json"
```

Expected: `ok: true`, `cuda_available: true`, `bf16_supported: true`, and your GPU
name/memory. This uses the pinned PyTorch **2.11.0+cu128** runtime. The `CUDA 13.3`
label in your supplied `nvidia-smi` is not the Python package version to install.
This check tests actual container access before downloading model weights.

Download the model once, explicitly:

```bash
dc run --rm model-download
```

This downloads only the five reviewed files for `AlexWortega/openjev`, checkpoint
`qwen3.5-4b-nli-v2`, revision
`552759daad712f1af6c4c13dabcb1e047886fc9c`, then checks their sizes and SHA256 hashes
against the committed manifest. It needs internet but no Hugging Face token. The
named `openjev-model` volume retains the model for later runs. An interrupted
download can be retried with the same command.

Now run the complete capacity check:

```bash
dc run --rm gpu-check /opt/jev-env/bin/python deploy/demo/gpu_preflight.py \
  --cache-dir /models --output "/results/gpu-preflight-$RUN_ID.json"
```

Expected: exit code **0**, `ok: true`, `stage: "all_seven_cases_fit"`, **7 model
calls / 26 hypothesis pairs**. The report includes GPU name, package versions,
model hashes, actual token lengths and peak allocated/reserved memory. The model
cache is read-only and networking is disabled during this check and step 8.

The 1,024-token guard rejects oversized inputs; it does not truncate them. These
seven retained cases had complete pair lengths of 91–217 tokens in the reference
run. The preflight defaults to an 11,264 MiB PyTorch allocator ceiling (or 95% of
device capacity if lower). CUDA context and other programs use additional memory.
Passing establishes capacity for these complete cases on this run only. If it
fails, retain the diagnostic JSON and follow troubleshooting below.

## 8. Compare actual local JEV with the retained LLM calls

After the capacity check succeeds, run the mapped comparison through the real
handler/worker path:

```bash
dc run --rm jev python core/examples/jev_recorded_comparison.py \
  --worker-python /opt/jev-env/bin/python \
  --model-cache-dir /models --device cuda \
  --max-context-tokens 1024 --timeout-seconds 600 --repeats 2 \
  --journal "/results/jev-trials-$RUN_ID.jsonl" \
  --output-dir "/results/jev-$RUN_ID"
printf 'Open http://127.0.0.1:8080/generated/jev-%s/index.html\n' "$RUN_ID"
```

This makes **14 new local JEV calls** (7 cases × 2 passes), plus the 7 capacity
calls from step 7. It makes zero new hosted LLM calls. Each comparison trial is
flushed to the journal, including a failure if execution fails. Successful runs
write `comparison.json`, `index.html` and `manifest.json`.

On the generated page, inspect all three categories:

| Category | Cases | What to inspect |
| --- | --- | --- |
| Business decision | Support priority | Expected choice, raw scores, abstention gate, explanation still required |
| Tool selection | Knowledge search, incident lookup, ticket lookup | Allowed candidates, chosen hypothesis, acceptance gate; no tool executes |
| Next-step decision | Retrieve, clarify, stop | State, authored transitions, explicit choices and deterministic rule baseline |

The **historical native reference**, measured on a **4090 Laptop GPU with 16 GB**, got all
7 raw choices right and accepted 6/7 per pass. Business priority chose `high` but
deferred because its 0.115 margin missed the fixed 0.15 gate. All three tool choices
and all three next-step choices were accepted. Keep a different result from your
machine visible; do not lower the gate or remove a candidate to reproduce a badge.

That native run's warm median was 491.20 ms and the first call included approximately
36.47 s of loading. Those are **not promised 4080 timings** and are not a controlled
comparison with hosted latency. Report your fresh timings, first-load overhead,
and abstentions separately. JEV chooses among supplied hypotheses; it does not
supply the business rationale or authorize downstream execution.

This comparison intentionally uses the complete committed collection. Arbitrary
fresh hosted exports are not drop-in replacements: the evaluator expects the
seven-case/nine-comparison inventory, expected-choice file and manifest hashes.

## 9. Optional live approval demonstration

This switches to a separate approval database and recreates both API and nginx so
the proxy resolves the recreated API correctly:

```bash
docker compose -f docker-compose.demo.yml -f deploy/demo/compose.approval.yml \
  up -d --wait --force-recreate api web
```

In the frontend select **Live API**, **Load core task**, and **Run comparison**.
The warm preparation pauses on `read_catalog`. Select that Canopy node →
**Inspect approval** → **Approve & retry**. Show the exact approved retry
continuing. Each new bound request can require its own approval; approving the
preparation does not approve every baseline/comparison request automatically.

Return to the normal demo afterward:

```bash
dc up -d --wait --force-recreate api web
```

Reload the page and **Load core task** again.

## 10. Presentation order and stage fallback

Prepare these tabs before presenting: React frontend, retained evidence hub,
fresh CPU results, fresh JEV results, fresh communication page. Run model downloads
and the first GPU warm-up beforehand. Keep the laptop plugged in and other GPU
workloads closed.

| Time | Action | Point to make |
| --- | --- | --- |
| 0:00–1:00 | Live API unchanged, then relevant source updated | Real execution records; reuse only while relevant evidence remains valid |
| 1:00–2:00 | Hosted recording across provider/category examples | Same baseline/governed task, retained outputs and failures |
| 2:00–3:00 | Optimization view with fresh advisor imports | Offline discovery of bounded business/tool/next-step candidates, no extra inference |
| 3:00–4:15 | Fresh JEV page, including business deferral | Actual local evaluation and explicit abstention; selection is distinct from generation |
| 4:15–5:00 | Fresh communication page and governance JSON | Smaller lossless messages, batching/deduplication, enforced budgets and containment |

If live API is unavailable, select **Recorded runs / Core recordings** in React.
If the GPU fails, use the **retained JEV page** from the evidence hub and state that
it is the earlier 16 GB-machine run. Playback needs no GPU or provider credentials.
After images and weights are prepared, the normal demo runs without external
network access. Keep fresh and retained results labeled distinctly.

## Optional: record a new hosted comparison for your benchmark

This part needs your own provider account and makes billable model requests.
It is separate from the credential-free demo. The `live-models` service has network
access but receives no key unless explicitly passed. For one fresh OpenAI sample
comparison, enter a model ID available to your account:

```bash
read -r -p 'OpenAI model ID: ' OPENAI_MODEL
read -r -s -p 'OpenAI API key: ' OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
dc run --rm -e OPENAI_API_KEY live-models python core/examples/live_comparison.py \
  --provider openai --model "$OPENAI_MODEL" --output-dir /results/live-comparisons
unset OPENAI_API_KEY
```

The default one-step task normally makes **3 real calls and 1 warm reuse**. Inspect
the complete saved report and failures. For Anthropic/Gemini/compatible endpoints,
custom business/tool/next-step tasks, key names, and independent scoring, use the
[benchmark handoff](docs/BENCHMARK_HANDOFF.md) and
[live model guide](core/docs/live_models.md). Never put keys into `VITE_*` variables,
Dockerfiles, recording exports or committed files. The normal frontend API still
uses its synthetic registry after this optional command.

## Troubleshooting and repeat runs

| Symptom | Action |
| --- | --- |
| Docker command missing in Ubuntu / cannot connect | Start Docker Desktop; enable WSL integration for this exact distribution; confirm Linux containers. Reopen Ubuntu and repeat `docker version`. |
| Build interrupted / dependency download fails | Rerun the same `dc build` command; keep pinned versions. Check internet/disk capacity. |
| Ports 8080 or 8000 occupied | In Ubuntu set `export CANOPY_WEB_PORT=8081` and/or `export CANOPY_API_PORT=8001`, then `dc up -d --wait`. Use the new browser ports; internal nginx→API stays at 8000. |
| Fixtures missing, stale evidence or live run blocked after waiting | `dc run --rm fixtures`; click **Load core task**. Historical playback is allowed to remain historical. |
| Proxy 502 after switching API mode | `dc restart web`, then check `dc logs --tail=80 api web`. |
| CUDA unavailable | Check Windows NVIDIA driver, WSL update, Docker WSL2 backend and Linux mode. Run the device-only check again with a new output filename. |
| CUDA error 500 / `named symbol not found`, despite working `nvidia-smi` | Update Docker Desktop to a current release (at least 4.31), then restart it. Older bundled runtimes can omit a required library for newer Windows NVIDIA drivers. Do not downgrade the model or bypass the CUDA check. |
| Model missing / hash mismatch | Rerun `dc run --rm model-download`. Do not bypass verification; retain the error if the downloaded pinned file still differs. |
| GPU out of memory | Close other GPU applications, rerun capacity check with a fresh ID. Do not run preflight and comparison simultaneously. If complete cases still fail on 12 GB, use retained evidence and report the capacity failure; quantization/offload is not implemented in this path. |
| Host memory kill (exit 137) | Inspect Docker/WSL memory limits and available physical RAM. Increase WSL allocation only within your machine's capacity, restart Docker, and retry. |
| `output already exists` / new-directory error | Set `RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)` and retry. Do not erase prior measurements to hide a failed run. |
| New shell says `dc: command not found` | Return to the repo and redefine the Bash function in step 2. |

Useful checks:

```bash
dc ps -a
dc logs --tail=80 fixtures api web
docker system df
```

**If WSL has too little host RAM:** on a machine with at least 32 GB physical RAM,
you can allow WSL up to 24 GB, leaving room for Windows. In **Windows PowerShell**:

```powershell
notepad "$env:USERPROFILE\.wslconfig"
```

Preserve existing settings and add/update `memory` under the existing `[wsl2]`
section, or create that section if absent:

```ini
[wsl2]
memory=24GB
```

Save work in all WSL distributions, exit Docker Desktop, then run
`wsl --shutdown` in **PowerShell**. This stops every WSL distribution. Restart
Docker Desktop and Ubuntu, then repeat the Docker checks and rehearsal. Choose a
smaller allocation if your physical RAM cannot support this example; do not
assume swap makes the GPU path suitable for a low-memory host. See
[Microsoft's WSL memory configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#main-wsl-settings).

Normal shutdown preserves the model volume, API databases and output files:

```bash
dc down
```

Restart with `dc up -d --wait`, refresh fixtures, and load the core task again.
Do **not** add `--volumes` / `-v` unless you deliberately want to delete the named
model cache and API databases. Generated results remain in `.canopy/demo-results/`
and are Git-ignored. Copy the selected result folder and its trial journal for
your benchmark evidence; do not commit credentials or private databases.

## Validation performed on 17 October 2026

The [published container bundle](core/examples/recordings/container-benchmarks-2026-10-17/README.md)
now retains the [GPU comparison](core/examples/recordings/container-benchmarks-2026-10-17/jev/index.html),
[capacity report](core/examples/recordings/container-benchmarks-2026-10-17/gpu/gpu-preflight-validation-named-volume.json)
and a [new CPU rerun](core/examples/recordings/container-benchmarks-2026-10-17/cpu/index.html).
The CPU rerun produced nine advisor reports / 21 groups / zero model calls, and
its [communication report](core/examples/recordings/container-benchmarks-2026-10-17/cpu/comms/index.html)
reproduced 618 events / 36 batches, with 771,411 JSON bytes versus 356,491 shared
CBOR bytes. The original hosted observations were retained, not rerun or redated.

All three Docker images built successfully. The CPU stack, live frontend/API,
warm reuse, freshness changes, approval flow, advisor imports and communication
reports were exercised. Core tests passed **821/821**; frontend tests passed
**59/59**, including a successful build and contract check. The implementation's
[CI run also passed](https://github.com/liamstojanovic/buildingBetterCode-monorepo/actions/runs/39064452878).

The GPU image was exercised on a **4090 Laptop with 16 GB VRAM**, using the Linux
Docker model volume. The capacity check completed all **7 calls / 26 hypothesis
pairs**, with a maximum of 217 tokens and no truncation. Peak PyTorch memory was
**9,143.99 MiB allocated / 9,186 MiB reserved**, under the 11,264 MiB allocator
ceiling. These measurements do **not** establish fit on your physical 12 GB 4080:
run the complete capacity check on your machine before the comparison.

The subsequent comparison completed **14/14 raw matches** and **12/14 accepted
matches** across two passes. The business decision deferred in both passes;
all tool-selection and next-step decisions were accepted. Warm median latency
was **301.87 ms**; the first call took **6,469.67 ms**, including **5,643.78 ms** of
model loading. These are a local rehearsal on seven cases, not a controlled
cross-provider speed benchmark or a promise of your machine's performance.

**Local compatibility caveat:** the validation host had Docker Desktop 4.28.
Its standard GPU setup failed CUDA initialization because a Windows driver
library was missing inside the container. A temporary, read-only library bind
allowed the GPU checks above; no host software was changed, and that workaround
is not part of the shipped Compose files. The current-Docker GPU configuration
must still be checked on your machine as described in steps 1 and 7. We reused
and SHA256-verified all five pinned model files rather than downloading them
again, and made no new hosted-model calls during this rehearsal.

## Included deployment files

| File | Purpose |
| --- | --- |
| [docker-compose.demo.yml](docker-compose.demo.yml) | CPU stack, isolated tools, optional hosted jobs, GPU preparation and inference services |
| [Dockerfile.core](deploy/demo/Dockerfile.core) | Pinned core dependencies, API and CPU jobs |
| [Dockerfile.web](deploy/demo/Dockerfile.web) | Frozen Bun install, Node build, nginx frontend and evidence serving |
| [Dockerfile.jev](deploy/demo/Dockerfile.jev) | Separate core and CUDA-enabled Pollard-Jev environments |
| [requirements-openjev-container.txt](core/requirements-openjev-container.txt) | Linux/Python 3.12 worker dependencies and hashed PyTorch wheel |
| [nginx.conf](deploy/demo/nginx.conf) | API proxy, fresh fixtures, retained and generated evidence pages |
| [init_fixtures.py](deploy/demo/init_fixtures.py) | Fresh one-hour task and synthetic recording export |
| [start_api.py](deploy/demo/start_api.py) | Container binding and fixed local demo operator |
| [run_cpu_demo.py](deploy/demo/run_cpu_demo.py) | One-command governance/advisor/comms run and linked results index |
| [prepare_openjev.py](deploy/demo/prepare_openjev.py) | Explicit pinned model download and SHA256 verification |
| [gpu_preflight.py](deploy/demo/gpu_preflight.py) | CUDA check and measured complete-case capacity test |
| [compose.approval.yml](deploy/demo/compose.approval.yml) | Optional API approval mode |

These commands are the container demo path. Component APIs and measurement
semantics remain documented in [TEAM_HANDOFF.md](docs/TEAM_HANDOFF.md),
[FRONTEND_HANDOFF.md](docs/FRONTEND_HANDOFF.md),
[BENCHMARK_HANDOFF.md](docs/BENCHMARK_HANDOFF.md), and
[JEV_COMMS_DEMO.md](docs/JEV_COMMS_DEMO.md).
