"""Runtime integration tests use real component stores and local Python handlers."""

from datetime import timedelta

import pytest
from pollard import MemoryStore, verify

from canopy.contracts import BudgetLimits, HandlerResult, Policy, RunConfig, Step, Task, Usage, utcnow
from canopy.drift import DriftMonitor
from canopy.governance import ReviewerIdentity
from canopy.handlers import HandlerRegistry, synthetic_task
from canopy.recording import RunNotFound
from canopy.runtime import create_runtime, run_task


@pytest.fixture
def runtime(tmp_path):
    instance = create_runtime(str(tmp_path / "runtime.sqlite3"))
    yield instance
    instance.close()


@pytest.fixture
def config():
    return RunConfig(
        permissions=["execute:*"],
        policy=Policy(allowed_tools=["synthetic.catalog", "synthetic.greeting", "echo", "effect"]),
        model_fingerprint="synthetic-python-fixture-v1",
    )


def task_for(*steps):
    return Task(task_id="task", tenant_id="tenant", workflow_id="workflow", steps=list(steps))


def echo_step(step_id="a", **kwargs):
    return Step(step_id=step_id, kind="tool", handler="echo", **kwargs)


def register_echo(runtime, calls, *, usage=None, **registration):
    registry = HandlerRegistry()

    def echo(context):
        calls.append(context.step.step_id)
        return HandlerResult(
            output={"inputs": context.inputs, "dependencies": context.dependencies},
            usage=usage or Usage(input_tokens=3, output_tokens=2, cost_usd=0.01),
        )

    registry.register("echo", echo, **registration)
    runtime.registry = registry


def test_six_synthetic_scenarios_with_real_persistence(runtime, config):
    task = synthetic_task()
    baseline = run_task(task, config.model_copy(update={"mode": "baseline"}), runtime=runtime)
    cold = runtime.run_task(task, config)
    warm = runtime.run_task(task, config)
    changed = runtime.run_task(synthetic_task(source_version="2", source_amount_minor=3100), config)
    refused = runtime.run_task(task, config.model_copy(update={
        "cache_enabled": False, "budget": BudgetLimits(max_steps=0),
    }))
    replay = runtime.run_task(task, config.model_copy(update={
        "mode": "replay", "replay_run_id": cold.run_id,
    }))

    assert baseline.output == cold.output == warm.output == replay.output
    assert baseline.reused_steps == cold.reused_steps == 0
    assert warm.reused_steps == 3 and warm.invalidated_steps == 0
    assert changed.reused_steps == 1 and changed.invalidated_steps == 2
    assert changed.output["read_catalog"]["amount_minor"] == 3100
    assert refused.status == "blocked"
    for result in (warm, refused, replay):
        assert result.usage.model_calls == result.usage.tool_calls == 0
        assert result.usage.cost_usd == 0 and result.usage.energy_j is None
    for result in (baseline, cold):
        assert result.usage.model_calls == 1 and result.usage.tool_calls == 2
        assert result.usage.cost_usd is None and result.usage.energy_j is None
        assert result.synthetic
    for result in (baseline, cold, warm, changed, refused, replay):
        assert runtime.store.verify_run(task.tenant_id, result.run_id).ok
    assert runtime.store.get_run(task.tenant_id, replay.run_id).result == replay
    replay_event = next(event for event in runtime.store.events(task.tenant_id, replay.run_id)
                        if event.event_type == "replay_loaded")
    assert replay_event.payload["historical_usage"]["model_calls"] == 1


def test_dag_dispatch_and_checks_precede_each_handler(runtime, config):
    calls = []
    register_echo(runtime, calls)
    task = task_for(echo_step("child", depends_on=["parent"]), echo_step("parent", inputs={"x": 2}))
    result = runtime.run_task(task, config)
    assert calls == ["parent", "child"]
    assert result.output["child"]["dependencies"]["parent"]["inputs"] == {"x": 2}
    assert result.usage.input_tokens == 6 and result.usage.output_tokens == 4
    assert result.usage.tool_calls == 2 and result.usage.model_calls == 0
    assert result.usage.cost_usd == pytest.approx(0.02)
    assert result.usage.elapsed_ms > 0
    assert result.usage.elapsed_known
    assert all(step.usage.elapsed_known and step.usage.elapsed_ms > 0 for step in result.steps)
    assert all(step.usage.elapsed_known for step in runtime.store.get_run(
        task.tenant_id, result.run_id,
    ).result.steps)
    events = runtime.store.events(task.tenant_id, result.run_id)
    assert [event.sequence for event in events] == list(range(len(events)))
    for step in task.steps:
        types = [event.event_type for event in events if event.step_id == step.step_id]
        assert types.index("policy_checked") < types.index("budget_admitted")
        assert types.index("budget_admitted") < types.index("step_dispatched")
        assert types.index("step_dispatched") < types.index("budget_settled")


