# Durable recording, replay, and cache

Public integration surface (Python 3.11+, contract schema 1):

```python
from canopy.recording import ExecutionStore, canonical_json, fingerprint
from canopy.cache import ResultCache

ExecutionStore(path: str | Path = ":memory:", *, read_only: bool = False)
store.create_run(task: Task, config: RunConfig, *, run_id: str | None = None,
                 started_at: datetime | None = None) -> str
store.get_run(tenant_id: str, run_id: str) -> RecordedRun
store.list_runs(tenant_id: str, *, limit: int = 100, offset: int = 0) -> list[RecordedRun]
store.append_event(tenant_id: str, run_id: str, event_type: str, *,
                   step_id: str | None = None, payload: dict | None = None,
                   timestamp: datetime | None = None) -> RunEvent
store.events(tenant_id: str, run_id: str, *, after_sequence: int = -1,
             limit: int | None = None) -> list[RunEvent]
store.claim_step(tenant_id: str, run_id: str, step_id: str) -> bool
store.record_step(tenant_id: str, run_id: str, result: StepResult) -> StepResult
store.finish_run(result: RunResult) -> RunResult
store.verify_run(tenant_id: str, run_id: str) -> pollard.VerifyReport
store.trace(tenant_id: str, run_id: str) -> list[pollard.Node]
store.trace_links(tenant_id: str, run_id: str) -> dict[int, str]
store.replay(tenant_id: str, run_id: str, *, replay_run_id: str | None = None,
             task_id: str | None = None) -> RunResult
store.close() -> None

ResultCache(path: str | Path = ":memory:")
cache.key(task: Task, step: Step, config: RunConfig,
          dependency_outputs: Mapping[str, Any]) -> str
cache.lookup(task, step, config, dependency_outputs, *, now: datetime | None = None
             ) -> CacheHit | None
cache.put(task, step, config, dependency_outputs, result: StepResult, *,
          source_run_id: str, expires_at: datetime | None = None) -> CacheHit | None
cache.invalidate(tenant_id: str, key: str) -> bool
cache.close() -> None

canonical_json(value: Any) -> str
fingerprint(value: Any) -> str
```

Both stores support context managers. `RecordedRun` contains `run_id`, `task`,
`config`, `started_at`, optional terminal `result`, recorded `steps`,
`pollard_root_id`, and `pollard_head_id`. `CacheHit` contains `key`, `source_run_id`,
`output`, `historical_usage`, `created_at`, `expires_at`, and `output_digest`;
`hit.as_step_result(step_id)` produces a reused result with zero new provider usage.

## Integration semantics

Create a run before dispatch. Call `claim_step` immediately before each live
handler dispatch; only a `True` result authorizes this run's first dispatch of the
step. Claims verify recorded integrity before authorizing work, are unavailable
in replay mode, persist across reopen, and are never automatically released. A crash
after a claim has an unknown outcome: do not blindly retry external effects.
External services must provide their own idempotency across different runs.
Recording does not itself execute handlers. `record_step` and `finish_run` accept
identical retries idempotently and reject conflicting overwrites. `finish_run`
also records any result steps not previously recorded.

Replay is strict, storage-only inspection. It accepts no handler or registry,
verifies the persisted trace and snapshots, and refuses missing, incomplete, or
corrupt runs. It produces an ephemeral `RunResult` with `mode="replay"`, a new run
ID, `replay_source_run_id`, and per-step source provenance. New provider calls,
tokens, and monetary spend are zero; energy remains unmeasured (`None`). Historical
usage remains in `store.get_run(tenant_id, source_id).result.usage`. Replay does
not insert a new run or event. Original blocked/failed statuses remain historical
outcomes rather than being promoted to successful completion.
The known zero token usage is explicitly marked `tokens_known=True`. A runtime
may separately archive the returned replay result and its source reference.

