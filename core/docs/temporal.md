# Temporal validity

Owner 3 public interface (implementation in `canopy.temporal`):

```python
class TemporalValidator:
    def __init__(self, *, threshold: float = 0.5,
                 default_half_life_seconds: float | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None: ...

    def validate(self, evidence: Evidence, *, tenant_id: str,
                 expected_source: str | None = None,
                 expected_version: str | None = None,
                 threshold: float | None = None,
                 now: datetime | None = None) -> FreshnessDecision: ...

    def validate_step(self, task: Task, step_id: str, *,
                      expected_sources: Mapping[str, str] | None = None,
                      expected_versions: Mapping[str, str] | None = None,
                      threshold: float | None = None,
                      now: datetime | None = None) -> tuple[FreshnessDecision, ...]: ...

def validate_dag(steps: Sequence[Step]) -> tuple[str, ...]: ...
def validate_task(task: Task) -> tuple[str, ...]: ...
def relevant_evidence_ids(task: Task, step_id: str, *,
                          include_dependencies: bool = True) -> frozenset[str]: ...
def transitive_dependents(steps: Sequence[Step], changed_step_ids: Iterable[str], *,
                          include_roots: bool = True) -> frozenset[str]: ...
def affected_steps(task: Task, *, changed_evidence_ids: Iterable[str] = (),
                   changed_sources: Iterable[str] = (),
                   changed_step_ids: Iterable[str] = ()) -> frozenset[str]: ...
def changed_evidence_ids(previous: Sequence[Evidence],
                         current: Sequence[Evidence]) -> frozenset[str]: ...
```

`FreshnessDecision` is an immutable dataclass with `evidence_id`, `valid`,
`status` (`fresh`, `stale`, `inapplicable`, or `unknown`), `score: float | None`,
`threshold`, `checked_at`, `age_seconds`, `reason_codes: tuple[str, ...]`,
`reason`, and `requires_reacquisition`. `score` is temporal/quality fitness, never
proof of source authority. Callers must enforce trusted provenance and policy.

`validate_dag` returns a deterministic topological ordering of step IDs.
`validate_task` also checks evidence references and tenant consistency.
Malformed graphs, duplicate identifiers, missing references and cross-tenant
task evidence raise `TaskValidationError` (a `ValueError`). All graph helpers
validate their input. Unknown changed step IDs are errors; evidence/source
changes that have no consumers intentionally have no effect.

`validate_step` checks only the evidence explicitly referenced by that step and
its transitive dependencies, once each in task evidence order. Source/version
expectations are keyed by evidence ID, supplied by the trusted caller. It uses
one clock snapshot for the whole assessment. Unrelated stale evidence does not
block a step. An empty decision tuple means the step has no evidence requirements.

Chronofy 0.1.9 `HalfLifeDecay` and `TemporalFact` provide the actual calculation:
`quality * 0.5 ** (age_seconds / half_life_seconds)`. Explicit evidence half-life
takes precedence over a configured default. Threshold equality passes. Expiry
is a hard boundary (`now >= expires_at` fails), regardless of score. Future
observations always fail, including subsecond differences. All datetimes must
be timezone aware; comparison and returned timestamps use UTC.

If only an expiry is supplied, the unexpired evidence uses its quality weight
as the score (no decay is invented). If neither an explicit/default half-life
nor an expiry exists, validity is unknown and fails closed: the application must
obtain a freshness policy or reacquire evidence with a defined validity window.
Missing source/version expectations mean no external applicability constraint
was requested, rather than external verification of the submitted provenance.

`affected_steps` invalidates direct evidence/source consumers and their
descendants. `changed_evidence_ids` detects additions, removals and changes to
any evidence content/provenance/timing/quality field, including a payload change
without a version bump. Feed those IDs to `affected_steps` for the relevant task;
to cover removed consumers, union the results for previous and current tasks.
None of these helpers fetches, mutates or refreshes evidence. Failed decisions
explain why current evidence must be reacquired or its applicability established;
changing a timestamp is not a refresh.

No shared-contract changes or additional dependencies are required.

Runtime integration:

```python
from canopy.temporal import TemporalValidator, validate_task

order = validate_task(task)  # structural/tenant checks before any dispatch
validator = TemporalValidator(threshold=config.policy.freshness_threshold)
for step_id in order:
    decisions = validator.validate_step(task, step_id)
    failures = [decision for decision in decisions if not decision.valid]
    if failures:
        # Persist reasons and block/reacquire; do not dispatch using stale evidence.
        reason = " ".join(decision.reason for decision in failures)
        ...
    # Current permission/policy/approval and budget checks are still required.
```

Supply `expected_sources` / `expected_versions` in `validate_step` when the
application has trusted current requirements. The shared `Step` contract does
not carry these requirements. A version string on submitted evidence cannot
independently establish that it is the latest source version.

Reason codes are `tenant_mismatch`, `missing_identity`, `source_mismatch`,
`version_mismatch`, `future_observation`, `invalid_expiry`, `expired`,
`missing_freshness_policy`, and `below_threshold`. Multiple failures are retained.
Applicability failures take status precedence; time/quality failures use `stale`;
a missing freshness policy alone uses `unknown`. Future observations have no
score. `requires_reacquisition` is true on any failure: the current evidence is
not usable as supplied, and the caller must resolve the stated cause. This flag
never authorizes automatic retrieval or external operations.

`changed_evidence_ids` compares snapshot values; callers must retain a prior copy
rather than mutate the same `Evidence` object in both snapshots. A semantic
payload/provenance change invalidates consumers even if its version was not
incremented. Deterministic graph operations are iterative and support deep DAGs.
Snapshot comparison distinguishes boolean, integer and floating-point values,
including nested payloads, while ignoring dictionary key order. Unsupported
custom payload objects conservatively count as changed.

Verification (2026-10-17):

```powershell
.venv\Scripts\python.exe -m pytest core/tests/test_temporal.py -q
# 64 passed
.venv\Scripts\python.exe -m ruff check core/src/canopy/temporal.py core/tests/test_temporal.py
# All checks passed
```

All test evidence and sources are explicitly synthetic. Tests call the installed
Chronofy calculation, verify expiry/future boundaries and clock consistency,
reject tenant/source/version mismatches, preserve unrelated work during evidence
changes, and traverse a 1,500-step DAG without recursion.
