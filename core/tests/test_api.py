"""HTTP boundary tests against the real runtime, SQLite stores, and policy gate."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from canopy.api import ServiceContext, create_app
from canopy.contracts import BudgetLimits, HandlerResult, Policy, RunConfig, Step, Task, Usage
from canopy.governance import ApprovalStore, ReviewerIdentity
from canopy.handlers import HandlerContext, HandlerRegistry
from canopy.runtime import Runtime, create_runtime


@dataclass
class Harness:
    runtime: Runtime
    context: ServiceContext
    calls: list[HandlerContext]
    clients: list[TestClient]

    def client(self, **changes: Any) -> TestClient:
        context = self.context.model_copy(deep=True, update=changes)
        client = TestClient(create_app(self.runtime, context=context))
        self.clients.append(client)
        return client


@pytest.fixture
def harness(tmp_path):
    calls: list[HandlerContext] = []

    def handler(context: HandlerContext) -> HandlerResult:
        calls.append(context)
        return HandlerResult(
            output={"value": context.inputs.get("value", "ok"), "synthetic": True},
            usage=Usage(input_tokens=4, output_tokens=2),
        )

    def reviewer_authorizer(context: Any) -> ReviewerIdentity | None:
        if not isinstance(context, ServiceContext):
            return None
        return ReviewerIdentity(
            tenant_id=context.tenant_id,
            reviewer_id=context.actor_id,
            permissions=context.permissions,
        )

    registry = HandlerRegistry()
    registry.register("api.synthetic", handler, synthetic=True, kind="model", cacheable=True)
    registry.register("api.synthetic_action", handler, synthetic=True, side_effect=True)
    runtime = create_runtime(
        str(tmp_path / "api.sqlite3"),
        registry=registry,
        reviewer_authorizer=reviewer_authorizer,
    )
    context = ServiceContext(
        tenant_id="tenant-a",
        actor_id="trusted-operator",
        permissions={"execute:api.synthetic", "execute:api.synthetic_action"},
        policy=Policy(
            version="trusted-policy-v1",
            allowed_tools=["api.synthetic_action"],
            approval_required_tools=["api.synthetic_action"],
        ),
        budget=BudgetLimits(max_tokens=100, max_steps=5),
        model_fingerprint="synthetic-api-v1",
        prompt_version="trusted-prompt-v1",
    )
    result = Harness(runtime, context, calls, [])
    yield result
    for client in result.clients:
        client.close()
    runtime.close()


def payload(*, tenant_id="tenant-a", action=False, value="ok") -> dict[str, Any]:
    return {
        "task": Task(
            task_id="api-test-task",
            tenant_id=tenant_id,
            workflow_id="api-test-workflow",
            steps=[
                Step(
                    step_id="work",
                    kind="tool" if action else "model",
                    handler="api.synthetic_action" if action else "api.synthetic",
                    side_effect=action,
                    inputs={"value": value},
                    estimated_usage=Usage(input_tokens=4, output_tokens=2),
                )
            ],
        ).model_dump(mode="json")
    }


def request_approval(client: TestClient) -> tuple[str, dict[str, Any]]:
    request = payload(action=True)
    response = client.post("/runs", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_approval"
    events = client.get(f"/runs/{response.json()['run_id']}/events").json()
    approval_id = next(
        event["payload"]["approval_id"]
        for event in events
        if event["payload"].get("approval_id")
    )
    return approval_id, request


def test_run_list_detail_and_ordered_paginated_events(harness):
    client = harness.client()
    response = client.post("/runs", json=payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert result["synthetic"] is True
    assert result["usage"]["model_calls"] == 1
    assert result["usage"]["input_tokens"] == 4
    assert result["usage"]["cost_usd"] is None
    run_id = result["run_id"]
    listed = client.get("/runs").json()
    assert [run["run_id"] for run in listed] == [run_id]
    assert listed[0]["status"] == "completed"
    assert client.get("/runs?offset=1").json() == []
    detail = client.get(f"/runs/{run_id}").json()
    assert detail["result"] == result
    assert detail["task"]["tenant_id"] == "tenant-a"
    assert "config" not in detail
    events = client.get(f"/runs/{run_id}/events").json()
    sequences = [event["sequence"] for event in events]
    assert sequences and sequences == sorted(set(sequences))
    first = client.get(f"/runs/{run_id}/events?limit=1").json()
    rest = client.get(f"/runs/{run_id}/events?after_sequence={first[0]['sequence']}").json()
    assert first + rest == events
    record = harness.runtime.store.get_run("tenant-a", run_id)
    assert record.config.actor_id == "trusted-operator"
    assert record.config.permissions == sorted(harness.context.permissions)
    assert record.config.policy == harness.context.policy
    assert record.config.budget == harness.context.budget
    assert record.config.model_fingerprint == "synthetic-api-v1"
    assert record.config.prompt_version == "trusted-prompt-v1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor_id", "admin"),
        ("tenant_id", "tenant-b"),
        ("permissions", ["*"]),
        ("policy", {"allowed_tools": ["api.synthetic_action"]}),
        ("budget", {"max_steps": 100000}),
        ("model_fingerprint", "attacker"),
        ("config", {"permissions": ["*"]}),
    ],
)
def test_client_authority_fields_are_rejected(harness, field, value):
    request = payload()
    request[field] = value
    assert harness.client().post("/runs", json=request).status_code == 422
    assert harness.calls == []
    assert harness.runtime.store.list_runs("tenant-a") == []


@pytest.mark.parametrize("mode", ["baseline", "governed"])
def test_metadata_and_headers_cannot_grant_execution_permission(harness, mode):
    request = payload()
    request["mode"] = mode
    request["task"]["metadata"] = {"permissions": ["*"], "role": "admin"}
    client = harness.client(permissions=frozenset())
    response = client.post(
        "/runs", json=request, headers={"X-Permissions": "*", "X-Actor-ID": "admin"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert harness.calls == []


def test_service_budget_admission_precedes_dispatch(harness):
    response = harness.client(budget=BudgetLimits(max_steps=0)).post("/runs", json=payload())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert harness.calls == []


def test_server_context_is_snapshotted_before_requests(harness):
    client = TestClient(create_app(harness.runtime, context=harness.context))
    harness.clients.append(client)
    harness.context.budget.max_steps = 0
    response = client.post("/runs", json=payload())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


def test_cross_tenant_run_read_events_replay_and_start_are_isolated(harness):
    other = harness.client(tenant_id="tenant-b")
    response = other.post("/runs", json=payload(tenant_id="tenant-b"))
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    client = harness.client()
    assert client.get("/runs").json() == []
    for resource in [f"/runs/{run_id}", f"/runs/{run_id}/events"]:
        response = client.get(resource)
        missing = client.get(resource.replace(run_id, "missing"))
        assert response.status_code == missing.status_code == 404
        assert response.json() == missing.json()
    replay_request = payload()
    replay_request.update(mode="replay", replay_run_id=run_id)
    assert client.post("/runs", json=replay_request).status_code == 404
    assert client.post("/runs", json=payload(tenant_id="tenant-b")).status_code == 403
    assert len(harness.calls) == 1


def test_replay_and_warm_run_make_no_new_handler_calls(harness):
    client = harness.client()
    request = payload()
    cold = client.post("/runs", json=request).json()
    warm = client.post("/runs", json=request).json()
    assert warm["status"] == "completed"
    assert warm["reused_steps"] == 1
    assert warm["usage"]["model_calls"] == 0
    request.update(mode="replay", replay_run_id=cold["run_id"])
    response = client.post("/runs", json=request)
    assert response.status_code == 200, response.text
    replay = response.json()
    assert replay["replay_source_run_id"] == cold["run_id"]
    assert replay["usage"]["model_calls"] == 0
    assert replay["usage"]["input_tokens"] == 0
    assert len(harness.calls) == 1
    assert client.get(f"/runs/{replay['run_id']}").json()["result"] == replay
    replay_events = client.get(f"/runs/{replay['run_id']}/events").json()
    historical = next(event for event in replay_events if event["event_type"] == "replay_loaded")
    assert historical["payload"]["historical_usage"] == cold["usage"]


def test_incomplete_run_is_visible_but_cannot_be_replayed(harness):
    request = payload()
    run_id = harness.runtime.store.create_run(Task.model_validate(request["task"]), RunConfig())
    client = harness.client()
    detail = client.get(f"/runs/{run_id}").json()
    assert detail["status"] == "running"
    assert detail["result"] is None
    request.update(mode="replay", replay_run_id=run_id)
    response = client.post("/runs", json=request)
    assert response.status_code == 409
    assert response.json() == {"detail": "Run is incomplete and cannot be replayed"}
    assert harness.calls == []


@pytest.mark.parametrize(
    "changes",
    [{"mode": "replay"}, {"replay_run_id": "source"}, {"mode": "surprise"}],
)
def test_invalid_replay_selection_is_rejected(harness, changes):
    request = payload()
    request.update(changes)
    assert harness.client().post("/runs", json=request).status_code == 422
    assert harness.calls == []


def test_unknown_remote_handler_name_is_not_executed(harness):
    request = payload()
    request["task"]["steps"][0]["handler"] = "os.system"
    assert harness.client().post("/runs", json=request).status_code == 422
    assert harness.calls == []


def test_approval_review_and_exact_single_use_dispatch(harness):
    client = harness.client()
    approval_id, request = request_approval(client)
    assert harness.calls == []
    review = client.get(f"/approvals/{approval_id}")
    assert review.status_code == 200
    assert review.json()["binding"]["tenant_id"] == "tenant-a"
    assert review.json()["status"] == "pending"
    assert len(review.json()["binding_digest"]) == 64
    decision_path = f"/approvals/{approval_id}/decision"
    assert client.post(decision_path, json={"approved": True}).status_code == 403
    reviewer = harness.client(actor_id="trusted-reviewer", permissions={"approvals:decide"})
    decision = reviewer.post(decision_path, json={"approved": True, "reason": "Reviewed"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["reviewer_id"] == "trusted-reviewer"
    request["approval_ids"] = {"work": approval_id}
    changed = deepcopy(request)
    changed["task"]["steps"][0]["inputs"]["value"] = "changed"
    mismatch = client.post("/runs", json=changed)
    assert mismatch.status_code == 200, mismatch.text
    assert mismatch.json()["status"] != "completed"
    assert harness.calls == []
    allowed = client.post("/runs", json=request)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "completed"
    consumed = client.get(f"/approvals/{approval_id}").json()
    assert consumed["consumed_at"] is not None
    repeated = client.post("/runs", json=request)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["status"] != "completed"
    assert len(harness.calls) == 1


def test_cross_tenant_approvals_are_indistinguishable_from_missing(harness):
    approval_id, _ = request_approval(harness.client())
    other = harness.client(tenant_id="tenant-b", permissions={"approvals:decide"})
    for resource in [approval_id, "missing"]:
        assert other.get(f"/approvals/{resource}").status_code == 404
        response = other.post(f"/approvals/{resource}/decision", json={"approved": True})
        assert response.status_code == 404
        assert response.json() == {"detail": "Approval not found"}
    assert harness.calls == []


def test_approval_request_cannot_spoof_reviewer_or_permissions(harness):
    approval_id, _ = request_approval(harness.client())
    reviewer = harness.client(permissions={"approvals:decide"})
    response = reviewer.post(
        f"/approvals/{approval_id}/decision",
        json={"approved": True, "reviewer_id": "admin", "permissions": ["*"]},
    )
    assert response.status_code == 422
    assert reviewer.get(f"/approvals/{approval_id}").json()["status"] == "pending"


def test_approval_store_without_authorizer_still_denies_trusted_permission(harness):
    approvals = ApprovalStore(":memory:")
    runtime = Runtime(
        registry=harness.runtime.registry,
        store=harness.runtime.store,
        cache=harness.runtime.cache,
        approvals=approvals,
    )
    context = harness.context.model_copy(update={
        "permissions": harness.context.permissions | {"approvals:decide"},
    })
    try:
        with TestClient(create_app(runtime, context=context)) as client:
            approval_id, _ = request_approval(client)
            response = client.post(
                f"/approvals/{approval_id}/decision", json={"approved": True}
            )
            assert response.status_code == 403
            assert client.get(f"/approvals/{approval_id}").json()["status"] == "pending"
    finally:
        approvals.close()


def test_conflicting_approval_decision_is_a_conflict(harness):
    approval_id, _ = request_approval(harness.client())
    reviewer = harness.client(permissions={"approvals:decide"})
    path = f"/approvals/{approval_id}/decision"
    assert reviewer.post(path, json={"approved": False}).status_code == 200
    assert reviewer.post(path, json={"approved": True}).status_code == 409


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "offset=-1"]
)
def test_invalid_run_pagination_is_rejected(harness, query):
    assert harness.client().get(f"/runs?{query}").status_code == 422
