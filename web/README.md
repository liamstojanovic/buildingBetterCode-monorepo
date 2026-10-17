# Canopy execution studio

Bun, React, TypeScript, Vite, React Flow, and Storybook. The frontend consumes
version 1 core contracts and owns only `web/`. Use Node 22.12+ or 24 for the Vite
and Storybook toolchain; Bun manages dependencies and scripts.

```sh
cd web
bun install --frozen-lockfile
bun run dev
```

Open <http://127.0.0.1:5173>. The initial **Mock workspace** is an explicitly
synthetic PortMiami cargo illustration. Run comparison enters presentation view;
Exit focus returns to the full console. Select any graph node for its evidence,
dependencies, decision reasons, and reuse provenance.

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

## Actual core integration

Use the pinned Python 3.11/3.12 environment from `docs/TEAM_HANDOFF.md`. From the
repository root, start the synthetic local API:

```sh
PYTHONPATH=core/src .venv/bin/python core/examples/local_api.py
```

Vite proxies `/api` to `http://127.0.0.1:8000`. Set `CANOPY_API_URL` for another
local backend or `VITE_API_BASE_URL` for a different browser path. Production hosts
must configure their own `/api` reverse proxy; Vite's proxy is development-only.
Never put provider credentials in browser source or Vite environment variables.

Generate fresh fixtures with the same core environment:

```sh
cd web
../.venv/bin/python scripts/export-core-fixtures.py --benchmark
```

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
The core task expires one hour after export: regenerate before a later live demo.

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

The core's `live_comparison.py --output-dir` retains a report and baseline/governed
databases. Convert those artifacts without executing models:

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

Import the generated `public/recordings/provider-comparison.json`. The converter
reads saved stores only and checks report/result agreement. It does not fabricate
events, quality scores, cost, or changed-source scenarios. See `src/api/types.ts`
and the committed core recording for the complete `canopy-comparison/v1` bundle.

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
