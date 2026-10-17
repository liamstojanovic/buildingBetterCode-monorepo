"""Synthetic evidence exercises real Chronofy scoring and dependency-local validity."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from chronofy.decay.half_life import HalfLifeDecay
from chronofy.models import TemporalFact

from canopy.contracts import Evidence, Step, Task
from canopy.temporal import (
    TaskValidationError,
    TemporalValidator,
    affected_steps,
    changed_evidence_ids,
    relevant_evidence_ids,
    transitive_dependents,
    validate_dag,
    validate_task,
)

NOW = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)


def synthetic_evidence(evidence_id="e-main", **overrides):
    fields = {
        "evidence_id": evidence_id,
        "tenant_id": "synthetic-tenant",
        "source": "synthetic://inventory",
        "version": "v1",
        "observed_at": NOW,
        "half_life_seconds": 60,
        "quality": 1.0,
        "data": {"synthetic": True, "available": 4},
    }
    fields.update(overrides)
    return Evidence(**fields)


def synthetic_step(step_id, *, depends_on=(), evidence_ids=()):
    return Step(
        step_id=step_id,
        kind="model",
        handler="synthetic-handler",
        depends_on=list(depends_on),
        evidence_ids=list(evidence_ids),
    )


def synthetic_task(*, steps=(), evidence=()):
    return Task(
        task_id="synthetic-task",
        tenant_id="synthetic-tenant",
        workflow_id="synthetic-workflow",
        steps=list(steps),
        evidence=list(evidence),
    )


@pytest.fixture
def branching_task():
    """A diamond shares evidence; an unrelated branch has stale evidence."""
    return synthetic_task(
        steps=[
            synthetic_step("join", depends_on=("left", "right")),
            synthetic_step("left", depends_on=("root",), evidence_ids=("e-left",)),
            synthetic_step("unrelated", evidence_ids=("e-unrelated",)),
            synthetic_step("root", evidence_ids=("e-root",)),
            synthetic_step("right", depends_on=("root",), evidence_ids=("e-root",)),
        ],
        evidence=[
            synthetic_evidence("e-left", source="synthetic://pricing"),
            synthetic_evidence("e-unrelated", observed_at=NOW - timedelta(days=1)),
            synthetic_evidence("e-root"),
        ],
    )


@pytest.mark.parametrize("age_seconds", [0, 30, 60, 120])
def test_scores_match_real_chronofy_with_quality_applied_once(age_seconds):
    evidence = synthetic_evidence(observed_at=NOW - timedelta(seconds=age_seconds), quality=0.8)
    reference_fact = TemporalFact(
        content="Explicitly synthetic inventory evidence",
        timestamp=evidence.observed_at,
        source_quality=evidence.quality,
    )
    expected = HalfLifeDecay(default_half_life=60, time_unit="seconds").compute(reference_fact, NOW)

    decision = TemporalValidator(threshold=0.4, clock=lambda: NOW).validate(
        evidence, tenant_id=evidence.tenant_id
    )

    assert decision.score == pytest.approx(expected)
    assert decision.age_seconds == age_seconds
    assert decision.valid is (expected >= 0.4)
    assert decision.checked_at == NOW


def test_validator_calls_the_installed_chronofy_calculation(monkeypatch):
    calls = []
    original_compute = HalfLifeDecay.compute

    def observed_compute(self, fact, query_time):
        calls.append((fact, query_time))
        return original_compute(self, fact, query_time)

    monkeypatch.setattr(HalfLifeDecay, "compute", observed_compute)
    evidence = synthetic_evidence(observed_at=NOW - timedelta(seconds=60), quality=0.8)
    decision = TemporalValidator(clock=lambda: NOW).validate(evidence, tenant_id=evidence.tenant_id)

    assert calls
    assert calls[0][0].timestamp == evidence.observed_at
    assert calls[0][0].source_quality == evidence.quality
    assert calls[0][1] == NOW
    assert decision.score == pytest.approx(0.4)


def test_threshold_equality_passes_and_override_controls_the_decision():
    evidence = synthetic_evidence(observed_at=NOW - timedelta(seconds=60))
    validator = TemporalValidator(threshold=0.75, clock=lambda: NOW)
    strict = validator.validate(evidence, tenant_id=evidence.tenant_id)
    equal = validator.validate(evidence, tenant_id=evidence.tenant_id, threshold=0.5)

    assert strict.valid is False
    assert strict.status == "stale"
    assert equal.valid is True
    assert equal.status == "fresh"
    assert equal.threshold == 0.5
    assert equal.requires_reacquisition is False


def test_explicit_half_life_takes_precedence_over_default():
    validator = TemporalValidator(default_half_life_seconds=30, clock=lambda: NOW)
    observed_at = NOW - timedelta(seconds=60)
    explicit = validator.validate(
        synthetic_evidence(observed_at=observed_at), tenant_id="synthetic-tenant"
    )
    default = validator.validate(
        synthetic_evidence(observed_at=observed_at, half_life_seconds=None),
        tenant_id="synthetic-tenant",
    )

    assert explicit.score == pytest.approx(0.5)
    assert explicit.valid is True
    assert default.score == pytest.approx(0.25)
    assert default.valid is False


@pytest.mark.parametrize("expiry_offset", [0, -1])
def test_expiry_is_a_hard_boundary_even_with_a_fresh_score(expiry_offset):
    evidence = synthetic_evidence(
        observed_at=NOW - timedelta(seconds=2),
        expires_at=NOW + timedelta(seconds=expiry_offset),
        half_life_seconds=3600,
    )
    decision = TemporalValidator(threshold=0, clock=lambda: NOW).validate(
        evidence, tenant_id=evidence.tenant_id
    )

    assert decision.valid is False
    assert decision.reason
    assert decision.reason_codes
    assert decision.requires_reacquisition is True


def test_expiry_only_uses_quality_without_inventing_decay():
    evidence = synthetic_evidence(
        observed_at=NOW - timedelta(days=365),
        expires_at=NOW + timedelta(microseconds=1),
        half_life_seconds=None,
        quality=0.7,
    )
    decision = TemporalValidator(clock=lambda: NOW).validate(evidence, tenant_id=evidence.tenant_id)

    assert decision.valid is True
    assert decision.score == pytest.approx(0.7)


def test_missing_freshness_policy_fails_closed():
    evidence = synthetic_evidence(half_life_seconds=None)
    decision = TemporalValidator(threshold=0, clock=lambda: NOW).validate(
        evidence, tenant_id=evidence.tenant_id
    )

    assert decision.valid is False
    assert decision.status == "unknown"
    assert decision.score is None
    assert decision.reason
    assert decision.requires_reacquisition is True


@pytest.mark.parametrize("future_seconds", [0.000001, 1, 86400])
def test_future_observations_are_rejected_despite_chronofy_age_clamping(future_seconds):
    evidence = synthetic_evidence(observed_at=NOW + timedelta(seconds=future_seconds))
    decision = TemporalValidator(threshold=0, clock=lambda: NOW).validate(
        evidence, tenant_id=evidence.tenant_id
    )

    assert decision.valid is False
    assert decision.reason
    assert decision.requires_reacquisition is True


def test_invalid_observation_expiry_interval_is_rejected():
    evidence = synthetic_evidence(
        observed_at=NOW - timedelta(seconds=10),
        expires_at=NOW - timedelta(seconds=11),
    )
    decision = TemporalValidator(clock=lambda: NOW).validate(evidence, tenant_id=evidence.tenant_id)

    assert decision.valid is False
    assert decision.reason_codes


@pytest.mark.parametrize(
    "constraints",
    [
        {"tenant_id": "another-tenant"},
        {"tenant_id": "synthetic-tenant", "expected_source": "synthetic://different"},
        {"tenant_id": "synthetic-tenant", "expected_version": "v2"},
    ],
)
def test_applicability_mismatch_rejects_otherwise_fresh_evidence(constraints):
    decision = TemporalValidator(clock=lambda: NOW).validate(synthetic_evidence(), **constraints)

    assert decision.valid is False
    assert decision.status == "inapplicable"
    assert decision.reason
    assert decision.requires_reacquisition is True


def test_matching_source_and_version_allow_applicable_evidence():
    evidence = synthetic_evidence()
    decision = TemporalValidator(clock=lambda: NOW).validate(
        evidence,
        tenant_id=evidence.tenant_id,
        expected_source=evidence.source,
        expected_version=evidence.version,
    )

    assert decision.valid is True


def test_timezone_offsets_represent_the_same_instant_and_return_utc():
    offset = timezone(timedelta(hours=-4))
    evidence = synthetic_evidence(observed_at=(NOW - timedelta(seconds=60)).astimezone(offset))
    decision = TemporalValidator(clock=lambda: NOW.astimezone(offset)).validate(
        evidence, tenant_id=evidence.tenant_id
    )

    assert decision.valid is True
    assert decision.score == pytest.approx(0.5)
    assert decision.age_seconds == 60
    assert decision.checked_at.tzinfo is UTC


def test_explicit_now_overrides_clock():
    def unexpected_clock():
        pytest.fail("Explicit now must avoid consulting the clock")

    decision = TemporalValidator(clock=unexpected_clock).validate(
        synthetic_evidence(), tenant_id="synthetic-tenant", now=NOW
    )

    assert decision.valid is True
    assert decision.checked_at == NOW


@pytest.mark.parametrize("explicit_now", [False, True])
def test_naive_reference_times_are_rejected(explicit_now):
    naive = NOW.replace(tzinfo=None)
    validator = TemporalValidator(clock=lambda: naive)
    kwargs = {"now": naive} if explicit_now else {}

    with pytest.raises(ValueError):
        validator.validate(synthetic_evidence(), tenant_id="synthetic-tenant", **kwargs)


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_thresholds_are_rejected(threshold):
    with pytest.raises(ValueError):
        TemporalValidator(threshold=threshold)
    with pytest.raises(ValueError):
        TemporalValidator(clock=lambda: NOW).validate(
            synthetic_evidence(), tenant_id="synthetic-tenant", threshold=threshold
        )


@pytest.mark.parametrize("half_life", [0, -1, float("nan"), float("inf")])
def test_invalid_default_half_life_is_rejected(half_life):
    with pytest.raises(ValueError):
        TemporalValidator(default_half_life_seconds=half_life)


def test_decision_is_immutable_and_validation_does_not_refresh_evidence():
    evidence = synthetic_evidence(observed_at=NOW - timedelta(days=1))
    original = evidence.model_dump(mode="json")
    decision = TemporalValidator(clock=lambda: NOW).validate(evidence, tenant_id=evidence.tenant_id)

    assert decision.valid is False
    assert decision.requires_reacquisition is True
    assert evidence.model_dump(mode="json") == original
    with pytest.raises(FrozenInstanceError):
        decision.valid = True


def test_validate_dag_orders_dependencies_deterministically(branching_task):
    order = validate_dag(branching_task.steps)

    assert order == validate_dag(branching_task.steps)
    assert set(order) == {step.step_id for step in branching_task.steps}
    positions = {step_id: index for index, step_id in enumerate(order)}
    for step in branching_task.steps:
        for dependency in step.depends_on:
            assert positions[dependency] < positions[step.step_id]


def test_empty_graph_and_task_are_valid():
    assert validate_dag([]) == ()
    assert validate_task(synthetic_task()) == ()


@pytest.mark.parametrize(
    "steps",
    [
        [synthetic_step("duplicate"), synthetic_step("duplicate")],
        [synthetic_step("missing", depends_on=("absent",))],
        [synthetic_step("self", depends_on=("self",))],
        [synthetic_step("a", depends_on=("b",)), synthetic_step("b", depends_on=("a",))],
    ],
)
def test_malformed_dags_raise_task_validation_errors(steps):
    with pytest.raises(TaskValidationError):
        validate_dag(steps)


@pytest.mark.parametrize(
    "task",
    [
        synthetic_task(steps=[synthetic_step("a", evidence_ids=("absent",))]),
        synthetic_task(evidence=[synthetic_evidence(), synthetic_evidence()]),
        synthetic_task(evidence=[synthetic_evidence(tenant_id="another-tenant")]),
    ],
)
def test_validate_task_rejects_invalid_evidence_references_and_identity(task):
    with pytest.raises(TaskValidationError):
        validate_task(task)


def test_graph_helpers_reject_unknown_step_ids(branching_task):
    with pytest.raises(TaskValidationError):
        relevant_evidence_ids(branching_task, "absent")
    with pytest.raises(TaskValidationError):
        transitive_dependents(branching_task.steps, ["absent"])
    with pytest.raises(TaskValidationError):
        affected_steps(branching_task, changed_step_ids=["absent"])
    with pytest.raises(TaskValidationError):
        TemporalValidator(clock=lambda: NOW).validate_step(branching_task, "absent")


def test_relevant_evidence_includes_dependencies_once_and_excludes_unrelated(branching_task):
    assert relevant_evidence_ids(branching_task, "join") == frozenset({"e-left", "e-root"})
    assert relevant_evidence_ids(branching_task, "join", include_dependencies=False) == frozenset()
    assert relevant_evidence_ids(branching_task, "right", include_dependencies=False) == frozenset(
        {"e-root"}
    )


def test_validate_step_uses_one_clock_snapshot_and_task_evidence_order(branching_task):
    clock_calls = []

    def ticking_clock():
        instant = NOW + timedelta(seconds=len(clock_calls) * 120)
        clock_calls.append(instant)
        return instant

    decisions = TemporalValidator(clock=ticking_clock).validate_step(branching_task, "join")

    assert clock_calls == [NOW]
    assert tuple(decision.evidence_id for decision in decisions) == ("e-left", "e-root")
    assert all(decision.valid for decision in decisions)
    assert all(decision.checked_at == NOW for decision in decisions)


def test_step_applicability_expectations_apply_to_dependency_evidence(branching_task):
    decisions = TemporalValidator(clock=lambda: NOW).validate_step(
        branching_task,
        "join",
        expected_sources={"e-root": "synthetic://wrong"},
        expected_versions={"e-left": "v2"},
    )

    assert len(decisions) == 2
    assert all(not decision.valid and decision.status == "inapplicable" for decision in decisions)


def test_step_without_evidence_returns_no_decisions():
    task = synthetic_task(steps=[synthetic_step("empty")])
    assert TemporalValidator(clock=lambda: NOW).validate_step(task, "empty") == ()


def test_stale_ancestor_blocks_descendant_without_its_own_evidence(branching_task):
    validator = TemporalValidator(clock=lambda: NOW + timedelta(seconds=120))

    decisions = validator.validate_step(branching_task, "join")

    assert {decision.evidence_id for decision in decisions} == {"e-left", "e-root"}
    assert all(not decision.valid and decision.requires_reacquisition for decision in decisions)


def test_transitive_dependents_follow_diamond_without_touching_unrelated_branch(branching_task):
    assert transitive_dependents(branching_task.steps, ["root"]) == frozenset(
        {"root", "left", "right", "join"}
    )
    assert transitive_dependents(branching_task.steps, ["root"], include_roots=False) == frozenset(
        {"left", "right", "join"}
    )
    assert transitive_dependents(branching_task.steps, ["left"]) == frozenset({"left", "join"})
    assert transitive_dependents(branching_task.steps, []) == frozenset()


def test_evidence_changes_invalidate_only_consumers_and_descendants(branching_task):
    assert affected_steps(branching_task, changed_evidence_ids=["e-root"]) == frozenset(
        {"root", "left", "right", "join"}
    )
    assert affected_steps(branching_task, changed_evidence_ids=["e-left"]) == frozenset(
        {"left", "join"}
    )
    assert affected_steps(branching_task, changed_evidence_ids=["not-consumed"]) == frozenset()


def test_source_changes_and_step_changes_are_combined_locally(branching_task):
    assert affected_steps(
        branching_task, changed_sources=["synthetic://pricing"], changed_step_ids=["right"]
    ) == frozenset({"left", "right", "join"})
    assert affected_steps(branching_task, changed_sources=["synthetic://absent"]) == frozenset()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "v2"},
        {"tenant_id": "another-tenant"},
        {"source": "synthetic://another-source"},
        {"observed_at": NOW - timedelta(seconds=1)},
        {"expires_at": NOW + timedelta(seconds=1)},
        {"half_life_seconds": 30},
        {"quality": 0.9},
        {"data": {"synthetic": True, "available": 3}},
    ],
)
def test_evidence_comparison_detects_every_semantic_field_change(changes):
    before = synthetic_evidence()
    after = synthetic_evidence(**changes)

    assert changed_evidence_ids([before], [after]) == frozenset({"e-main"})


def test_evidence_comparison_detects_addition_removal_and_ignores_order():
    first = synthetic_evidence("first")
    second = synthetic_evidence("second")
    added = synthetic_evidence("added")

    assert changed_evidence_ids([first, second], [second, first]) == frozenset()
    assert changed_evidence_ids([first, second], [second, added]) == frozenset({"first", "added"})
    assert changed_evidence_ids([], []) == frozenset()


@pytest.mark.parametrize("replacement", [True, 1.0])
def test_nested_payload_type_changes_invalidate_consumers(replacement):
    before = synthetic_evidence(data={"nested": [{"value": 1}]})
    after = synthetic_evidence(data={"nested": [{"value": replacement}]})

    assert changed_evidence_ids([before], [after]) == frozenset({"e-main"})


def test_payload_dictionary_key_order_does_not_invalidate_evidence():
    before = synthetic_evidence(data={"value": 1, "nested": {"first": True, "second": 2}})
    after = synthetic_evidence(data={"nested": {"second": 2, "first": True}, "value": 1})

    assert changed_evidence_ids([before], [after]) == frozenset()


def test_changed_payload_without_version_bump_invalidates_only_related_work(branching_task):
    updated = [
        evidence.model_copy(update={"data": {"synthetic": True, "available": 0}})
        if evidence.evidence_id == "e-left"
        else evidence
        for evidence in branching_task.evidence
    ]
    changes = changed_evidence_ids(branching_task.evidence, updated)

    assert changes == frozenset({"e-left"})
    assert affected_steps(branching_task, changed_evidence_ids=changes) == frozenset(
        {"left", "join"}
    )


def test_long_dependency_chain_is_supported_without_recursive_traversal():
    steps = [synthetic_step("step-0", evidence_ids=("e-main",))]
    steps.extend(
        synthetic_step(f"step-{index}", depends_on=(f"step-{index - 1}",))
        for index in range(1, 1500)
    )
    task = synthetic_task(steps=steps, evidence=[synthetic_evidence()])

    assert len(validate_dag(steps)) == 1500
    assert relevant_evidence_ids(task, "step-1499") == frozenset({"e-main"})
    assert len(affected_steps(task, changed_evidence_ids=["e-main"])) == 1500
