"""Durable run snapshots and strict, dispatch-free replay with Pollard evidence."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from pollard import (
    MemoryStore, Node, NodeKind, PollardError, SQLiteStore, VerifyFinding, VerifyReport, verify,
)
from pydantic import BaseModel

from canopy.contracts import RunConfig, RunEvent, RunResult, Step, StepResult, Task, Usage, new_id, utcnow

_FORMAT = "canopy-json-v1"


class RecordingError(RuntimeError):
    """Base class for recording failures."""


class RunNotFound(RecordingError, LookupError):
    """The requested run is absent from the caller's tenant scope."""


class RecordingConflict(RecordingError):
    """An immutable recording already exists with different content."""


class IncompleteRun(RecordingError):
    """A run has no terminal result and cannot be replayed."""


class RecordingIntegrityError(RecordingError):
    """Stored snapshots or Pollard evidence failed verification."""


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        # Pydantic defaults need not be validated (e.g. float fields default to
        # integer 0). Normalize fields before hashing so a JSON reload is stable.
        validated = type(value).model_validate(value.model_dump(mode="python"))
        return _json_value(validated.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers cannot be recorded")
        return value
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported recording value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize strict JSON data/models; normalize aware datetimes to UTC."""
    return json.dumps(
        _json_value(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value: Any) -> str:
    """Version-prefixed SHA-256 over canonical UTF-8; never use Python hash()."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{_FORMAT}:{digest}"


@dataclass(frozen=True)
class RecordedRun:
    run_id: str
    task: Task
    config: RunConfig
    started_at: datetime
    result: RunResult | None
    steps: list[StepResult]
    pollard_root_id: str
    pollard_head_id: str


class ExecutionStore:
    """SQLite application records with a separate, real Pollard trace store.

    All public reads require tenant scope. A durable claim is a fail-closed gate
    for dispatch within a run; the runtime must honor it. This class never calls
    a handler. Claims cannot guarantee idempotency at an external service.
    """

    def __init__(self, path: str | Path = ":memory:", *, read_only: bool = False) -> None:
        self.path = str(path)
        self.read_only = read_only
        self.trace_path = None if self.path == ":memory:" else Path(f"{self.path}.pollard.sqlite3")
        self._lock = RLock()
        self._memory_trace = MemoryStore() if self.trace_path is None else None
        self._closed = False
        if read_only:
            if self.trace_path is None:
                raise ValueError("read_only requires an existing on-disk recording")
            uri = f"{Path(self.path).resolve().as_uri()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.execute("PRAGMA query_only=ON")
        else:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        try:
            if not read_only:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS canopy_recording_meta (
                        key TEXT PRIMARY KEY, value TEXT NOT NULL
                    );
                    INSERT OR IGNORE INTO canopy_recording_meta VALUES ('schema', '1');
                    CREATE TABLE IF NOT EXISTS canopy_runs (
                        tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
                        task_json TEXT NOT NULL, config_json TEXT NOT NULL,
                        started_at TEXT NOT NULL, result_json TEXT,
                        root_id TEXT NOT NULL, head_id TEXT NOT NULL,
                        PRIMARY KEY (tenant_id, run_id)
                    );
                    CREATE TABLE IF NOT EXISTS canopy_events (
                        tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL, event_json TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        PRIMARY KEY (tenant_id, run_id, sequence),
                        FOREIGN KEY (tenant_id, run_id)
                            REFERENCES canopy_runs(tenant_id, run_id)
                    );
                    CREATE TABLE IF NOT EXISTS canopy_steps (
                        tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
                        step_id TEXT NOT NULL, result_json TEXT NOT NULL,
                        PRIMARY KEY (tenant_id, run_id, step_id),
                        FOREIGN KEY (tenant_id, run_id)
                            REFERENCES canopy_runs(tenant_id, run_id)
                    );
                    CREATE TABLE IF NOT EXISTS canopy_step_claims (
                        tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
                        step_id TEXT NOT NULL,
                        PRIMARY KEY (tenant_id, run_id, step_id),
                        FOREIGN KEY (tenant_id, run_id)
                            REFERENCES canopy_runs(tenant_id, run_id)
                    );
                    """
                )
            schema = self._conn.execute(
                "SELECT value FROM canopy_recording_meta WHERE key='schema'"
            ).fetchone()
            if schema is None or schema[0] != "1":
                raise RecordingIntegrityError("unsupported recording schema")
            with self._trace_store():
                pass
        except BaseException:
            self._conn.close()
            raise

    def __enter__(self) -> ExecutionStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    @contextmanager
    def _trace_store(self, *, reading: bool = False) -> Iterator[MemoryStore | SQLiteStore]:
        if self._memory_trace is not None:
            yield self._memory_trace
        else:
            with SQLiteStore(self.trace_path, read_only=self.read_only or reading) as trace:
                yield trace

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[None]:
        with self._lock:
            if write and self.read_only:
                raise PermissionError("recording store is read-only")
            self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def _row(self, tenant_id: str, run_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM canopy_runs WHERE tenant_id=? AND run_id=?", (tenant_id, run_id)
        ).fetchone()
        if row is None:
            raise RunNotFound("run not found in tenant scope")
        return row

    def _snapshot(self, row: sqlite3.Row) -> RecordedRun:
        task = Task.model_validate_json(row["task_json"])
        steps = {
            item["step_id"]: StepResult.model_validate_json(item["result_json"])
            for item in self._conn.execute(
                "SELECT step_id, result_json FROM canopy_steps WHERE tenant_id=? AND run_id=?",
                (row["tenant_id"], row["run_id"]),
            )
        }
        return RecordedRun(
            run_id=row["run_id"], task=task,
            config=RunConfig.model_validate_json(row["config_json"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            result=None if row["result_json"] is None else RunResult.model_validate_json(
                row["result_json"]
            ),
            steps=[steps[s.step_id] for s in task.steps if s.step_id in steps],
            pollard_root_id=row["root_id"], pollard_head_id=row["head_id"],
        )

    @staticmethod
    def _root(run_id: str, task: Task, config: RunConfig, started_at: datetime) -> Node:
        return Node.make(kind=NodeKind.ROOT, parent=None, payload={
            "format": _FORMAT, "run": run_id, "tenant_id": task.tenant_id,
            "snapshot": canonical_json({"task": task, "config": config, "started_at": started_at}),
        })

    @staticmethod
    def _validate_task(task: Task) -> None:
        if not task.tenant_id or not task.task_id:
            raise ValueError("tenant_id and task_id must not be empty")
        step_ids = [step.step_id for step in task.steps]
        if len(set(step_ids)) != len(step_ids) or any(not value for value in step_ids):
            raise ValueError("step IDs must be nonempty and unique")
        evidence_ids = [e.evidence_id for e in task.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence IDs must be unique")
        if any(e.tenant_id != task.tenant_id for e in task.evidence):
            raise ValueError("evidence tenant mismatch")
        for step in task.steps:
            if not set(step.depends_on) <= set(step_ids):
                raise ValueError("unknown step dependency")
            if not set(step.evidence_ids) <= set(evidence_ids):
                raise ValueError("unknown evidence reference")

    def create_run(
        self, task: Task, config: RunConfig, *, run_id: str | None = None,
        started_at: datetime | None = None,
    ) -> str:
        task_json, config_json = canonical_json(task), canonical_json(config)
        task = Task.model_validate_json(task_json)
        config = RunConfig.model_validate_json(config_json)
        self._validate_task(task)
        run_id = new_id() if run_id is None else run_id
        if not run_id:
            raise ValueError("run_id must not be empty")
        with self._transaction(write=True):
            existing = self._conn.execute(
                "SELECT * FROM canopy_runs WHERE tenant_id=? AND run_id=?", (task.tenant_id, run_id)
            ).fetchone()
            if existing is not None:
                same_time = started_at is None or canonical_json(started_at) == canonical_json(
                    datetime.fromisoformat(existing["started_at"])
                )
                if (existing["task_json"], existing["config_json"]) != (task_json, config_json) or (
                    not same_time
                ):
                    raise RecordingConflict("run ID already binds a different snapshot")
                return run_id
            started_at = utcnow() if started_at is None else started_at
            root = self._root(run_id, task, config, started_at)
            with self._trace_store() as trace:
                trace.put(root)
            self._conn.execute(
                "INSERT INTO canopy_runs VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (task.tenant_id, run_id, task_json, config_json, _json_value(started_at),
                 root.id, root.id),
            )
        return run_id

    def get_run(self, tenant_id: str, run_id: str) -> RecordedRun:
        with self._transaction():
            return self._snapshot(self._row(tenant_id, run_id))

    def list_runs(self, tenant_id: str, *, limit: int = 100, offset: int = 0) -> list[RecordedRun]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset nonnegative")
        with self._transaction():
            rows = self._conn.execute(
                "SELECT * FROM canopy_runs WHERE tenant_id=? "
                "ORDER BY started_at DESC, run_id LIMIT ? OFFSET ?", (tenant_id, limit, offset)
            ).fetchall()
            return [self._snapshot(row) for row in rows]

    def _append(
        self, tenant_id: str, run_id: str, event_type: str, *, step_id: str | None = None,
        payload: dict[str, Any] | None = None, timestamp: datetime | None = None,
    ) -> RunEvent:
        row = self._row(tenant_id, run_id)
        if row["result_json"] is not None:
            raise RecordingConflict("a terminal run cannot accept new events")
        if step_id is not None:
            self._require_step(row, step_id)
        next_sequence = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), -1) + 1 FROM canopy_events "
            "WHERE tenant_id=? AND run_id=?", (tenant_id, run_id)
        ).fetchone()[0]
        event = RunEvent(run_id=run_id, sequence=next_sequence, event_type=event_type,
                         step_id=step_id, payload={} if payload is None else payload,
                         timestamp=utcnow() if timestamp is None else timestamp)
        event_json = canonical_json(event)
        node = Node.make(kind=NodeKind.NOTE, parent=row["head_id"], payload={
            "format": _FORMAT, "event": event_json,
        })
        with self._trace_store() as trace:
            trace.put(node)
        self._conn.execute("INSERT INTO canopy_events VALUES (?, ?, ?, ?, ?)",
                           (tenant_id, run_id, next_sequence, event_json, node.id))
        self._conn.execute(
            "UPDATE canopy_runs SET head_id=? WHERE tenant_id=? AND run_id=?",
            (node.id, tenant_id, run_id),
        )
        return RunEvent.model_validate_json(event_json)

    def append_event(
        self, tenant_id: str, run_id: str, event_type: str, *, step_id: str | None = None,
        payload: dict[str, Any] | None = None, timestamp: datetime | None = None,
    ) -> RunEvent:
        if not event_type or event_type.startswith("canopy."):
            raise ValueError("event type must be nonempty and must not use the canopy. prefix")
        with self._transaction(write=True):
            return self._append(tenant_id, run_id, event_type, step_id=step_id,
                                payload=payload, timestamp=timestamp)

    def events(
        self, tenant_id: str, run_id: str, *, after_sequence: int = -1, limit: int | None = None,
    ) -> list[RunEvent]:
        if after_sequence < -1 or (limit is not None and limit < 1):
            raise ValueError("invalid event cursor or limit")
        with self._transaction():
            self._row(tenant_id, run_id)
            rows = self._conn.execute(
                "SELECT event_json FROM canopy_events WHERE tenant_id=? AND run_id=? "
                "AND sequence>? ORDER BY sequence LIMIT ?",
                (tenant_id, run_id, after_sequence, -1 if limit is None else limit),
            )
            return [RunEvent.model_validate_json(row[0]) for row in rows]

    @staticmethod
    def _require_step(row: sqlite3.Row, step_id: str) -> Step:
        task = Task.model_validate_json(row["task_json"])
        for step in task.steps:
            if step.step_id == step_id:
                return step
        raise ValueError("step is not declared in the recorded task")

    def claim_step(self, tenant_id: str, run_id: str, step_id: str) -> bool:
        """Persist the only dispatch claim; False means do not invoke a handler."""
        with self._transaction(write=True):
            row = self._row(tenant_id, run_id)
            self._require_step(row, step_id)
            if RunConfig.model_validate_json(row["config_json"]).mode == "replay":
                return False
            recorded = self._conn.execute(
                "SELECT 1 FROM canopy_steps WHERE tenant_id=? AND run_id=? AND step_id=?",
                (tenant_id, run_id, step_id),
            ).fetchone()
            if row["result_json"] is not None or recorded is not None:
                return False
            report = self._verify_with_trace(row)
            if not report.ok:
                raise RecordingIntegrityError("; ".join(item.message for item in report.findings))
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO canopy_step_claims VALUES (?, ?, ?)",
                (tenant_id, run_id, step_id),
            )
            if not cursor.rowcount:
                return False
            self._append(tenant_id, run_id, "canopy.step.claimed", step_id=step_id)
            return True

    def _record_step(self, tenant_id: str, run_id: str, result: StepResult) -> StepResult:
        result_json = canonical_json(result)
        result = StepResult.model_validate_json(result_json)
        row = self._row(tenant_id, run_id)
        step = self._require_step(row, result.step_id)
        if result.status == "reused":
            if step.side_effect:
                raise ValueError("side-effect steps cannot be reusable computations")
            if not result.source_run_id:
                raise ValueError("reused results require source_run_id provenance")
        existing = self._conn.execute(
            "SELECT result_json FROM canopy_steps WHERE tenant_id=? AND run_id=? AND step_id=?",
            (tenant_id, run_id, result.step_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != result_json:
                raise RecordingConflict("step result is already recorded with different content")
            return StepResult.model_validate_json(existing[0])
        self._append(tenant_id, run_id, "canopy.step.recorded", step_id=result.step_id,
                     payload={"result": json.loads(result_json)})
        self._conn.execute("INSERT INTO canopy_steps VALUES (?, ?, ?, ?)",
                           (tenant_id, run_id, result.step_id, result_json))
        return StepResult.model_validate_json(result_json)

    def record_step(self, tenant_id: str, run_id: str, result: StepResult) -> StepResult:
        result = StepResult.model_validate_json(canonical_json(result))
        with self._transaction(write=True):
            return self._record_step(tenant_id, run_id, result)

    def finish_run(self, result: RunResult) -> RunResult:
        result_json = canonical_json(result)
        result = RunResult.model_validate_json(result_json)
        with self._transaction(write=True):
            row = self._row(result.tenant_id, result.run_id)
            if row["result_json"] is not None:
                if row["result_json"] != result_json:
                    raise RecordingConflict("run has a different terminal result")
                return RunResult.model_validate_json(row["result_json"])
            saved = self._snapshot(row)
            if (result.task_id, result.mode, result.policy_version, result.started_at) != (
                saved.task.task_id, saved.config.mode, saved.config.policy.version, saved.started_at
            ):
                raise ValueError("result does not match its run snapshot")
            if result.finished_at < result.started_at:
                raise ValueError("run finish precedes its start")
            ids = [step.step_id for step in result.steps]
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate step results")
            if not {step.step_id for step in saved.steps} <= set(ids):
                raise ValueError("terminal result omits previously recorded steps")
            if result.status == "completed" and (
                set(ids) != {step.step_id for step in saved.task.steps}
                or any(step.status not in {"completed", "reused"} for step in result.steps)
            ):
                raise ValueError("completed runs require all steps to have succeeded")
            for step in result.steps:
                self._record_step(result.tenant_id, result.run_id, step)
            self._append(result.tenant_id, result.run_id, "canopy.run.finished",
                         payload={"result_digest": fingerprint(result)})
            self._conn.execute(
                "UPDATE canopy_runs SET result_json=? WHERE tenant_id=? AND run_id=?",
                (result_json, result.tenant_id, result.run_id),
            )
        return RunResult.model_validate_json(result_json)

    def _verify(self, row: sqlite3.Row, trace: MemoryStore | SQLiteStore) -> VerifyReport:
        findings: list[VerifyFinding] = []

        def problem(node_id: str, message: str) -> None:
            findings.append(VerifyFinding(node_id, message))

        try:
            saved = self._snapshot(row)
            if saved.task.tenant_id != row["tenant_id"]:
                problem(row["root_id"], "snapshot tenant mismatch")
            root = self._root(saved.run_id, saved.task, saved.config, saved.started_at)
            if root.id != row["root_id"]:
                problem(row["root_id"], "root does not bind the persisted run snapshot")
            previous = root.id
            recorded_steps: dict[str, StepResult] = {}
            claimed_steps: set[str] = set()
            terminal = None
            rows = self._conn.execute(
                "SELECT * FROM canopy_events WHERE tenant_id=? AND run_id=? ORDER BY sequence",
                (row["tenant_id"], row["run_id"]),
            ).fetchall()
            for sequence, item in enumerate(rows):
                event = RunEvent.model_validate_json(item["event_json"])
                if event.sequence != sequence or item["sequence"] != sequence:
                    problem(item["trace_id"], "event sequence is not contiguous")
                if event.run_id != saved.run_id:
                    problem(item["trace_id"], "event run ID mismatch")
                expected = Node.make(kind=NodeKind.NOTE, parent=previous, payload={
                    "format": _FORMAT, "event": canonical_json(event),
                })
                if item["trace_id"] != expected.id:
                    problem(item["trace_id"], "event is not bound to its Pollard trace node")
                actual = trace.get(item["trace_id"])
                if actual.identity_tuple() != expected.identity_tuple():
                    problem(item["trace_id"], "Pollard event content or ancestry mismatch")
                previous = item["trace_id"]
                if event.event_type == "canopy.step.recorded":
                    step = StepResult.model_validate(event.payload["result"])
                    if step.step_id != event.step_id or step.step_id in recorded_steps:
                        problem(previous, "conflicting step recording event")
                    recorded_steps[step.step_id] = step
                elif event.event_type == "canopy.step.claimed":
                    if event.step_id is None or event.step_id in claimed_steps:
                        problem(previous, "conflicting dispatch claim event")
                    claimed_steps.add(event.step_id)
                elif event.event_type == "canopy.run.finished":
                    if terminal is not None or sequence != len(rows) - 1:
                        problem(previous, "terminal event must be unique and last")
                    terminal = event
            if previous != row["head_id"]:
                problem(row["head_id"], "trace head differs from committed event history")
            step_rows = self._conn.execute(
                "SELECT step_id, result_json FROM canopy_steps WHERE tenant_id=? AND run_id=?",
                (row["tenant_id"], row["run_id"]),
            ).fetchall()
            step_data = {item["step_id"]: json.loads(item["result_json"]) for item in step_rows}
            if canonical_json(recorded_steps) != canonical_json(step_data):
                problem(previous, "step snapshots do not match recorded events")
            claims = {item[0] for item in self._conn.execute(
                "SELECT step_id FROM canopy_step_claims WHERE tenant_id=? AND run_id=?",
                (row["tenant_id"], row["run_id"]),
            )}
            if claims != claimed_steps:
                problem(previous, "dispatch claims do not match recorded events")
            if saved.result is not None:
                if terminal is None or terminal.payload.get("result_digest") != fingerprint(
                    saved.result
                ):
                    problem(previous, "terminal result is not bound to its recorded digest")
                if canonical_json({step.step_id: step for step in saved.result.steps}) != (
                    canonical_json(recorded_steps)
                ):
                    problem(previous, "terminal steps do not match recorded step results")
            elif terminal is not None:
                problem(previous, "terminal event has no persisted result")
            findings.extend(verify(trace, row["head_id"]).findings)
        except (KeyError, ValueError, TypeError, sqlite3.DatabaseError, PollardError) as exc:
            problem(row["head_id"], f"unreadable recording: {exc}")
        return VerifyReport(ok=not findings, findings=findings)

    def _verify_with_trace(self, row: sqlite3.Row) -> VerifyReport:
        try:
            with self._trace_store(reading=True) as trace:
                return self._verify(row, trace)
        except (OSError, ValueError, sqlite3.DatabaseError, PollardError) as exc:
            return VerifyReport(ok=False, findings=[
                VerifyFinding(row["head_id"], f"unreadable Pollard trace: {exc}")
            ])

    def verify_run(self, tenant_id: str, run_id: str) -> VerifyReport:
        with self._transaction():
            row = self._row(tenant_id, run_id)
            return self._verify_with_trace(row)

    def trace_links(self, tenant_id: str, run_id: str) -> dict[int, str]:
        with self._transaction():
            self._row(tenant_id, run_id)
            return dict(self._conn.execute(
                "SELECT sequence, trace_id FROM canopy_events WHERE tenant_id=? AND run_id=? "
                "ORDER BY sequence", (tenant_id, run_id)
            ).fetchall())

    def trace(self, tenant_id: str, run_id: str) -> list[Node]:
        """Return only committed trace nodes, never unreferenced crash leftovers."""
        with self._transaction():
            row = self._row(tenant_id, run_id)
            ids = [row["root_id"]] + [item[0] for item in self._conn.execute(
                "SELECT trace_id FROM canopy_events WHERE tenant_id=? AND run_id=? ORDER BY sequence",
                (tenant_id, run_id),
            )]
            with self._trace_store(reading=True) as trace:
                return [trace.get(node_id) for node_id in ids]

    def replay(
        self, tenant_id: str, run_id: str, *, replay_run_id: str | None = None,
        task_id: str | None = None,
    ) -> RunResult:
        """Verify and return history with zero new provider spend; no dispatch/write."""
        if replay_run_id is not None and (not replay_run_id or replay_run_id == run_id):
            raise ValueError("replay_run_id must be nonempty and distinct from the source")
        if task_id is not None and not task_id:
            raise ValueError("task_id must not be empty")
        with self._transaction():
            row = self._row(tenant_id, run_id)
            report = self._verify_with_trace(row)
            if not report.ok:
                raise RecordingIntegrityError("; ".join(item.message for item in report.findings))
            saved = self._snapshot(row)
            if saved.result is None:
                raise IncompleteRun("run has no terminal recording; strict replay cannot execute it")
            source = saved.result
            zero = Usage(cost_usd=0, tokens_known=True)
            steps = [step.model_copy(deep=True, update={
                "usage": zero.model_copy(), "source_run_id": step.source_run_id or source.run_id,
            }) for step in source.steps]
            instant = utcnow()
            return source.model_copy(deep=True, update={
                "run_id": new_id() if replay_run_id is None else replay_run_id,
                "task_id": source.task_id if task_id is None else task_id,
                "mode": "replay", "replay_source_run_id": source.run_id,
                "started_at": instant, "finished_at": instant,
                "steps": steps, "usage": zero,
            })
