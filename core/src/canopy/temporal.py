"""Evidence freshness and dependency-local invalidation using real Chronofy decay.

Freshness is not authenticity. Expectations and freshness policies must come from
the trusted application; this module never obtains or manufactures new evidence.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from heapq import heappop, heappush
from math import isfinite
from typing import Literal

from chronofy import HalfLifeDecay, TemporalFact

from canopy.contracts import Evidence, Step, Task, utcnow

__all__ = [
    "FreshnessDecision",
    "TaskValidationError",
    "TemporalValidator",
    "affected_steps",
    "changed_evidence_ids",
    "relevant_evidence_ids",
    "transitive_dependents",
    "validate_dag",
    "validate_task",
]


class TaskValidationError(ValueError):
    """Task identifiers, evidence boundaries or dependency edges are invalid."""


@dataclass(frozen=True)
class FreshnessDecision:
    """An assessment at one instant, not a certificate of source authority."""

    evidence_id: str
    valid: bool
    status: Literal["fresh", "stale", "inapplicable", "unknown"]
    score: float | None
    threshold: float
    checked_at: datetime
    age_seconds: float
    reason_codes: tuple[str, ...]
    reason: str
    requires_reacquisition: bool


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _threshold(value: float) -> float:
    if not isfinite(value) or not 0 <= value <= 1:
        raise ValueError("threshold must be finite and between 0 and 1")
    return float(value)


class TemporalValidator:
    """Apply explicit boundaries around Chronofy's quality-weighted half-life score.

    An expiry alone supplies a hard validity window with no invented decay. In
    the absence of both expiry and a configured half-life, freshness is unknown.
    """

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        default_half_life_seconds: float | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.threshold = _threshold(threshold)
        if default_half_life_seconds is not None and (
            not isfinite(default_half_life_seconds) or default_half_life_seconds <= 0
        ):
            raise ValueError("default_half_life_seconds must be finite and positive")
        self.default_half_life_seconds = default_half_life_seconds
        self._clock = clock

    def validate(
        self,
        evidence: Evidence,
        *,
        tenant_id: str,
        expected_source: str | None = None,
        expected_version: str | None = None,
        threshold: float | None = None,
        now: datetime | None = None,
    ) -> FreshnessDecision:
        """Assess evidence without changing it; supplied time overrides the clock.

        Source/version equality only checks applicability to caller expectations.
        It does not verify that those expectations or the evidence are authentic.
        """
        checked_at = _utc(self._clock() if now is None else now, "now")
        observed_at = _utc(evidence.observed_at, "observed_at")
        expires_at = (
            None if evidence.expires_at is None else _utc(evidence.expires_at, "expires_at")
        )
        cutoff = self.threshold if threshold is None else _threshold(threshold)
        age_seconds = (checked_at - observed_at).total_seconds()
        failures: list[tuple[str, str]] = []
        inapplicable = False
        if not tenant_id.strip() or evidence.tenant_id != tenant_id:
            failures.append(
                ("tenant_mismatch", "Evidence does not belong to the requested tenant.")
            )
            inapplicable = True
        if (
            not evidence.evidence_id.strip()
            or not evidence.source.strip()
            or not evidence.version.strip()
        ):
            failures.append(
                ("missing_identity", "Evidence identity, source or version is missing.")
            )
            inapplicable = True
        if expected_source is not None and evidence.source != expected_source:
            failures.append(
                ("source_mismatch", "Evidence source differs from the required source.")
            )
            inapplicable = True
        if expected_version is not None and evidence.version != expected_version:
            failures.append(
                ("version_mismatch", "Evidence version differs from the required version.")
            )
            inapplicable = True
        if age_seconds < 0:
            failures.append(("future_observation", "Observation timestamp is in the future."))
        if expires_at is not None:
            if expires_at <= observed_at:
                failures.append(("invalid_expiry", "Expiry must be later than the observation."))
            if checked_at >= expires_at:
                failures.append(("expired", "The explicit evidence validity window has expired."))

        half_life = (
            evidence.half_life_seconds
            if evidence.half_life_seconds is not None
            else self.default_half_life_seconds
        )
        score: float | None = None
        unknown = half_life is None and expires_at is None
        # Chronofy clamps future age to zero. Never present that as a valid score.
        if age_seconds >= 0:
            if half_life is not None:
                decay = HalfLifeDecay(default_half_life=half_life, time_unit="seconds")
                fact = TemporalFact(
                    content=evidence.evidence_id,
                    timestamp=observed_at,
                    source_quality=evidence.quality,
                    source=evidence.source,
                    metadata={"tenant_id": evidence.tenant_id, "version": evidence.version},
                )
                score = decay.compute(fact, checked_at)
            elif expires_at is not None:
                score = evidence.quality
        if unknown:
            failures.append(
                ("missing_freshness_policy", "No half-life or explicit expiry is defined.")
            )
        if score is not None and score < cutoff:
            failures.append(
                ("below_threshold", f"Freshness/quality score {score:.6g} is below {cutoff:.6g}.")
            )

        status: Literal["fresh", "stale", "inapplicable", "unknown"]
        if inapplicable:
            status = "inapplicable"
        elif any(code not in {"missing_freshness_policy"} for code, _ in failures):
            status = "stale"
        elif unknown:
            status = "unknown"
        else:
            status = "fresh"
        valid = not failures
        if valid:
            reason = "Evidence meets the supplied freshness and applicability constraints."
        else:
            reason = " ".join(message for _, message in failures)
            reason += (
                " Reacquire evidence from the applicable source with a valid observation and"
                " freshness policy, or establish its applicability; changing a timestamp is"
                " not reacquisition. Freshness does not establish source authority."
            )
        return FreshnessDecision(
            evidence_id=evidence.evidence_id,
            valid=valid,
            status=status,
            score=score,
            threshold=cutoff,
            checked_at=checked_at,
            age_seconds=age_seconds,
            reason_codes=tuple(code for code, _ in failures),
            reason=reason,
            requires_reacquisition=not valid,
        )

    def validate_step(
        self,
        task: Task,
        step_id: str,
        *,
        expected_sources: Mapping[str, str] | None = None,
        expected_versions: Mapping[str, str] | None = None,
        threshold: float | None = None,
        now: datetime | None = None,
    ) -> tuple[FreshnessDecision, ...]:
        """Check only this step's evidence and its ancestors' evidence at one time."""
        relevant = relevant_evidence_ids(task, step_id)
        checked_at = _utc(self._clock() if now is None else now, "now")
        cutoff = self.threshold if threshold is None else _threshold(threshold)
        sources = {} if expected_sources is None else expected_sources
        versions = {} if expected_versions is None else expected_versions
        return tuple(
            self.validate(
                evidence,
                tenant_id=task.tenant_id,
                expected_source=sources.get(evidence.evidence_id),
                expected_version=versions.get(evidence.evidence_id),
                threshold=cutoff,
                now=checked_at,
            )
            for evidence in task.evidence
            if evidence.evidence_id in relevant
        )


def _graph(
    steps: Sequence[Step],
) -> tuple[dict[str, Step], dict[str, list[str]], tuple[str, ...]]:
    indexed: dict[str, Step] = {}
    for step in steps:
        if not step.step_id.strip():
            raise TaskValidationError("Step ID must not be empty")
        if step.step_id in indexed:
            raise TaskValidationError(f"Duplicate step ID: {step.step_id!r}")
        indexed[step.step_id] = step
    dependents: dict[str, list[str]] = {step_id: [] for step_id in indexed}
    degree: dict[str, int] = {}
    for step_id, step in indexed.items():
        if len(set(step.depends_on)) != len(step.depends_on):
            raise TaskValidationError(f"Duplicate dependency for step {step_id!r}")
        for dependency in step.depends_on:
            if dependency not in indexed:
                raise TaskValidationError(f"Unknown dependency {dependency!r} for step {step_id!r}")
            dependents[dependency].append(step_id)
        degree[step_id] = len(step.depends_on)
    positions = {step_id: index for index, step_id in enumerate(indexed)}
    ready: list[tuple[int, str]] = []
    for step_id, count in degree.items():
        if count == 0:
            heappush(ready, (positions[step_id], step_id))
    ordered: list[str] = []
    while ready:
        _, step_id = heappop(ready)
        ordered.append(step_id)
        for dependent in dependents[step_id]:
            degree[dependent] -= 1
            if degree[dependent] == 0:
                heappush(ready, (positions[dependent], dependent))
    if len(ordered) != len(indexed):
        blocked = [step_id for step_id, count in degree.items() if count]
        raise TaskValidationError(f"Dependency cycle blocks steps: {blocked!r}")
    return indexed, dependents, tuple(ordered)


def validate_dag(steps: Sequence[Step]) -> tuple[str, ...]:
    """Return stable topological step IDs or raise TaskValidationError."""
    return _graph(steps)[2]


def _evidence_index(evidence: Sequence[Evidence]) -> dict[str, Evidence]:
    indexed: dict[str, Evidence] = {}
    for item in evidence:
        if not item.evidence_id.strip():
            raise TaskValidationError("Evidence ID must not be empty")
        if item.evidence_id in indexed:
            raise TaskValidationError(f"Duplicate evidence ID: {item.evidence_id!r}")
        indexed[item.evidence_id] = item
    return indexed


def validate_task(task: Task) -> tuple[str, ...]:
    """Check graph, evidence references and tenant boundaries before dispatch."""
    ordered = validate_dag(task.steps)
    evidence = _evidence_index(task.evidence)
    if not task.tenant_id.strip():
        raise TaskValidationError("Task tenant ID must not be empty")
    for item in evidence.values():
        if item.tenant_id != task.tenant_id:
            raise TaskValidationError(
                f"Evidence {item.evidence_id!r} belongs to a different tenant"
            )
    for step in task.steps:
        for evidence_id in step.evidence_ids:
            if evidence_id not in evidence:
                raise TaskValidationError(
                    f"Unknown evidence {evidence_id!r} for step {step.step_id!r}"
                )
    return ordered


def relevant_evidence_ids(
    task: Task, step_id: str, *, include_dependencies: bool = True
) -> frozenset[str]:
    """Collect explicit evidence requirements, optionally including all ancestors."""
    validate_task(task)
    indexed = {step.step_id: step for step in task.steps}
    if step_id not in indexed:
        raise TaskValidationError(f"Unknown step ID: {step_id!r}")
    pending = [step_id]
    visited: set[str] = set()
    evidence: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        evidence.update(indexed[current].evidence_ids)
        if include_dependencies:
            pending.extend(indexed[current].depends_on)
    return frozenset(evidence)


def _descendants(dependents: Mapping[str, Sequence[str]], roots: set[str]) -> frozenset[str]:
    pending = list(roots)
    visited: set[str] = set()
    while pending:
        step_id = pending.pop()
        if step_id in visited:
            continue
        visited.add(step_id)
        pending.extend(dependents[step_id])
    return frozenset(visited)


def transitive_dependents(
    steps: Sequence[Step], changed_step_ids: Iterable[str], *, include_roots: bool = True
) -> frozenset[str]:
    """Find descendants of changed steps, preserving unrelated graph branches."""
    indexed, dependents, _ = _graph(steps)
    roots = set(changed_step_ids)
    unknown = roots - indexed.keys()
    if unknown:
        raise TaskValidationError(f"Unknown changed step IDs: {sorted(unknown)!r}")
    descendants = _descendants(dependents, roots)
    return descendants if include_roots else descendants - roots


def affected_steps(
    task: Task,
    *,
    changed_evidence_ids: Iterable[str] = (),
    changed_sources: Iterable[str] = (),
    changed_step_ids: Iterable[str] = (),
) -> frozenset[str]:
    """Invalidate direct evidence/source consumers and their transitive descendants."""
    validate_task(task)
    changed = set(changed_evidence_ids)
    sources = set(changed_sources)
    changed.update(item.evidence_id for item in task.evidence if item.source in sources)
    roots = set(changed_step_ids)
    roots.update(step.step_id for step in task.steps if changed.intersection(step.evidence_ids))
    return transitive_dependents(task.steps, roots)


def changed_evidence_ids(
    previous: Sequence[Evidence], current: Sequence[Evidence]
) -> frozenset[str]:
    """Detect all evidence edits, even content changes without a version bump.

    Compare immutable snapshots: if a caller mutates and supplies the same object
    as both versions, no history remains from which to detect that change.
    """
    before = _evidence_index(previous)
    after = _evidence_index(current)
    return frozenset(
        evidence_id
        for evidence_id in before.keys() | after.keys()
        if evidence_id not in before
        or evidence_id not in after
        or not _same_snapshot(before[evidence_id].model_dump(), after[evidence_id].model_dump())
    )


def _same_snapshot(before: object, after: object) -> bool:
    """Compare nested values without conflating JSON booleans and numbers.

    Unknown payload object types conservatively count as changed. No arbitrary
    object equality is trusted to prove that evidence remains the same.
    """
    pending = [(before, after)]
    while pending:
        left, right = pending.pop()
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            if {(type(key), key) for key in left} != {(type(key), key) for key in right}:
                return False
            pending.extend((value, right[key]) for key, value in left.items())
        elif isinstance(left, (list, tuple)):
            if len(left) != len(right):
                return False
            pending.extend(zip(left, right, strict=True))
        elif type(left) not in {str, int, float, bool, bytes, datetime, type(None)}:
            return False
        elif left != right:
            return False
    return True
