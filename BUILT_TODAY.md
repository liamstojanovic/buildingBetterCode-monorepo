# Build provenance — October 17, 2026

This hackathon project adds a new enterprise integration layer around existing
open-source packages. The upstream packages were developed before this event and
are dependencies, not code claimed as newly written at the hackathon.

## New project work

- Shared task, evidence, policy, budget, usage, event and result contracts.
- Enterprise adapters for recorded execution/reuse, budgets, temporal validity,
  compact agent messages, action approval and policy conformance monitoring.
- Runtime and local HTTP integration surface for the separately built frontend
  and benchmark, plus synthetic examples and component/integration tests.
- Live model handlers for OpenAI Responses, compatible Chat Completions,
  Anthropic Messages, Gemini generateContent and local Pollard-Jev inference; an
  isolated baseline/governed comparison runner with provider usage reporting.
- Packaging, reproducible environment, CI and integration documentation.

## Existing dependencies

| Dependency | Pinned version | Intended responsibility |
| --- | --- | --- |
| [Pollard](https://github.com/jemsbhai/pollard) | 1.6.2 | Execution evidence and budget primitives |
| [Chronofy](https://github.com/jemsbhai/chronofy) | 0.1.9 | Temporal validity calculations |
| [Epicormic](https://github.com/jemsbhai/epicormic) | 0.1.0 | Behavioral drift observations and analysis |
| [CBOR-LD-ex](https://pypi.org/project/cbor-ld-ex/) | 0.2.1 | Compact typed evidence encoding |

Pollard-Jev 0.13 is integrated through its native DecisionRequest/OpenJevProvider
interface in a separate local worker environment, preserving its Pollard 1.6.0
pin beside Canopy's 1.6.2. Its robotics policy and simulator are not imported.
Package integration tests use actual contracts and a synthetic encoder; real
local inference requires the explicitly prepared, pinned OpenJev model cache.

FastAPI, Pydantic, SQLite and the other dependencies retain their own licenses and
attribution. See `core/requirements.lock` for the reproducible environment.

## Evidence boundaries

Component tests and synthetic demonstration runs are implementation checks, not
real customer savings or certification. The human benchmark teammate owns task
quality scoring and baseline comparisons. The frontend teammate owns `web/`.
Only observed metrics should be included in a pitch. Any controlled provider
regression must be labelled synthetic, and unknown measurements stay unknown.
