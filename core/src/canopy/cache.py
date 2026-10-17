"""Durable, tenant-scoped reuse of successful, side-effect-free computations.

Cache identity is a versioned digest of execution context, not a task ID. The
runtime must still perform temporal and policy admission before serving a hit;
this module enforces declared bindings and explicit expiry, not authorization.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from canopy.contracts import Evidence, RunConfig, Step, StepResult, Task, Usage, utcnow
from canopy.recording import canonical_json, fingerprint

_KEY_VERSION = "canopy-result-cache-v1"
_ENTRY_VERSION = "canopy-cache-entry-v1"
_ENTRY_FIELDS = (
    "tenant_id",
    "cache_key",
    "identity_json",
    "output_json",
    "output_digest",
    "usage_json",
    "source_run_id",
    "created_at",
    "expires_at",
)


class CacheValidationError(ValueError):
    """A cache request has incomplete or inconsistent execution bindings."""


class CacheIntegrityError(RuntimeError):
    """A persisted cache entry is malformed or its digests no longer match."""


@dataclass(frozen=True)
class CacheHit:
    key: str
    source_run_id: str
    output: Any
    historical_usage: Usage
    created_at: datetime
    expires_at: datetime | None
    output_digest: str

    def as_step_result(self, step_id: str) -> StepResult:
        """Return reuse provenance with zero new provider spend and calls.

        Source usage remains in ``historical_usage``. Energy stays unknown: a
        cache hit does not imply that local computation consumed zero energy.
        """
        return StepResult(
            step_id=step_id,
            status="reused",
            output=copy.deepcopy(self.output),
            cache_key=self.key,
            source_run_id=self.source_run_id,
            usage=Usage(cost_usd=0.0, tokens_known=True),
        )


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CacheValidationError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _entry_digest(entry: Mapping[str, Any] | sqlite3.Row) -> str:
    return fingerprint(
        {
            "format": _ENTRY_VERSION,
            "entry": {field: entry[field] for field in _ENTRY_FIELDS},
        }
    )


def _snapshot_request(
    task: Task,
    step: Step,
    config: RunConfig,
    dependency_outputs: Mapping[str, Any],
    result: StepResult | None = None,
) -> tuple[Task, Step, RunConfig, dict[str, Any], StepResult | None]:
    """Detach request state before SQLite may wait for another writer.

    All admission decisions, identity, expiry and persisted values thereafter
    derive from this one serialization, never from caller-owned mutable models.
    """
    if not isinstance(dependency_outputs, Mapping):
        raise CacheValidationError("dependency_outputs must be a mapping")
    try:
        snapshot = json.loads(
            canonical_json(
                {
                    "task": task,
                    "step": step,
                    "config": config,
                    "dependency_outputs": dict(dependency_outputs),
                    "result": result,
                }
            )
        )
        return (
            Task.model_validate(snapshot["task"]),
            Step.model_validate(snapshot["step"]),
            RunConfig.model_validate(snapshot["config"]),
            snapshot["dependency_outputs"],
            StepResult.model_validate(snapshot["result"]) if snapshot["result"] is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise CacheValidationError(f"cache request cannot be serialized: {exc}") from exc


def _step_binding(step: Step) -> dict[str, Any]:
    # Estimates affect budget admission, not the meaning of a successful output.
    binding = step.model_dump(mode="python", exclude={"estimated_usage"})
    binding["depends_on"] = sorted(step.depends_on)
    binding["evidence_ids"] = sorted(step.evidence_ids)
    return binding


def _identity(
    task: Task,
    step: Step,
    config: RunConfig,
    dependency_outputs: Mapping[str, Any],
) -> tuple[str, str, list[Evidence]]:
    if not task.tenant_id or not task.workflow_id or not config.actor_id:
        raise CacheValidationError("tenant_id, workflow_id and actor_id must be nonempty")
    if not isinstance(dependency_outputs, Mapping):
        raise CacheValidationError("dependency_outputs must be a mapping")
    steps = {item.step_id: item for item in task.steps}
    if len(steps) != len(task.steps) or any(not item.step_id for item in task.steps):
        raise CacheValidationError("task step IDs must be nonempty and unique")
    if step.step_id not in steps or steps[step.step_id] != step:
        raise CacheValidationError("step must match its declaration in task.steps")
    if any(not isinstance(key, str) for key in dependency_outputs):
        raise CacheValidationError("dependency output IDs must be strings")
    if set(dependency_outputs) != set(step.depends_on):
        raise CacheValidationError("dependency_outputs must contain exactly the declared dependencies")

    evidence = {item.evidence_id: item for item in task.evidence}
    if len(evidence) != len(task.evidence) or any(not item.evidence_id for item in task.evidence):
        raise CacheValidationError("evidence IDs must be nonempty and unique")
    if any(item.tenant_id != task.tenant_id for item in task.evidence):
        raise CacheValidationError("all evidence must belong to the task tenant")

    visited: set[str] = set()
    visiting: set[str] = set()
    bound_evidence_ids: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise CacheValidationError("task dependencies contain a cycle")
        if step_id in visited:
            return
        if step_id not in steps:
            raise CacheValidationError(f"missing dependency step: {step_id}")
        item = steps[step_id]
        if len(set(item.depends_on)) != len(item.depends_on):
            raise CacheValidationError(f"duplicate dependency IDs on step: {step_id}")
        if len(set(item.evidence_ids)) != len(item.evidence_ids):
            raise CacheValidationError(f"duplicate evidence bindings on step: {step_id}")
        if any(evidence_id not in evidence for evidence_id in item.evidence_ids):
            raise CacheValidationError(f"missing evidence on step: {step_id}")
        bound_evidence_ids.update(item.evidence_ids)
        visiting.add(step_id)
        for dependency in item.depends_on:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    visit(step.step_id)
    bound_evidence = [evidence[evidence_id] for evidence_id in sorted(bound_evidence_ids)]
    policy = config.policy.model_copy(
        update={
            "allowed_tools": sorted(set(config.policy.allowed_tools)),
            "approval_required_tools": sorted(set(config.policy.approval_required_tools)),
        }
    )
    identity = {
        "cache_key_version": _KEY_VERSION,
        "schema_version": task.schema_version,
        "tenant_id": task.tenant_id,
        "workflow_id": task.workflow_id,
        "metadata": task.metadata,
        "step": _step_binding(step),
        "ancestors": [_step_binding(steps[item]) for item in sorted(visited - {step.step_id})],
        "dependency_outputs": dict(dependency_outputs),
        "evidence": bound_evidence,
        "actor_id": config.actor_id,
        "permissions": sorted(set(config.permissions)),
        "policy": policy,
        "model_fingerprint": config.model_fingerprint,
        "prompt_version": config.prompt_version,
    }
    try:
        identity_json = canonical_json(identity)
        key = fingerprint(json.loads(identity_json))
    except (TypeError, ValueError) as exc:
        raise CacheValidationError(f"cache identity cannot be serialized: {exc}") from exc
    return key, identity_json, bound_evidence


class ResultCache:
    """SQLite cache whose rows are isolated by tenant and identity digest.

    Multiple instances may share a local SQLite file, including ExecutionStore's
    file. Writes serialize with SQLite transactions; reads return detached JSON
    values. Instances also support concurrent threads. SQLite is not an external
    distributed cache or an authorization boundary against direct database access.
    A whole-entry checksum detects accidental metadata corruption, not an attacker
    who can rewrite the database and recompute hashes. Legacy rows without this
    checksum remain stored but are treated as misses until safely replaced.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS canopy_cache_entries (
                    tenant_id TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    output_digest TEXT NOT NULL,
                    usage_json TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    entry_digest TEXT,
                    PRIMARY KEY (tenant_id, cache_key)
                )"""
            )
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(canopy_cache_entries)")
            }
            if "entry_digest" not in columns:
                # Existing rows cannot have their provenance retroactively
                # verified. Leave their digest NULL so readers refuse reuse.
                self._connection.execute(
                    "ALTER TABLE canopy_cache_entries ADD COLUMN entry_digest TEXT"
                )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            self._connection.close()
            raise

    def __enter__(self) -> ResultCache:
        self._ensure_open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ResultCache is closed")

    @staticmethod
    def _eligible(step: Step, config: RunConfig) -> bool:
        return config.mode == "governed" and config.cache_enabled and not step.side_effect

    def key(
        self,
        task: Task,
        step: Step,
        config: RunConfig,
        dependency_outputs: Mapping[str, Any],
    ) -> str:
        """Compute identity, validating dependencies and bound evidence.

        Task IDs, budgets, estimates, run mode and cache enablement do not alter
        computational identity. Eligibility is enforced by lookup and put.
        """
        task, step, config, dependency_outputs, _ = _snapshot_request(
            task, step, config, dependency_outputs
        )
        return _identity(task, step, config, dependency_outputs)[0]

    @staticmethod
    def _decode(row: sqlite3.Row, identity_json: str) -> CacheHit:
        try:
            if row["entry_digest"] != _entry_digest(row):
                raise ValueError("whole-entry digest mismatch")
            if row["identity_json"] != identity_json:
                raise ValueError("identity does not match the requested execution")
            identity = json.loads(row["identity_json"])
            if fingerprint(identity) != row["cache_key"]:
                raise ValueError("identity digest mismatch")
            if identity["tenant_id"] != row["tenant_id"]:
                raise ValueError("identity tenant mismatch")
            output = json.loads(row["output_json"])
            if fingerprint(output) != row["output_digest"]:
                raise ValueError("output digest mismatch")
            usage = Usage.model_validate_json(row["usage_json"])
            created_at = _aware_utc(datetime.fromisoformat(row["created_at"]), "created_at")
            expires_at = (
                _aware_utc(datetime.fromisoformat(row["expires_at"]), "expires_at")
                if row["expires_at"] is not None
                else None
            )
            if not row["source_run_id"]:
                raise ValueError("source run provenance is missing")
            return CacheHit(
                key=row["cache_key"],
                source_run_id=row["source_run_id"],
                output=output,
                historical_usage=usage,
                created_at=created_at,
                expires_at=expires_at,
                output_digest=row["output_digest"],
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise CacheIntegrityError(f"invalid persisted cache entry: {exc}") from exc

    def lookup(
        self,
        task: Task,
        step: Step,
        config: RunConfig,
        dependency_outputs: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> CacheHit | None:
        """Read an eligible hit; explicit evidence expiry is a hard boundary."""
        with self._lock:
            self._ensure_open()
            if not self._eligible(step, config):
                return None
            task, step, config, dependency_outputs, _ = _snapshot_request(
                task, step, config, dependency_outputs
            )
            if not self._eligible(step, config):
                return None
            key, identity_json, evidence = _identity(task, step, config, dependency_outputs)
            current_time = _aware_utc(now if now is not None else utcnow(), "now")
            if any(item.expires_at and item.expires_at <= current_time for item in evidence):
                return None
            row = self._connection.execute(
                "SELECT * FROM canopy_cache_entries WHERE tenant_id = ? AND cache_key = ?",
                (task.tenant_id, key),
            ).fetchone()
            if row is None or row["entry_digest"] is None:
                return None
            hit = self._decode(row, identity_json)
            if hit.expires_at is not None and hit.expires_at <= current_time:
                return None
            return hit

    def put(
        self,
        task: Task,
        step: Step,
        config: RunConfig,
        dependency_outputs: Mapping[str, Any],
        result: StepResult,
        *,
        source_run_id: str,
        expires_at: datetime | None = None,
    ) -> CacheHit | None:
        """Persist a completed computation, preserving the first valid source.

        A concurrent writer that loses the insertion race receives the stored
        winner. Expired rows can be replaced. Reused and unsuccessful results are
        never written, and explicit expiry is capped by all bound evidence.
        """
        with self._lock:
            self._ensure_open()
            if not self._eligible(step, config) or result.status != "completed":
                return None
            task, step, config, dependency_outputs, snapshot_result = _snapshot_request(
                task, step, config, dependency_outputs, result
            )
            assert snapshot_result is not None
            result = snapshot_result
            if not self._eligible(step, config) or result.status != "completed":
                return None
            if result.step_id != step.step_id:
                raise CacheValidationError("result.step_id must match step.step_id")
            if not isinstance(source_run_id, str) or not source_run_id:
                raise CacheValidationError("source_run_id must be a nonempty string")
            if result.source_run_id is not None and result.source_run_id != source_run_id:
                raise CacheValidationError("result source_run_id conflicts with source provenance")
            key, identity_json, evidence = _identity(task, step, config, dependency_outputs)
            if result.cache_key is not None and result.cache_key != key:
                raise CacheValidationError("result.cache_key does not match execution identity")
            current_time = utcnow()
            expiries = [item.expires_at for item in evidence if item.expires_at is not None]
            if expires_at is not None:
                expiries.append(_aware_utc(expires_at, "expires_at"))
            effective_expiry = min(expiries) if expiries else None
            if effective_expiry is not None and effective_expiry <= current_time:
                return None
            try:
                output_json = canonical_json(result.output)
                output_digest = fingerprint(json.loads(output_json))
                usage_json = canonical_json(result.usage)
            except (TypeError, ValueError) as exc:
                raise CacheValidationError(f"cache result cannot be serialized: {exc}") from exc
            entry = {
                "tenant_id": task.tenant_id,
                "cache_key": key,
                "identity_json": identity_json,
                "output_json": output_json,
                "output_digest": output_digest,
                "usage_json": usage_json,
                "source_run_id": source_run_id,
                "created_at": current_time.isoformat(),
                "expires_at": effective_expiry.isoformat() if effective_expiry is not None else None,
            }
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT * FROM canopy_cache_entries WHERE tenant_id = ? AND cache_key = ?",
                    (task.tenant_id, key),
                ).fetchone()
                if row is not None and row["entry_digest"] is not None:
                    hit = self._decode(row, identity_json)
                    if hit.expires_at is None or hit.expires_at > current_time:
                        self._connection.commit()
                        return hit
                self._connection.execute(
                    """INSERT INTO canopy_cache_entries
                       (tenant_id, cache_key, identity_json, output_json, output_digest,
                        usage_json, source_run_id, created_at, expires_at, entry_digest)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (tenant_id, cache_key) DO UPDATE SET
                         identity_json = excluded.identity_json,
                         output_json = excluded.output_json,
                         output_digest = excluded.output_digest,
                         usage_json = excluded.usage_json,
                         source_run_id = excluded.source_run_id,
                         created_at = excluded.created_at,
                         expires_at = excluded.expires_at,
                         entry_digest = excluded.entry_digest""",
                    (*[entry[field] for field in _ENTRY_FIELDS], _entry_digest(entry)),
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            return CacheHit(
                key=key,
                source_run_id=source_run_id,
                output=json.loads(output_json),
                historical_usage=Usage.model_validate_json(usage_json),
                created_at=current_time,
                expires_at=effective_expiry,
                output_digest=output_digest,
            )

    def invalidate(self, tenant_id: str, key: str) -> bool:
        """Delete only the specified tenant's entry and return whether it existed."""
        if not tenant_id or not key:
            raise CacheValidationError("tenant_id and key must be nonempty")
        with self._lock:
            self._ensure_open()
            with self._connection:
                cursor = self._connection.execute(
                    "DELETE FROM canopy_cache_entries WHERE tenant_id = ? AND cache_key = ?",
                    (tenant_id, key),
                )
            return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True
