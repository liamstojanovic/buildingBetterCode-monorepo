"""Synthetic providers exercise real Epicormic/Pollard storage and analysis."""

from dataclasses import replace

import pytest
from pollard import MemoryStore, NodeKind

from canopy.drift import (
    DEFAULT_POLICY_PROBES,
    DriftMonitor,
    PolicyConformanceScorer,
    PolicyProbe,
)


def synthetic_conforming(request):
    expected = {probe.probe_id: probe.expected_decision for probe in DEFAULT_POLICY_PROBES}
    return {"decision": expected[request["probe_id"]], "explanation": "synthetic baseline"}


def monitor(store=None, **kwargs):
    return DriftMonitor(
        store if store is not None else MemoryStore(),
        tenant_id="tenant-a",
        monitor_id="provider-policy",
        policy_version="policy-1",
        **kwargs,
    )


def observe(subject, window, provider=synthetic_conforming, **kwargs):
    return subject.observe(
        window,
        provider,
        provider_fingerprint=kwargs.pop("provider_fingerprint", "synthetic-v1"),
        policy_version=kwargs.pop("policy_version", "policy-1"),
        **kwargs,
    )


def test_real_epicormic_scores_and_persists_stable_verdict():
    store = MemoryStore()
    subject = monitor(store)
    baseline = observe(subject, "baseline", establish_baseline=True)
    assert baseline.status == "unknown"
    assert baseline.containment == "review"
    assert baseline.samples_observed == 15
    current = observe(subject, "current")
    assert current.status == "stable"
    assert current.containment == "none"
    assert "does not certify compliance" in current.reason
    assert current.epicormic_state == "stable"
    assert current.verdict_node_id is not None
    verdict = store.get(current.verdict_node_id)
    assert verdict.payload["format"] == "epicormic/verdict/v1"
    assert "canopy_policy_conformance_v1" in verdict.payload["scorers"]
    assert "normalized_match" in verdict.payload["scorers"]
    calls = [
        node
        for root in store.roots()
        for node in store.walk(root)
        if node.kind == NodeKind.MODEL_CALL.value
    ]
    assert len(calls) == 30
    assert subject.latest() == current


def test_baseline_cannot_normalize_a_consistent_known_violation():
    calls = []

    def synthetic_violating(request):
        calls.append(request)
        return {"decision": "allow"}

    subject = monitor()
    result = observe(subject, "bad-baseline", synthetic_violating, establish_baseline=True)
    assert result.status == "nonconformant"
    assert result.containment == "deny"
    assert result.baseline_window_id is None
    assert result.known_violations == ("cross-tenant-read: pinned decision contract violated",)
    assert len(calls) == 1
    assert result.samples_observed == 1
    assert result.epicormic_state is None  # No statistical significance is needed.


def test_first_current_violation_stops_dispatch_and_latches_across_restart(tmp_path):
    path = tmp_path / "drift.sqlite"
    subject = monitor(path)
    observe(subject, "baseline", establish_baseline=True)
    calls = []

    def synthetic_violating(request):
        calls.append(request)
        return {"decision": "allow"}

    failed = observe(subject, "violation", synthetic_violating, samples=1)
    assert failed.status == "nonconformant"
    assert failed.samples_observed == 1
    subject.close()
    reopened = monitor(path)
    assert reopened.latest() == failed
    contained = observe(
        reopened, "later", synthetic_violating, provider_fingerprint="synthetic-fixed-v2"
    )
    assert contained.containment == "deny"
    assert contained.known_violations == failed.known_violations
    assert contained.samples_observed == 0
    assert len(calls) == 1
    reopened.close()


def test_transient_violation_persistence_error_never_downgrades_containment():
    class TransientStore(MemoryStore):
        failed = False

        def update_meta(self, node_id, patch):
            report = patch.get("canopy_drift", {}).get("latest")
            if report and report["status"] == "nonconformant" and not self.failed:
                self.failed = True
                raise OSError("synthetic transient persistence failure")
            return super().update_meta(node_id, patch)

    subject = monitor(TransientStore())
    observe(subject, "baseline", establish_baseline=True)
    report = observe(subject, "bad", lambda _: {"decision": "allow"})
    assert report.status == "nonconformant"
    assert report.containment == "deny"
    later = observe(subject, "later")
    assert later.status == "nonconformant"
    assert later.containment == "deny"
    assert later.samples_observed == 0


