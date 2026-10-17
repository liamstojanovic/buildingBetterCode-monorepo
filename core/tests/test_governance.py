"""Synthetic enterprise actions; no external transactions or providers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pollard.policy import Decision as PollardDecision

from canopy.contracts import Evidence, Policy, Step
from canopy.governance import ApprovalStore, ExecutionContext, PolicyGate, ReviewerIdentity

NOW = datetime(2026, 10, 17, 16, tzinfo=UTC)
CONTEXT = ExecutionContext("tenant-a", "operator-a", frozenset({"execute:payment"}))
POLICY = Policy(allowed_tools=["payment"], approval_required_tools=["payment"],
                max_amount_minor=1000)
SESSION = object()


def authorize(session):
    """Synthetic auth fixture: identity requires possession of this session object."""
    if session is SESSION:
        return ReviewerIdentity("tenant-a", "reviewer-a", frozenset({"approvals:decide"}))
    return None


def action(**updates):
    base = Step(step_id="pay", kind="tool", handler="payment", side_effect=True,
                inputs={"recipient": "vendor", "amount_minor": 100})
    return base.model_copy(update=updates)


def evidence(**updates):
    base = Evidence(evidence_id="invoice", tenant_id="tenant-a", source="invoice-service",
                    version="1", observed_at=NOW, expires_at=NOW + timedelta(hours=2),
                    data={"invoice_id": "INV-SYNTHETIC", "amount_minor": 100})
    return base.model_copy(update=updates)


def evaluate(gate, step=None, **kwargs):
    values = {"context": CONTEXT, "policy": POLICY, "now": NOW}
    values.update(kwargs)
    return gate.evaluate(action() if step is None else step, **values)


def approve(store, binding):
    request = store.request(binding, now=NOW)
    return store.decide(request.approval_id, reviewer_context=SESSION, approved=True, now=NOW)


def test_known_policy_denials_cannot_be_overridden():
    gate = PolicyGate()
    assert evaluate(gate, context=ExecutionContext("tenant-a", "actor", frozenset())).status == "deny"
    assert evaluate(gate, policy=Policy()).status == "deny"
    assert evaluate(gate, action(inputs={"amount_minor": 1001})).status == "deny"
    assert evaluate(gate, action(inputs={"batch": [{"amount_minor": 1001}]})).status == "deny"
    assert evaluate(gate, dependency_outputs={"invoice": {"amount_minor": 1001}}).status == "deny"
    assert evaluate(gate, policy=POLICY.model_copy(update={"approval_required_tools": []})).allowed
    model = action(kind="model", handler="planner")
    context = ExecutionContext("tenant-a", "actor", frozenset({"execute:planner"}))
    assert evaluate(gate, model, context=context, policy=Policy()).allowed


@pytest.mark.parametrize("amount", [True, False, -1, 1.2, "100", None])
def test_amount_requires_nonnegative_integer(amount):
    assert evaluate(PolicyGate(), action(inputs={"amount_minor": amount})).status == "deny"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "integer-key"},
                                   {"set": {1}}, {"tuple": (1,)}, object()])
def test_invalid_original_inputs_and_evidence_are_denied(value):
    assert evaluate(PolicyGate(), action(inputs={"value": value})).status == "deny"
    item = evidence(data={"value": value})
    assert evaluate(PolicyGate(), evidence=[item]).status == "deny"


def test_cyclic_inputs_fail_closed():
    data = {}
    data["self"] = data
    assert evaluate(PolicyGate(), action(inputs=data)).status == "deny"


def test_canonical_json_is_immutable_and_preserves_meaning():
    gate = PolicyGate()
    left = action(inputs={"a": 1, "b": ["x", "y"]})
    right = action(inputs={"b": ["x", "y"], "a": 1})
    original = evaluate(gate, left).binding
    assert original == evaluate(gate, right).binding
    left.inputs["b"].reverse()
    assert original != evaluate(gate, left).binding
    assert original.to_dict()["arguments"]["inputs"]["b"] == ["x", "y"]
    assert evaluate(gate, action(inputs={"a": True})).binding != evaluate(
        gate, action(inputs={"a": 1}),
    ).binding
    detached = original.to_dict()
    detached["actor_id"] = "evil"
    assert original.actor_id == "operator-a"


def test_approval_persists_exact_binding_then_claims_once(tmp_path):
    path = tmp_path / "approvals.sqlite"
    store = ApprovalStore(path, reviewer_authorizer=authorize)
    gate = PolicyGate(store)
    decision = evaluate(gate)
    assert decision.status == "approval_required"
    assert decision.pollard_decision is PollardDecision.CONFIRM
    record = approve(store, decision.binding)
    assert store.request(decision.binding, now=NOW).approval_id == record.approval_id
    store.close()

    store = ApprovalStore(path, reviewer_authorizer=authorize)
    gate = PolicyGate(store)
    permitted = evaluate(gate, approval_id=record.approval_id)
    assert permitted.allowed
    assert permitted.pollard_decision is PollardDecision.ALLOW
    claimed = store.consume(record.approval_id, permitted.binding, now=NOW)
    assert claimed.consumed_at == NOW
    assert evaluate(gate, approval_id=record.approval_id).status == "approval_required"
    with pytest.raises(PermissionError, match="not available"):
        store.consume(record.approval_id, permitted.binding, now=NOW)
    with pytest.raises(ValueError, match="expired or consumed"):
        store.decide(record.approval_id, reviewer_context=SESSION, approved=True, now=NOW)
    store.close()


@pytest.mark.parametrize("change", ["actor", "tenant", "inputs", "policy_version",
                                    "policy_content", "handler_version", "evidence_content",
                                    "evidence_version", "dependencies", "side_effect", "step_id"])
def test_approval_rejects_each_changed_action_dimension(tmp_path, change):
    store = ApprovalStore(tmp_path / "approvals.sqlite", reviewer_authorizer=authorize)
    gate = PolicyGate(store)
    step = action(evidence_ids=["invoice"])
    kwargs = {"evidence": [evidence()], "dependency_outputs": {"lookup": {"vendor": "A"}}}
    original = evaluate(gate, step, **kwargs)
    approved = approve(store, original.binding)
    if change == "actor":
        kwargs["context"] = ExecutionContext("tenant-a", "other", CONTEXT.permissions)
    elif change == "tenant":
        kwargs["context"] = ExecutionContext("tenant-b", "operator-a", CONTEXT.permissions)
        kwargs["evidence"] = [evidence(tenant_id="tenant-b")]
    elif change == "inputs":
        step = action(evidence_ids=["invoice"], inputs={"amount_minor": 101})
    elif change == "policy_version":
        kwargs["policy"] = POLICY.model_copy(update={"version": "2"})
    elif change == "policy_content":
        kwargs["policy"] = POLICY.model_copy(update={"max_amount_minor": 999})
    elif change == "evidence_content":
        kwargs["evidence"] = [evidence(data={"invoice_id": "CHANGED"})]
    elif change == "evidence_version":
        kwargs["evidence"] = [evidence(version="2")]
    elif change == "dependencies":
        kwargs["dependency_outputs"] = {"lookup": {"vendor": "B"}}
    else:
        field = {"handler_version": "2", "side_effect": False, "step_id": "other"}
        step = step.model_copy(update={change: field[change]})
    current = evaluate(gate, step, approval_id=approved.approval_id, **kwargs)
    assert current.status == "approval_required"
    with pytest.raises(PermissionError, match="does not match"):
        store.consume(approved.approval_id, current.binding, now=NOW)
    store.close()


def test_evidence_presence_tenant_expiry_and_duplicates():
    gate = PolicyGate()
    step = action(evidence_ids=["invoice"])
    assert evaluate(gate, step).status == "deny"
    assert evaluate(gate, step, evidence=[evidence(tenant_id="tenant-b")]).status == "deny"
    assert evaluate(gate, step, evidence=[evidence(expires_at=NOW)]).status == "deny"
    assert evaluate(gate, step, evidence=[evidence(), evidence()]).status == "deny"
    assert evaluate(gate, step, evidence=[evidence()]).status == "approval_required"


@pytest.mark.parametrize("identity", [None,
    ReviewerIdentity("tenant-b", "reviewer", frozenset({"approvals:decide"})),
    ReviewerIdentity("tenant-a", "reviewer", frozenset({"execute:*"})),
    {"tenant_id": "tenant-a", "role": "admin"},
])
def test_reviewer_labels_and_wrong_permissions_do_not_authorize(tmp_path, identity):
    store = ApprovalStore(tmp_path / "approvals.sqlite", reviewer_authorizer=lambda _: identity)
    record = store.request(evaluate(PolicyGate()).binding, now=NOW)
    with pytest.raises(PermissionError):
        store.decide(record.approval_id, reviewer_context={"role": "admin"}, approved=True, now=NOW)
    assert store.get(record.approval_id, tenant_id="tenant-a").status == "pending"
    assert store.get(record.approval_id, tenant_id="tenant-b") is None
    store.close()


def test_absent_or_failing_authorizer_never_changes_approval(tmp_path):
    path = tmp_path / "approvals.sqlite"
    store = ApprovalStore(path)
    request = store.request(evaluate(PolicyGate()).binding, now=NOW)
    with pytest.raises(PermissionError):
        store.decide(request.approval_id, reviewer_context=SESSION, approved=True, now=NOW)
    store.close()

    def broken_authorizer(_):
        raise RuntimeError("synthetic auth service outage")

    store = ApprovalStore(path, reviewer_authorizer=broken_authorizer)
    with pytest.raises(RuntimeError, match="outage"):
        store.decide(request.approval_id, reviewer_context=SESSION, approved=True, now=NOW)
    assert store.get(request.approval_id, tenant_id="tenant-a").status == "pending"
    store.close()


def test_denials_are_immutable_and_revoked_permissions_block(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.sqlite", reviewer_authorizer=authorize)
    gate = PolicyGate(store)
    request = store.request(evaluate(gate).binding, now=NOW)
    denied = store.decide(request.approval_id, reviewer_context=SESSION, approved=False,
                          reason="synthetic denial", now=NOW)
    assert store.decide(request.approval_id, reviewer_context=SESSION, approved=False,
                        reason="synthetic denial", now=NOW) == denied
    with pytest.raises(ValueError, match="immutable"):
        store.decide(request.approval_id, reviewer_context=SESSION, approved=True, now=NOW)
    assert evaluate(gate, approval_id=request.approval_id).status == "deny"
    new_request = approve(store, evaluate(gate).binding)
    assert new_request.approval_id != denied.approval_id
    revoked = ExecutionContext("tenant-a", "operator-a", frozenset())
    assert evaluate(gate, context=revoked, approval_id=new_request.approval_id).status == "deny"
    assert evaluate(gate, action(inputs={"amount_minor": 1001}),
                    approval_id=new_request.approval_id).status == "deny"
    store.close()


def test_expiry_boundary_and_naive_time_are_rejected(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.sqlite", reviewer_authorizer=authorize)
    binding = evaluate(PolicyGate()).binding
    request = store.request(binding, expires_at=NOW + timedelta(seconds=1), now=NOW)
    store.decide(request.approval_id, reviewer_context=SESSION, approved=True, now=NOW)
    later = NOW + timedelta(seconds=1)
    assert evaluate(PolicyGate(store), approval_id=request.approval_id,
                    now=later).status == "approval_required"
    with pytest.raises(PermissionError):
        store.consume(request.approval_id, binding, now=later)
    with pytest.raises(ValueError):
        store.request(binding, expires_at=NOW, now=NOW)
    with pytest.raises(ValueError, match="timezone"):
        store.request(binding, now=NOW.replace(tzinfo=None))
    store.close()


def test_two_independent_connections_have_one_atomic_claim_winner(tmp_path):
    path = tmp_path / "approvals.sqlite"
    first = ApprovalStore(path, reviewer_authorizer=authorize)
    second = ApprovalStore(path, reviewer_authorizer=authorize)
    binding = evaluate(PolicyGate()).binding
    record = approve(first, binding)

    def claim(store):
        try:
            store.consume(record.approval_id, binding, now=NOW)
            return "claimed"
        except PermissionError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, [first, second])) == ["claimed", "refused"]
    first.close()
    second.close()


def drift_report(**updates):
    result = {"status": "stable", "containment": "none", "known_violations": [],
              "tenant_id": "tenant-a", "policy_version": "1", "window_id": "probe-1"}
    result.update(updates)
    return result


def test_known_drift_violation_overrides_statistical_stability_and_allow_policy():
    policy = POLICY.model_copy(update={"approval_required_tools": [], "drift_action": "allow"})
    gate = PolicyGate()
    assert evaluate(gate, policy=policy, drift=drift_report()).allowed
    bad = drift_report(known_violations=["forbidden tool accepted"])
    assert evaluate(gate, policy=policy, drift=bad).status == "deny"
    assert evaluate(gate, policy=policy, drift=drift_report(status="nonconformant")).status == "deny"
    assert evaluate(gate, policy=policy, drift=drift_report(containment="deny")).status == "deny"
    assert evaluate(gate, policy=policy, drift=drift_report(tenant_id="tenant-b")).status == "deny"
    assert evaluate(gate, policy=policy, drift=SimpleNamespace(status="stable")).status == "deny"


def test_unknown_drift_needs_review_and_updated_report_invalidates_approval(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.sqlite", reviewer_authorizer=authorize)
    gate = PolicyGate(store)
    policy = POLICY.model_copy(update={"approval_required_tools": []})
    report = drift_report(status="unknown", containment="review")
    decision = evaluate(gate, policy=policy, drift=report)
    assert decision.status == "approval_required"
    approved = approve(store, decision.binding)
    assert evaluate(gate, policy=policy, drift=report, approval_id=approved.approval_id).allowed
    report["window_id"] = "probe-2"
    assert evaluate(gate, policy=policy, drift=report,
                    approval_id=approved.approval_id).status == "approval_required"
    deny = policy.model_copy(update={"drift_action": "deny"})
    assert evaluate(gate, policy=deny, drift=report).status == "deny"
    assert evaluate(gate, policy=deny, drift=drift_report(policy_version="old")).status == "deny"
    store.close()


@pytest.mark.parametrize("changes", [{"status": []}, {"containment": {}},
                                     {"status": None}, {"containment": "unexpected"}])
def test_malformed_drift_report_denies_instead_of_raising(changes):
    assert evaluate(PolicyGate(), drift=drift_report(**changes)).status == "deny"


def test_real_drift_report_drives_gate_and_cannot_override_tool_policy():
    from pollard import MemoryStore

    from canopy.drift import DEFAULT_POLICY_PROBES, DriftMonitor

    expected = {probe.probe_id: probe.expected_decision for probe in DEFAULT_POLICY_PROBES}

    def synthetic_provider(request):
        return {"decision": expected[request["probe_id"]]}

    monitor = DriftMonitor(MemoryStore(), tenant_id="tenant-a", monitor_id="gate-integration",
                           policy_version="1")
    baseline = monitor.observe("base", synthetic_provider, provider_fingerprint="synthetic-v1",
                               policy_version="1", establish_baseline=True)
    policy = POLICY.model_copy(update={"approval_required_tools": []})
    assert evaluate(PolicyGate(), policy=policy, drift=baseline).status == "approval_required"
    stable = monitor.observe("current", synthetic_provider, provider_fingerprint="synthetic-v1",
                             policy_version="1")
    assert evaluate(PolicyGate(), policy=policy, drift=stable).allowed
    assert evaluate(PolicyGate(), policy=Policy(), drift=stable).status == "deny"
    violating = monitor.observe("bad", lambda _: {"decision": "allow"},
                                provider_fingerprint="synthetic-v1", policy_version="1")
    assert evaluate(PolicyGate(), policy=policy.model_copy(update={"drift_action": "allow"}),
                    drift=violating).status == "deny"