@pytest.mark.parametrize("defect", ["unknown", "cycle", "version", "side_effect", "kind"])
def test_preflight_rejects_entire_task_before_any_dispatch(runtime, config, defect):
    calls = []
    register_echo(runtime, calls)
    task = task_for(echo_step("first"), echo_step("second"))
    if defect == "unknown":
        task.steps[1].handler = "os.system"
    elif defect == "cycle":
        task.steps[0].depends_on = ["second"]
        task.steps[1].depends_on = ["first"]
    elif defect == "version":
        task.steps[1].handler_version = "wrong"
    elif defect == "side_effect":
        task.steps[1].side_effect = True
    else:
        task.steps[1].kind = "model"
    with pytest.raises(ValueError):
        runtime.run_task(task, config)
    assert not calls
    assert runtime.store.list_runs(task.tenant_id) == []


@pytest.mark.parametrize("mode", ["baseline", "governed"])
def test_permissions_and_budget_apply_in_both_execution_modes(runtime, config, mode):
    calls = []
    register_echo(runtime, calls)
    task = task_for(echo_step())
    denied = runtime.run_task(task, config.model_copy(update={"permissions": [], "mode": mode}))
    refused = runtime.run_task(task, config.model_copy(update={
        "mode": mode, "budget": BudgetLimits(max_steps=0),
    }))
    assert denied.status == refused.status == "blocked"
    assert not calls


def test_expired_evidence_blocks_descendants_but_not_independent_work(runtime, config):
    task = synthetic_task(observed_at=utcnow() - timedelta(hours=2))
    result = runtime.run_task(task, config)
    statuses = {item.step_id: item.status for item in result.steps}
    assert statuses == {"read_catalog": "blocked", "summarize_quote": "blocked", "greeting": "completed"}
    assert result.usage.model_calls == 0 and result.usage.tool_calls == 1


def test_overrun_settles_actuals_then_blocks_future_calls(runtime, config):
    calls = []
    register_echo(runtime, calls)
    estimate = Usage(input_tokens=1, output_tokens=0)
    task = task_for(echo_step("a", estimated_usage=estimate), echo_step("b", estimated_usage=estimate))
    result = runtime.run_task(task, config.model_copy(update={"budget": BudgetLimits(max_tokens=2)}))
    assert calls == ["a"]
    assert result.status == "failed" and result.steps[0].status == "failed"
    assert result.steps[1].status == "blocked"
    assert result.usage.input_tokens == 3 and result.usage.output_tokens == 2
    assert result.usage.tool_calls == 1
    assert "limit_exceeded" in result.reason


def test_unknown_token_estimate_stays_unknown_after_json_round_trip(runtime, config):
    calls = []
    register_echo(runtime, calls)
    task = Task.model_validate_json(task_for(echo_step()).model_dump_json())
    result = runtime.run_task(task, config.model_copy(update={"budget": BudgetLimits(max_tokens=20)}))
    assert result.status == "blocked" and not calls
    assert "known tokens estimate" in result.reason


def test_unknown_actuals_fail_capped_step_and_contain_later_calls(runtime, config):
    calls = []
    register_echo(runtime, calls, usage=Usage())
    task = task_for(*[echo_step(name, estimated_usage=Usage(input_tokens=1, output_tokens=0))
                      for name in ("a", "b")])
    result = runtime.run_task(task, config.model_copy(update={"budget": BudgetLimits(max_tokens=20)}))
    assert calls == ["a"]
    assert result.status == "failed" and result.steps[1].status == "blocked"
    assert result.usage.tool_calls == 1 and not result.usage.tokens_known
    assert "unknown_actual" in result.reason