@pytest.mark.parametrize("samples", [1, 4])
def test_insufficient_sample_evidence_stays_unknown(samples):
    subject = monitor()
    observe(subject, "baseline", establish_baseline=True)
    report = observe(subject, "small", samples=samples)
    assert report.status == "unknown"
    assert report.containment == "review"
    assert not report.known_violations
    assert report.epicormic_state is None


@pytest.mark.parametrize(
    "output", [{}, {"decision": "probably"}, {"decision": True}, {"structured_output": []}]
)
def test_unscorable_output_never_becomes_a_baseline(output):
    subject = monitor()
    report = observe(subject, "unscorable", lambda _: output, establish_baseline=True)
    assert report.status == "unknown"
    assert report.baseline_window_id is None
    assert not report.known_violations


def test_known_tool_proposal_is_nonconformant_even_with_expected_decision():
    subject = monitor()

    def synthetic_tool_proposal(request):
        return {**synthetic_conforming(request), "tool_calls": [{"name": "send_payment"}]}

    report = observe(subject, "tool", synthetic_tool_proposal, establish_baseline=True)
    assert report.status == "nonconformant"
    assert report.samples_observed == 1


def test_no_baseline_is_unknown_despite_conforming_output():
    report = observe(monitor(), "initial")
    assert report.status == "unknown"
    assert report.baseline_window_id is None


def test_policy_revision_is_distinct_and_performs_zero_provider_calls():
    subject = monitor()
    observe(subject, "baseline", establish_baseline=True)

    def should_not_run(_):
        pytest.fail("policy revision dispatched stale probes")

    changed = observe(subject, "new-policy", should_not_run, policy_version="policy-2")
    assert changed.status == "policy_changed"
    assert changed.containment == "review"
    assert changed.samples_observed == 0
    assert not changed.provider_changed
    assert changed.policy_version == "policy-2"
    assert changed.baseline_window_id == "baseline"


def test_provider_change_alone_preserves_baseline_and_compares_behavior():
    subject = monitor()
    observe(subject, "baseline", establish_baseline=True)
    same = observe(subject, "new-provider", provider_fingerprint="synthetic-v2")
    assert same.status == "stable"
    assert same.provider_changed
    assert same.baseline_window_id == "baseline"


def test_conforming_behavior_change_runs_real_statistical_detection():
    subject = monitor()
    observe(subject, "baseline", establish_baseline=True)

    def synthetic_changed(request):
        return {**synthetic_conforming(request), "explanation": "synthetic changed wording"}

    changed = observe(subject, "changed", synthetic_changed)
    assert changed.status == "drifted"
    assert changed.epicormic_state in ("drift", "warning")
    assert not changed.known_violations
    assert changed.containment == "review"
    assert changed.verdict_node_id is not None


def test_baseline_and_window_identity_cannot_be_reused():
    subject = monitor()
    observe(subject, "baseline", establish_baseline=True)
    with pytest.raises(ValueError, match="baseline is immutable"):
        observe(
            subject, "replacement", establish_baseline=True, provider_fingerprint="synthetic-v2"
        )
    with pytest.raises(ValueError, match="window_id already used"):
        observe(subject, "baseline")
    assert subject.latest().baseline_window_id == "baseline"


def test_default_probe_version_is_pinned_even_in_a_new_store():
    changed = (replace(DEFAULT_POLICY_PROBES[0], expected_decision="allow"),)
    with pytest.raises(ValueError, match="default probe version is pinned"):
        monitor(probes=changed)


def test_custom_probe_version_cannot_change_prompts_or_expectations():
    store = MemoryStore()
    probes = (PolicyProbe("custom", "Custom rule requires denial.", "deny"),)
    monitor(store, probe_version="custom-v1", probes=probes)
    changed = (replace(probes[0], expected_decision="allow"),)
    with pytest.raises(ValueError, match="different panel_digest"):
        monitor(store, probe_version="custom-v1", probes=changed)


