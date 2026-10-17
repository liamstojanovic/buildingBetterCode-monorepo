# Canopy execution studio

Bun, React, TypeScript, Vite, React Flow, and Storybook. The frontend consumes
version 1 core contracts and owns only `web/`. Use Node 22.12+ or 24 for the Vite
and Storybook toolchain; Bun manages dependencies and scripts.

## Start the end-to-end demo

For the complete Windows/WSL2 Docker setup, including GPU JEV inference, the
offline advisor and compact communication measurements, follow
[demo_handoff.md](../demo_handoff.md). The commands below are the direct local
development path.

The working end-to-end path is **React frontend → local Python API → real Canopy
runtime with synthetic handlers**. It needs no model credentials. The initial
seven-step PortMiami cargo screen is a frontend mock; selecting **Live API** loads
the backend's three-step catalog/quote/greeting workflow instead.

One-time setup, from the repository root (macOS/Linux; Python 3.11 or 3.12):

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r core/requirements.lock
.venv/bin/python -m pip install -e 'core[dev,models]'
cd web
bun install --frozen-lockfile
```

If an existing environment already has the pinned core dependencies, use its
Python executable in place of `.venv/bin/python` throughout. On Windows, use
`.venv\Scripts\python.exe`; see the [team setup](../docs/TEAM_HANDOFF.md).

**Terminal 1 — repository root:** refresh the fixtures before rehearsal, then
start the backend. The export runs five paired synthetic trials per case, updates
only `web/public/recordings/`, and makes no provider requests.

```sh
.venv/bin/python web/scripts/export-core-fixtures.py --benchmark
.venv/bin/python core/examples/local_api.py
```

**Terminal 2 — repository root:**

```sh
cd web
bun run dev --strictPort
```

Open <http://127.0.0.1:5173> and select **Live API** in the data-source menu, then
**Run comparison**. The warm scenario first primes a real governed cache; baseline
then executes three steps while Canopy reuses three. Select **Relevant source
updated** and run again: two dependent steps execute, while the greeting remains
reusable. Select a graph node to inspect evidence, dependencies and provenance.
Run comparison enters presentation view; **Exit focus** restores the full console.

The API schema is at <http://127.0.0.1:8000/docs>. Keep both terminals running;
Ctrl-C stops each server. If ports 8000 or 5173 are occupied, use the existing demo
processes or stop them before restarting. For another backend port, start it with
`--port 8001` and run Vite with `CANOPY_API_URL=http://127.0.0.1:8001 bun run dev`.

**Stage fallback:** select **Recorded runs** (or **Core recordings** in the full
console) and run the comparison. This plays committed actual synthetic core events
without the API. **Mock workspace** demonstrates the cargo story and UI only.
Keep the data-source and synthetic labels visible in either case.

## Repository integration status

Aligned with the October 17 [frontend handoff](../docs/FRONTEND_HANDOFF.md):

| Capability | Current state |
| --- | --- |
| Runtime, recording, scoped reuse, freshness, budgets, approvals, replay | Implemented in `core/`; synthetic HTTP demo connected to this frontend |
| Live events | Synchronous `POST /runs` plus ordered event polling; async start/SSE remain backend work |
| Hosted-model comparisons | Python handlers and CLI implemented; import retained reports/stores using the converter below |
| PortMiami cargo execution | Frontend mock; registered cargo handlers and representative provider benchmarks still needed |
| Drift containment and compact messaging | Core components and smoke script; no dashboard HTTP endpoints or frontend report adapter |
| Pollard-Jev/OpenJEV | Actual local inference evaluated against retained LLM calls in all three decision categories; standalone results page, no dedicated React adapter |
| Optimization advisor | Offline core analyzer implemented; Optimization view imports its reports and displays unevaluated findings |
| Automatic backend switching | Not implemented; advisor findings do not activate a replacement backend |

The runtime executes an authored dependency graph **sequentially**. The local API
registers synthetic handlers only; choosing a provider in a browser cannot enable
hosted inference. The [advisor design](../docs/ADVISOR_DESIGN.md) and
[benchmark handoff](../docs/BENCHMARK_HANDOFF.md) distinguish implemented offline
analysis/evaluation from future routing. The repository's GitHub Actions workflow
currently checks the core; frontend checks below must be run separately.

## Components and checks

```sh
bun run storybook
bun run typecheck
bun run test
bun run build
bun run build-storybook
bun run contracts:check
```

Storybook covers empty/running/completed execution, warm reuse, source changes,
approval waits, refusal, failure, reconnecting, recorded playback, and an MSW-backed
HTTP integration. Components take props; transport adapters and the reducer are
independently testable. `bun run contracts` generates types from `../core/schemas/`.
Do not edit generated types or change core schemas from the frontend.

