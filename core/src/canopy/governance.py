"""Deterministic enterprise policy and durable, exact-action human approvals.

Identity objects are application trust boundaries, not authentication mechanisms.
The host must derive execution permissions from authenticated server context and
configure a reviewer authorizer that verifies its own session/token context.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pollard.policy import Decision as PollardDecision

from canopy.contracts import Evidence, Policy, Step


def _identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _timestamp(value: datetime | None = None) -> datetime:
    value = datetime.now(UTC) if value is None else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _json_value(value: Any, *, allow_tuples: bool = False) -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be strings")
        return {key: _json_value(item, allow_tuples=allow_tuples) for key, item in value.items()}
    if type(value) is list or (allow_tuples and type(value) is tuple):
        return [_json_value(item, allow_tuples=allow_tuples) for item in value]
    raise ValueError("action arguments must contain only finite JSON values")


def _canonical(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ExecutionContext:
    """Permissions already verified by the host, never taken from agent labels."""

    tenant_id: str
    actor_id: str
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        _identifier(self.tenant_id, "tenant_id")
        _identifier(self.actor_id, "actor_id")
        if isinstance(self.permissions, str):
            raise ValueError("permissions must be a collection, not a role label")
        object.__setattr__(self, "permissions", frozenset(self.permissions))


@dataclass(frozen=True)
class ReviewerIdentity:
    """Identity returned by the configured application reviewer authorizer."""

    tenant_id: str
    reviewer_id: str
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        _identifier(self.tenant_id, "tenant_id")
        _identifier(self.reviewer_id, "reviewer_id")
        if isinstance(self.permissions, str):
            raise ValueError("permissions must be a collection")
        object.__setattr__(self, "permissions", frozenset(self.permissions))


@dataclass(frozen=True)
class ApprovalBinding:
    """Immutable canonical action snapshot; digest includes every stored field."""

    canonical_json: str

    def __post_init__(self) -> None:
        payload = json.loads(self.canonical_json)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("unsupported approval binding")
        for field in ("tenant_id", "actor_id", "policy_version"):
            _identifier(payload.get(field), field)
        if _canonical(payload) != self.canonical_json:
            raise ValueError("approval binding must use canonical JSON")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    @property
    def tenant_id(self) -> str:
        return json.loads(self.canonical_json)["tenant_id"]

    @property
    def actor_id(self) -> str:
        return json.loads(self.canonical_json)["actor_id"]

    @property
    def policy_version(self) -> str:
        return json.loads(self.canonical_json)["policy_version"]

    def to_dict(self) -> dict[str, Any]:
        """Return a detached reviewable snapshot, not mutable binding state."""
        return json.loads(self.canonical_json)


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    binding: ApprovalBinding
    status: Literal["pending", "approved", "denied"]
    requested_at: datetime
    expires_at: datetime
    reviewer_id: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class PolicyDecision:
    status: Literal["allow", "deny", "approval_required"]
    reason: str
    binding: ApprovalBinding | None = None
    approval_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status == "allow"

    @property
    def pollard_decision(self) -> PollardDecision:
        """Actual Pollard policy enum for adapters using Pollard policy hooks."""
        return {
            "allow": PollardDecision.ALLOW,
            "deny": PollardDecision.DENY,
            "approval_required": PollardDecision.CONFIRM,
        }[self.status]


class ApprovalStore:
    """SQLite approval ledger with immutable decisions and atomic single use.

    An absent authorizer fails closed. Its argument is opaque to this store: the
    application decides how to verify a session, signed token, or request context.
    Database ownership is a trusted local boundary; this is not a signed ledger.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        reviewer_authorizer: Callable[[Any], ReviewerIdentity | None] | None = None,
    ) -> None:
        self._authorizer = reviewer_authorizer
        self._lock = RLock()
        self._db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 30000")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS canopy_approvals (
                approval_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                binding_digest TEXT NOT NULL,
                binding_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','approved','denied')),
                requested_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                reviewer_id TEXT,
                decided_at TEXT,
                reason TEXT,
                consumed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS canopy_approval_binding
                ON canopy_approvals (tenant_id, binding_digest);
        """)

    @staticmethod
    def _record(row: sqlite3.Row) -> ApprovalRecord:
        binding = ApprovalBinding(row["binding_json"])
        if binding.digest != row["binding_digest"] or binding.tenant_id != row["tenant_id"]:
            raise ValueError("approval storage binding integrity check failed")
        return ApprovalRecord(
            approval_id=row["approval_id"], binding=binding, status=row["status"],
            requested_at=datetime.fromisoformat(row["requested_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            reviewer_id=row["reviewer_id"],
            decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
            reason=row["reason"],
            consumed_at=datetime.fromisoformat(row["consumed_at"]) if row["consumed_at"] else None,
        )

    def request(
        self, binding: ApprovalBinding, *, expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        now = _timestamp(now)
        expiry = _timestamp(expires_at) if expires_at else now + timedelta(hours=1)
        if expiry <= now:
            raise ValueError("approval expiry must be in the future")
        with self._lock, self._db:
            # Serialize lookup + insert across independent store connections.
            self._db.execute("BEGIN IMMEDIATE")
            rows = self._db.execute(
                "SELECT * FROM canopy_approvals WHERE tenant_id=? AND binding_digest=? "
                "AND status IN ('pending', 'approved') AND consumed_at IS NULL "
                "ORDER BY requested_at DESC",
                (binding.tenant_id, binding.digest),
            ).fetchall()
            for row in rows:
                record = self._record(row)
                if record.binding == binding and record.expires_at > now:
                    return record
            approval_id = str(uuid4())
            self._db.execute(
                "INSERT INTO canopy_approvals (approval_id,tenant_id,binding_digest,binding_json,"
                "status,requested_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                (approval_id, binding.tenant_id, binding.digest, binding.canonical_json,
                 "pending", now.isoformat(), expiry.isoformat()),
            )
            return self._record(self._db.execute(
                "SELECT * FROM canopy_approvals WHERE approval_id=?", (approval_id,),
            ).fetchone())

    def get(self, approval_id: str, *, tenant_id: str) -> ApprovalRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM canopy_approvals WHERE approval_id=? AND tenant_id=?",
                (approval_id, tenant_id),
            ).fetchone()
        return self._record(row) if row is not None else None

    def decide(
        self, approval_id: str, *, reviewer_context: Any, approved: bool,
        reason: str | None = None, now: datetime | None = None,
    ) -> ApprovalRecord:
        if type(approved) is not bool:
            raise TypeError("approved must be a boolean")
        if reason is not None and not isinstance(reason, str):
            raise TypeError("reason must be text")
        identity = self._authorizer(reviewer_context) if self._authorizer else None
        if not isinstance(identity, ReviewerIdentity) or not (
            {"approvals:decide", "*"} & identity.permissions
        ):
            raise PermissionError("trusted reviewer authorization is required")
        now = _timestamp(now)
        status = "approved" if approved else "denied"
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            record = self.get(approval_id, tenant_id=identity.tenant_id)
            if record is None:
                raise PermissionError("approval is not available to this reviewer tenant")
            if record.expires_at <= now or record.consumed_at is not None:
                raise ValueError("approval is expired or consumed")
            if record.status != "pending":
                if (record.status, record.reviewer_id, record.reason) == (
                    status, identity.reviewer_id, reason,
                ):
                    return record
                raise ValueError("approval decision is immutable")
            self._db.execute(
                "UPDATE canopy_approvals SET status=?,reviewer_id=?,decided_at=?,reason=? "
                "WHERE approval_id=?",
                (status, identity.reviewer_id, now.isoformat(), reason, approval_id),
            )
            return self.get(approval_id, tenant_id=identity.tenant_id)  # type: ignore[return-value]

    def consume(
        self, approval_id: str, binding: ApprovalBinding, *, now: datetime | None = None,
    ) -> ApprovalRecord:
        """Atomically claim once; failure/unknown external outcomes never unclaim."""
        now = _timestamp(now)
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            record = self.get(approval_id, tenant_id=binding.tenant_id)
            if record is None or record.binding != binding:
                raise PermissionError("approval does not match the exact action")
            if (record.status != "approved" or record.consumed_at is not None
                    or record.expires_at <= now):
                raise PermissionError("approval is not available for dispatch")
            self._db.execute(
                "UPDATE canopy_approvals SET consumed_at=? WHERE approval_id=?",
                (now.isoformat(), approval_id),
            )
            return self.get(approval_id, tenant_id=binding.tenant_id)  # type: ignore[return-value]

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _amount_violation(value: Any, maximum: int | None) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "amount_minor":
                if type(item) is not int or item < 0:
                    return "amount_minor must be a nonnegative integer"
                if maximum is not None and item > maximum:
                    return "amount_minor exceeds policy maximum"
            violation = _amount_violation(item, maximum)
            if violation:
                return violation
    elif isinstance(value, list):
        for item in value:
            violation = _amount_violation(item, maximum)
            if violation:
                return violation
    return None


class PolicyGate:
    """Pure policy checks plus read-only approval lookup; never dispatches tools."""

    def __init__(self, approval_store: ApprovalStore | None = None) -> None:
        self.approval_store = approval_store

    def evaluate(
        self, step: Step, *, context: ExecutionContext, policy: Policy,
        evidence: Sequence[Evidence] = (),
        dependency_outputs: Mapping[str, Any] | None = None,
        approval_id: str | None = None, drift: Any = None,
        now: datetime | None = None,
    ) -> PolicyDecision:
        now = _timestamp(now)
        if not isinstance(context, ExecutionContext):
            return PolicyDecision("deny", "trusted execution context is required")
        if not ({"*", "execute:*", f"execute:{step.handler}"} & context.permissions):
            return PolicyDecision("deny", "actor lacks handler execution permission")
        if step.kind == "tool" and step.handler not in policy.allowed_tools:
            return PolicyDecision("deny", "tool is not allowed by current policy")
        try:
            _identifier(policy.version, "policy version")
            _identifier(step.handler, "handler")
            _identifier(step.handler_version, "handler version")
            evidence_by_id: dict[str, Evidence] = {}
            for item in evidence:
                # JSON-mode Pydantic dumps coerce tuples/sets/non-string keys and
                # NaN; validate original data before those lossy conversions.
                _json_value(item.data)
                if item.tenant_id != context.tenant_id:
                    return PolicyDecision("deny", "evidence tenant does not match execution tenant")
                if item.evidence_id in evidence_by_id:
                    return PolicyDecision("deny", "duplicate evidence identifier")
                if item.expires_at is not None and item.expires_at <= now:
                    return PolicyDecision("deny", "evidence has expired")
                evidence_by_id[item.evidence_id] = item
            if set(step.evidence_ids) - evidence_by_id.keys():
                return PolicyDecision("deny", "required evidence is missing")
            arguments = _json_value({
                "inputs": step.inputs,
                "dependency_outputs": {} if dependency_outputs is None else dependency_outputs,
            })
            violation = _amount_violation(arguments, policy.max_amount_minor)
            if violation:
                return PolicyDecision("deny", violation)
            drift_data = asdict(drift) if is_dataclass(drift) else drift
            drift_data = _json_value(drift_data, allow_tuples=True)
            binding = ApprovalBinding(_canonical({
                "schema_version": 1,
                "tenant_id": context.tenant_id,
                "actor_id": context.actor_id,
                "step_id": step.step_id,
                "kind": step.kind,
                "handler": step.handler,
                "handler_version": step.handler_version,
                "side_effect": step.side_effect,
                "depends_on": step.depends_on,
                "evidence_ids": sorted(step.evidence_ids),
                "arguments": arguments,
                "policy_version": policy.version,
                "policy": policy.model_dump(mode="json"),
                "evidence": [evidence_by_id[key].model_dump(mode="json")
                             for key in sorted(evidence_by_id)],
                "drift_evidence": drift_data,
            }))
            # Force UTF-8 validity before storing or comparing an approval.
            _ = binding.digest
        except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
            return PolicyDecision("deny", f"invalid action or evidence: {exc}")

        needs_review = step.handler in policy.approval_required_tools
        if drift_data is not None:
            if not isinstance(drift_data, dict):
                return PolicyDecision("deny", "invalid drift report", binding)
            status = drift_data.get("status")
            containment = drift_data.get("containment")
            if drift_data.get("tenant_id") != context.tenant_id:
                return PolicyDecision("deny", "drift report tenant mismatch", binding)
            if not isinstance(status, str) or status not in {
                "unknown", "stable", "drifted", "policy_changed", "nonconformant",
            }:
                return PolicyDecision("deny", "unrecognized drift status", binding)
            if not isinstance(containment, str) or containment not in {"none", "review", "deny"}:
                return PolicyDecision("deny", "unrecognized drift containment", binding)
            if drift_data.get("known_violations") or status == "nonconformant":
                return PolicyDecision("deny", "known policy probe violation", binding)
            if containment == "deny":
                return PolicyDecision("deny", "drift monitoring requires containment", binding)
            if drift_data.get("policy_version") != policy.version:
                status = "policy_changed"
            if status != "stable" and policy.drift_action == "deny":
                return PolicyDecision("deny", "current policy denies unresolved drift", binding)
            needs_review |= containment == "review" or (
                status != "stable" and policy.drift_action == "review"
            )

        if not needs_review:
            return PolicyDecision("allow", "current deterministic policy permits action", binding)
        if approval_id is None or self.approval_store is None:
            return PolicyDecision("approval_required", "exact-action approval is required", binding)
        record = self.approval_store.get(approval_id, tenant_id=context.tenant_id)
        if record is None or record.binding != binding:
            return PolicyDecision("approval_required", "approval does not match this action", binding)
        if record.status == "denied":
            return PolicyDecision("deny", "reviewer denied this exact action", binding, approval_id)
        if record.expires_at <= now or record.consumed_at is not None:
            return PolicyDecision("approval_required", "approval is expired or consumed", binding)
        if record.status != "approved":
            return PolicyDecision("approval_required", "approval decision is pending", binding,
                                  approval_id)
        return PolicyDecision("allow", "exact-action approval verified; claim before dispatch",
                              binding, approval_id)