def test_monitor_configuration_cannot_be_revised_silently():
    store = MemoryStore()
    monitor(store)
    with pytest.raises(ValueError, match="monitor configuration changed"):
        DriftMonitor(
            store, tenant_id="tenant-a", monitor_id="provider-policy", policy_version="policy-2"
        )


def test_tenants_have_independent_windows_baselines_and_containment():
    store = MemoryStore()
    first = monitor(store)
    second = DriftMonitor(
        store, tenant_id="tenant-b", monitor_id="provider-policy", policy_version="policy-1"
    )
    observe(first, "baseline", establish_baseline=True)
    unknown = observe(second, "baseline")
    assert unknown.status == "unknown"
    assert unknown.baseline_window_id is None
    assert first.latest().baseline_window_id == "baseline"


def test_provider_failure_is_unknown_and_never_becomes_successful_evidence():
    subject = monitor()
    calls = []

    def synthetic_error(request):
        calls.append(request)
        if len(calls) == 2:
            raise RuntimeError("synthetic provider unavailable")
        return synthetic_conforming(request)

    report = observe(subject, "error", synthetic_error, establish_baseline=True)
    assert report.status == "unknown"
    assert report.samples_observed == 1
    assert report.baseline_window_id is None
    assert "failed" in report.reason
    assert len(calls) == 2


def test_interrupted_observation_cannot_leave_prior_stable_current(tmp_path):
    subject = monitor(tmp_path / "interrupted.sqlite")
    observe(subject, "baseline", establish_baseline=True)
    assert observe(subject, "stable").status == "stable"

    def interrupted_provider(_):
        assert subject.latest().status == "unknown"
        raise KeyboardInterrupt("synthetic interruption")

    with pytest.raises(KeyboardInterrupt):
        observe(subject, "interrupted", interrupted_provider)
    subject.close()
    reopened = monitor(tmp_path / "interrupted.sqlite")
    assert reopened.latest().status == "unknown"
    assert reopened.latest().window_id == "interrupted"
    assert reopened.latest().containment == "review"
    reopened.close()


def test_analysis_failure_cannot_leave_prior_stability_current(monkeypatch):
    subject = monitor()
    observe(subject, "baseline", establish_baseline=True)
    assert observe(subject, "stable").status == "stable"

    def broken_analysis(*args, **kwargs):
        raise ValueError("synthetic analysis failure")

    monkeypatch.setattr("canopy.drift.analyze", broken_analysis)
    report = observe(subject, "failed-analysis")
    assert report.status == "unknown"
    assert "analysis failed" in report.reason
    assert subject.latest() == report


def test_reopen_recovers_violation_recorded_just_before_interruption(tmp_path, monkeypatch):
    path = tmp_path / "recover.sqlite"
    subject = monitor(path)

    def interrupted_score(*args, **kwargs):
        raise KeyboardInterrupt("synthetic interruption after Pollard persisted result")

    monkeypatch.setattr(subject._scorer, "evaluate", interrupted_score)
    with pytest.raises(KeyboardInterrupt):
        observe(subject, "interrupted", lambda _: {"decision": "allow"})
    subject.close()
    reopened = monitor(path)
    recovered = reopened.latest()
    assert recovered.status == "nonconformant"
    assert recovered.containment == "deny"
    assert recovered.samples_observed == 1
    assert recovered.known_violations
    assert observe(reopened, "later").samples_observed == 0
    reopened.close()


def test_conformance_scorer_is_reference_independent_and_structured():
    scorer = PolicyConformanceScorer(DEFAULT_POLICY_PROBES)
    assert not scorer.reference_based
    assert scorer.evaluate("cross-tenant-read", {"structured_output": {"decision": "deny"}}) == 1
    assert scorer.evaluate("cross-tenant-read", {"decision": "allow"}) == 0
    assert scorer.evaluate("cross-tenant-read", {"text": "deny"}) is None
    assert scorer.evaluate("missing", {"decision": "deny"}) is None


@pytest.mark.parametrize("minimum", [True, 0, 1, 1.5])
def test_invalid_minimum_is_rejected(minimum):
    with pytest.raises(ValueError, match="min_samples"):
        monitor(min_samples=minimum)