Missing or cross-tenant runs raise `RunNotFound` (also a `LookupError`). Immutable
overwrite attempts raise `RecordingConflict`; unfinished replay raises
`IncompleteRun`; failed replay/dispatch integrity checks raise
`RecordingIntegrityError`. `verify_run` returns a real Pollard `VerifyReport`.
`canopy.*` event types are reserved for the store; runtime events use other names.
Read-only file reopening uses `ExecutionStore(path, read_only=True)` and does not
create missing trace data.

Cache reuse is allowed only for governed, cache-enabled, pure steps. Identity
binds the step and ancestor definitions, declared direct dependency outputs,
referenced evidence snapshots, schema/workflow/task metadata, actor, permissions,
policy contents, model fingerprint, prompt version, and handler versions.
`task_id`, budget limits, and usage estimates do not identify the computation.
Dependency outputs must have exactly the declared direct dependency keys. Full
evidence snapshots bind tenant, source, content, versions, and expiry; runtime
freshness and current authorization checks are still required before returning a
hit. `source_run_id` must refer to the executed source, not the new destination.
Never use a cache hit to bypass current temporal or policy checks.
Handlers eligible for reuse must observe only bound context; task IDs, run IDs,
current wall time, unrelated evidence, budgets, and other unbound context must
not influence their outputs. The runtime owns cache-safe handler registration.

The first valid cache writer wins; later concurrent writers receive the existing
output and original provenance. Explicit entry expiry is capped by the earliest
expiry among all bound evidence. `CacheValidationError` identifies incomplete
or inconsistent requests; `CacheIntegrityError` identifies damaged stored data.
A whole-entry checksum binds identity, output, historical usage, source run,
creation time, and expiry. Legacy entries without that checksum become misses
after an additive schema migration and are replaced only by newly completed
results. The cache trusts the runtime's source-run ID; it does not independently
open the recording store to prove that source execution occurred.

## Persistence and trace evidence

The execution database uses SQLite and namespaced tables. On-disk Pollard nodes
live beside it at `<path>.pollard.sqlite3`; keep both files in backups. Trace
nodes are real `pollard.Node` records persisted by `pollard.SQLiteStore`, with
verification through `pollard.verify`. In-memory use uses `pollard.MemoryStore`.
Events form a hash-linked chain whose identities bind canonical event contents;
the root binds task/config/start snapshots and the terminal event binds the final
result digest. These are evidence of recorded content, not proof that a remote
provider executed truthfully, nor protection against an attacker rewriting all
storage and its hashes. Trace-first persistence may leave unreferenced Pollard
nodes after a crash; only committed run/event references are authoritative.
SQLite transaction locking serializes competing claims and event sequences
across store instances. Both stores support concurrent threads. These are local
storage adapters, not a distributed execution or external idempotency service.

Canonical JSON is UTF-8, sorted-key JSON with compact separators. It accepts
JSON data, contract models, and aware timestamps normalized to UTC; unsupported
values, non-string object keys, naive timestamps, and non-finite numbers fail.
Fingerprints are version-prefixed SHA-256 digests. No contract changes or extra
dependencies are required.

## Focused verification

Tests use explicitly synthetic operations. They cover durable reopen, read-only
replay with dispatch/write methods disabled, zero new usage, source provenance,
tenant separation across all run APIs, duplicate and competing dispatch claims,
concurrent ordered events, immutable writes, caller mutation during persistence,
corrupted snapshots/events/claims, missing Pollard nodes/files/interned blobs,
cache identity mutations, transitive evidence bindings, expiry, concurrent cache
writers, schema migration, and shared execution/cache database use.

```powershell
.venv\Scripts\python.exe -m pytest core/tests/test_recording.py core/tests/test_cache.py
.venv\Scripts\python.exe -m ruff check core/src/canopy/recording.py core/src/canopy/cache.py core/tests/test_recording.py core/tests/test_cache.py
```

Component verification on 2026-10-17: **121 tests passed in 2.31s** (54 recording,
67 cache); Ruff passed for both modules and their tests. No shared contracts,
package metadata, frontend, or benchmark application were changed by this owner.