def test_handler_exception_records_unknown_usage_and_no_retry(runtime, config):
    calls = []

    def broken(context):
        calls.append(context.step.step_id)
        raise RuntimeError("synthetic provider failure")

    registry = HandlerRegistry()
    registry.register("effect", broken, side_effect=True)
    runtime.registry = registry
    task = task_for(Step(step_id="effect", kind="tool", handler="effect", side_effect=True))
    result = runtime.run_task(task, config)
    assert calls == ["effect"] and result.status == "failed"
    assert result.usage.tool_calls == 1 and result.usage.cost_usd is None
    assert "outcome may be unknown" in result.reason
    assert runtime.store.get_run(task.tenant_id, result.run_id).result.status == "failed"


def test_bad_handler_usage_is_terminal_failure_not_invalid_client_request(runtime, config):
    def broken(_context):
        result = HandlerResult(output="returned", usage=Usage(input_tokens=1, output_tokens=1))
        result.usage.cost_usd = -1
        return result

    registry = HandlerRegistry()
    registry.register("echo", broken)
    runtime.registry = registry
    result = runtime.run_task(task_for(echo_step()), config)
    assert result.status == "failed" and result.usage.tool_calls == 1
    assert result.usage.cost_usd is None
    assert runtime.store.get_run(result.tenant_id, result.run_id).result is not None


def test_approved_side_effect_consumes_exact_binding_once(tmp_path, config):
    calls, reviewer_context = [], object()

    def authorizer(context):
        if context is reviewer_context:
            return ReviewerIdentity("tenant", "reviewer", frozenset({"approvals:decide"}))
        return None

    registry = HandlerRegistry()
    registry.register("effect", lambda context: (
        calls.append(context.inputs), HandlerResult(output="sent", usage=Usage(input_tokens=0, output_tokens=0))
    )[1], side_effect=True)
    runtime = create_runtime(str(tmp_path / "approval.sqlite3"), registry=registry,
                             reviewer_authorizer=authorizer)
    try:
        task = task_for(Step(step_id="pay", kind="tool", handler="effect", side_effect=True,
                             inputs={"amount_minor": 100}))
        config.policy.approval_required_tools = ["effect"]
        pending = runtime.run_task(task, config)
        assert pending.status == "awaiting_approval" and not calls
        approval_id = next(event.payload["approval_id"] for event in runtime.store.events(
            "tenant", pending.run_id) if event.event_type == "approval_required")
        runtime.approvals.decide(approval_id, reviewer_context=reviewer_context, approved=True)
        for update in (
            {"model_fingerprint": "another-provider"}, {"prompt_version": "another-prompt"},
            {"permissions": ["execute:effect"]}, {"mode": "baseline"},
        ):
            revised = runtime.run_task(
                task, config.model_copy(update=update), approval_ids={"pay": approval_id}
            )
            assert revised.status == "awaiting_approval" and not calls
        completed = runtime.run_task(task, config, approval_ids={"pay": approval_id})
        repeated = runtime.run_task(task, config, approval_ids={"pay": approval_id})
        assert completed.status == "completed" and repeated.status == "awaiting_approval"
        assert len(calls) == 1 and completed.reused_steps == 0
        assert runtime.approvals.get(approval_id, tenant_id="tenant").consumed_at is not None
    finally:
        runtime.close()


def test_replay_after_reopen_uses_no_registry_and_keeps_historical_usage(tmp_path, config):
    path = str(tmp_path / "reopen.sqlite3")
    runtime = create_runtime(path)
    task = synthetic_task()
    original = runtime.run_task(task, config)
    nodes = runtime.budget_trace(task.tenant_id, original.run_id)
    root_id = next(node.id for node in nodes if node.parent is None)
    runtime.close()
    reopened = create_runtime(path, registry=HandlerRegistry())
    try:
        restored = reopened.budget_trace(task.tenant_id, original.run_id)
        assert {node.id for node in nodes} == {node.id for node in restored}
        trace = MemoryStore()
        for node in restored:
            trace.put(node)
        assert all(verify(trace, node.id).ok for node in restored)
        assert trace.exists(root_id)
        replay = reopened.run_task(task, config.model_copy(update={
            "mode": "replay", "replay_run_id": original.run_id,
            "policy": Policy(version="inspection-policy"),
        }))
        assert replay.output == original.output and replay.usage.model_calls == 0
        assert replay.policy_version == original.policy_version
        replay_record = reopened.store.get_run(task.tenant_id, replay.run_id)
        assert replay_record.config.policy.version == original.policy_version
        replay_event = next(event for event in reopened.store.events(task.tenant_id, replay.run_id)
                            if event.event_type == "replay_loaded")
        assert replay_event.payload["inspection_policy_version"] == "inspection-policy"
        assert replay_event.payload["historical_policy_version"] == original.policy_version
        assert reopened.store.get_run(task.tenant_id, original.run_id).result.usage.model_calls == 1
        assert reopened.budget_trace(task.tenant_id, replay.run_id) == []
        with pytest.raises(RunNotFound):
            reopened.budget_trace("foreign", original.run_id)
    finally:
        reopened.close()