## JEV optimization findings

Select **Optimization** in the left navigation, then **Load advisor demo** for an
actual seven-group report generated by the existing core advisor. The underlying
handlers and observations are synthetic; no hosted model or OpenJEV inference
occurs. The native-shaped `review_operations` candidate demonstrates structural
compatibility, not replacement quality. Inspect the tool/next-step mapping gaps,
retained generation, deterministic alternative, and source sample references.

**Import advisor report** accepts an `advisor-report.json` up to 20 MiB with
`report_type: "canopy.offline_advisor"`, `schema_version: "1"`, and
`analyzer_version: "offline-advisor-v1"`. Runtime validation uses the existing core
JSON Schema; generated TypeScript types alone are not validation. Comparison
bundles, task files, and `demo.json` manifests are different formats and are rejected.

Filter by category and status, then select a group to inspect its choices, reasons,
blockers, required changes, observed usage, revisions, and bounded sample references.
**Analysis coverage and limits** exposes skipped counts, resource measurements,
configured bounds, and partial-analysis notices. Unknown measurements remain
unknown. Every current finding is **Not evaluated**; observed dispatches are not
savings, and imported provenance is not independently verified by the browser.

Advisor imports have separate state from execution. Switching workspaces preserves
comparison results, selection, approvals, presentation state, and live observation.
A failed import retains the previous report. Reports stay in browser memory for
this session; reload clears them. Copyable run/step references do not import source
recordings or establish access to the connected HTTP service. Report strings render
as text. This view does not call an advisor endpoint or activate backend routing.

The bundled report is `public/recordings/advisor-demo.json`; its adjacent
`advisor-demo.provenance.json` records the generator, source commit, timestamp, and
report hash. Regenerate from the repository root with the existing core dependency
environment:

```sh
.venv/bin/python web/scripts/export-advisor-fixture.py
```

Or run `bun run fixtures:advisor` from `web/` if `python3.12` already has the core
dependencies. The exporter reads core code, creates databases in a temporary
directory, and writes only the two frontend report files. It makes no provider
requests. The checked-in report works without Python or credentials.

Storybook's **Canopy / Optimization** includes the actual report and clearly labeled
frontend challenge fixtures for missing resources, partial analysis, mixed origins,
and retained work, along with empty, native-candidate, mapping, unsupported, and
import-failure states.

## Actual core integration

Vite proxies `/api` to `http://127.0.0.1:8000`. Set `CANOPY_API_URL` for another
local backend or `VITE_API_BASE_URL` for a different browser path. Static production
hosts must configure their own `/api` reverse proxy; building `dist/` does not
deploy the Python API or a proxy.
Never put provider credentials in browser source or Vite environment variables.

The committed recording contains actual core events for five paired repetitions
of cold, unchanged, relevant-change, and unrelated-change cases. Order alternates
and outputs are independently checked. These handlers are deterministic and
synthetic: no live provider requests, customer data, or dollar-savings claims.
**Core recordings** loads these records for playback without a provider.

Choose **Live API** to run the existing three-handler synthetic workload. The
cargo mock's handler names are not registered on that service. Loading the bundled
core task creates a fresh local workflow namespace to isolate the session cache;
evidence timestamps and contents are preserved. Warm/changed scenarios prepare
the cache through a visible real governed execution. Cold cases use a new namespace.
The core task expires one hour after export: regenerate before a later live demo,
then click **Load core task** in the full console to replace the browser's snapshot.
Historical recorded playback remains usable after that expiry. With a production
build, regenerate before `bun run build` so `dist/` contains the refreshed files.

**Import task** accepts Task or `{task: Task}` JSON supplied by the runtime owner.
Imported identifiers and evidence snapshots are preserved. Changed-source scenarios
update the certificate, or the first evidence for the core fixture. Unrelated
scenarios add unused synthetic evidence. These are controlled demonstration inputs.

Start the core with `--require-approval` to test approvals. Inspect the affected
node and its exact binding, then approve or deny. Approved retries preserve the
original request and add its step approval ID. The trusted service supplies reviewer
authority; this local demonstration does not implement production authentication.

## Backend SSE handoff

The current core exposes synchronous starts and polling. This frontend supports
these backend-owned extensions without changing existing core interfaces:

- `POST /runs/async`: same body as `/runs`; HTTP 202 returns
  `{ "run_id": "...", "status": "running" }` promptly. Reserve that exact ID
  before background dispatch. `VITE_ASYNC_START_PATH` overrides the endpoint path.