def test_replay_rejects_changed_task_and_tenant(runtime, config):
    task = synthetic_task()
    original = runtime.run_task(task, config)
    replay_config = config.model_copy(update={"mode": "replay", "replay_run_id": original.run_id})
    changed = task.model_copy(deep=True)
    changed.steps[0].inputs["sku"] = "changed"
    with pytest.raises(ValueError, match="match the recorded task"):
        runtime.run_task(changed, replay_config)
    with pytest.raises(ValueError, match="trusted execution"):
        runtime.run_task(task, config, tenant_id="foreign")


def test_drift_containment_gates_cached_outputs_without_new_provider_calls(runtime, config):
    task = synthetic_task()
    assert runtime.run_task(task, config).status == "completed"
    monitor = DriftMonitor(MemoryStore(), tenant_id=task.tenant_id, monitor_id="runtime-test",
                           policy_version=config.policy.version, min_samples=2)
    probe_calls = []

    def violating_provider(probe):
        probe_calls.append(probe)
        return {"decision": "allow"}

    report = monitor.observe("violation", violating_provider,
                             provider_fingerprint=config.model_fingerprint,
                             policy_version=config.policy.version, samples=2)
    assert report.containment == "deny"
    runtime.drift = monitor
    result = runtime.run_task(task, config)
    assert result.status == "blocked" and result.reused_steps == 0
    assert result.usage.model_calls == result.usage.tool_calls == 0
    assert len(probe_calls) == 1


def test_registry_rejects_remote_names_and_async_implementations():
    registry = HandlerRegistry()

    async def asynchronous(_context):
        return HandlerResult(output=None)

    with pytest.raises(ValueError, match="synchronous"):
        registry.register("async", asynchronous)
    with pytest.raises(ValueError, match="identifier"):
        registry.register("https://remote/function", lambda _context: None)
    registry.register("valid", lambda _context: HandlerResult(output=None))
    with pytest.raises(ValueError, match="already registered"):
        registry.register("valid", lambda _context: None)


def test_reuse_requires_explicit_cacheable_registration(runtime, config):
    calls = []
    register_echo(runtime, calls)
    task = task_for(echo_step())
    runtime.run_task(task, config)
    second = runtime.run_task(task, config)
    assert calls == ["a", "a"] and second.reused_steps == 0
    register_echo(runtime, calls, cacheable=True)
    runtime.run_task(task, config)
    fourth = runtime.run_task(task, config)
    assert calls == ["a", "a", "a"] and fourth.reused_steps == 1
    denied = runtime.run_task(task, config.model_copy(update={"permissions": []}))
    assert denied.status == "blocked" and len(calls) == 3


def test_corrupt_cache_fails_closed_with_terminal_result(runtime, config):
    import sqlite3

    task = synthetic_task()
    runtime.run_task(task, config)
    with sqlite3.connect(runtime.store.path) as connection:
        connection.execute("UPDATE canopy_cache_entries SET output_json='null'")
    result = runtime.run_task(task, config)
    assert result.status == "failed" and result.reused_steps == 0
    assert result.usage.model_calls == result.usage.tool_calls == 0
    assert runtime.store.get_run(task.tenant_id, result.run_id).result == result


def test_handler_output_types_are_identical_before_and_after_reuse(runtime, config):
    observed = utcnow()
    registry = HandlerRegistry()
    registry.register("echo", lambda _context: HandlerResult(
        output={"observed": observed}, usage=Usage(input_tokens=0, output_tokens=0)
    ), cacheable=True)
    runtime.registry = registry
    task = task_for(echo_step("a"), echo_step("b", depends_on=["a"]))
    cold = runtime.run_task(task, config)
    warm = runtime.run_task(task, config)
    assert cold.status == warm.status == "completed"
    assert cold.output == warm.output and isinstance(cold.output["a"]["observed"], str)
    assert warm.reused_steps == 2