- `GET /runs/{id}/events/stream?after_sequence=-1`: `text/event-stream`, with
  `id: <sequence>`, `event: run_event`, and `data: <RunEvent JSON>`. Support
  `Last-Event-ID`, heartbeat comments, existing tenant checks, and terminal drain.
- Execute synchronous handlers outside the API event loop. Subscriber disconnects
  do not cancel execution. Disable reverse-proxy buffering for the stream.

EventSource handles reconnect IDs. The reducer sorts and deduplicates events;
the final result remains authoritative. A bounded SSE recovery window falls back
to ordered polling. Only an explicit 404/405 from async start permits a synchronous
POST fallback. A timeout or 5xx never triggers another execution request.

For synchronous starts, keep the POST pending, discover its run by exact task ID,
mode, and exclusion of previous run IDs, then poll every 300 ms. Ambiguous matches
are never guessed. Reads can retry. **Stop watching** ends observation, not server work.

## Real benchmark recordings

For a real-provider demo, follow the [live model setup](../core/docs/live_models.md)
and configure the selected provider's key in the Python process environment. From
the repository root, choose a model your account can access:

```sh
.venv/bin/python core/examples/live_comparison.py \
  --provider openai --model YOUR_MODEL_ID --output-dir .canopy/comparisons
```

This explicitly makes provider requests and may incur charges. The default input
is an invented support ticket, not cargo paperwork. The current CLI compares
baseline first/repeat and governed cold/warm; it does not generate relevant-change
or unrelated-change benchmarks or independently score answer quality.

The core's `live_comparison.py --output-dir` retains a report and baseline/governed
databases. Keep the whole generated directory together, including both SQLite
stores. From `web/`, convert those artifacts without executing models:

```sh
../.venv/bin/python scripts/import-comparison.py /path/to/comparison/report.json
```

Pass multiple report paths for repeated cold/warm trials. Add `--evaluations`
with an independently produced JSON array such as:

```json
[
  {
    "run_id": "actual-id",
    "passed": true,
    "checked_fields": 12,
    "correct_fields": 12,
    "method": "Independent output checks"
  }
]
```

Click **Import recording** and select the generated
`web/public/recordings/provider-comparison.json`. Raw `report.json`,
`synthetic_demo.py` output and `component_smoke.py` output are not directly accepted
by this UI; it requires a `canopy-comparison/v1` bundle. The converter
reads saved stores only and checks report/result agreement. It does not fabricate
events, quality scores, cost, or changed-source scenarios. See `src/api/types.ts`
and the committed core recording for the complete `canopy-comparison/v1` bundle.
The live comparison runner currently leaves dollar cost and energy unknown;
importing token measurements alone will not enable dollar projections.

## Troubleshooting before presenting

- **Connection/proxy error:** check the API is running on port 8000 and open its
  `/docs`; start Vite after setting any `CANOPY_API_URL` override.
- **Stale evidence or refused run:** regenerate the fixtures and click **Load core
  task**. Refreshing the browser alone does not refresh the evidence on disk.
- **Unknown cargo handler:** use **Load core task** for the stock API. Cargo tasks
  require server-side handler registration by the backend owner.
- **Async endpoint 404:** expected with today's core; the client uses the existing
  synchronous start and polling path. It does not require WebSockets or SSE to run.
- **Graph finishes too quickly:** synthetic handlers are fast. Use recorded
  playback for inspection; playback speed does not alter measured execution time.
- **Recording conversion fails:** supply the complete retained comparison
  directory, including `baseline.sqlite3` and `governed.sqlite3`, not just its report.

## Measurement rules

- Final RunResult is authoritative. Live counters use settled usage events.
- Preserve tokens_known, elapsed_known, and null cost/energy. Fully reused core
  work can report zero new provider spend; unmeasured spend remains unknown.
- Synthetic model dispatch counts are not provider requests. Mock animation is
  illustrative; playback speed never changes historical measurements.
- Blocked, failed, incomplete, or unscored tasks cannot establish savings.
- Dollar projections require non-synthetic, successful, independently scored
  cold/warm runs, measured costs, and at least five repetitions. The formula
  uses paired median cold/warm cost differences weighted by repeat share and volume, including cold
  overhead. It is gross provider savings; infrastructure costs are excluded.

## Three-minute demo

1. **0:00–0:30:** Introduce the cargo packet and repeated analysis.
2. **0:30–1:35:** Compare baseline/warm runs and independent correctness.
3. **1:35–2:10:** Change the certificate; inspect affected and unaffected work.
4. **2:10–3:00:** Inspect provenance and show measured impact when available.

Keep Recorded and synthetic labels visible in the stage fallback. The cargo mock
demonstrates UI behavior until the benchmark owner supplies registered cargo
handlers and representative results.
