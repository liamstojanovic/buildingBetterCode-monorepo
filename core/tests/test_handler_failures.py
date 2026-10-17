"""Measured provider failures remain failures while preserving paid usage."""

import pytest

from canopy.contracts import BudgetLimits, HandlerResult, RunConfig, Step, Task, Usage
from canopy.handlers import HandlerExecutionError, HandlerRegistry
from canopy.runtime import create_runtime


@pytest.fixture
def runtime(tmp_path):
    instance = create_runtime(str(tmp_path / "failures.sqlite3"), registry=HandlerRegistry())
    yield instance
    instance.close()


def task_with(*steps):
    return Task(task_id="failure-task", tenant_id="tenant", workflow_id="failure-workflow",
                steps=list(steps))


def model_step(step_id, **kwargs):
    return Step(step_id=step_id, kind="model", handler="model", **kwargs)


def test_execution_error_detaches_usage_and_diagnostic_output():
    usage = Usage(input_tokens=11, output_tokens=7, cost_usd=0.02)
    output = {"status": "incomplete", "details": {"reason": "max_output_tokens"}}
    error = HandlerExecutionError("model response incomplete", usage=usage, output=output)
    usage.input_tokens = 999
    output["details"]["reason"] = "changed"
    assert error.usage.input_tokens == 11
    assert error.usage.tokens_known
    assert error.output["details"]["reason"] == "max_output_tokens"
    assert str(error) == "model response incomplete"


@pytest.mark.parametrize("mode", ["baseline", "governed"])
def test_measured_failure_settles_actual_tokens_and_contains_budget_overrun(runtime, mode):
    calls = []

    def provider(context):
        calls.append(context.step.step_id)
        raise HandlerExecutionError(
            "output limit reached", usage=Usage(input_tokens=11, output_tokens=7, cost_usd=0.02),
            output={"status": "incomplete"},
        )

    runtime.registry.register("model", provider, kind="model", cacheable=True)
    estimate = Usage(input_tokens=1, output_tokens=1)
    task = task_with(model_step("first", estimated_usage=estimate),
                     model_step("second", estimated_usage=estimate))
    config = RunConfig(mode=mode, permissions=["execute:*"], budget=BudgetLimits(max_tokens=10))
    result = runtime.run_task(task, config)
    assert calls == ["first"]
    assert [step.status for step in result.steps] == ["failed", "blocked"]
    assert result.status == "failed"
    assert result.usage.tokens_known
    assert result.usage.input_tokens == 11 and result.usage.output_tokens == 7
    assert result.usage.model_calls == 1 and result.usage.cost_usd == pytest.approx(0.02)
    assert "output limit reached" in result.reason and "limit_exceeded" in result.reason
    settlements = [event for event in runtime.store.events(task.tenant_id, result.run_id)
                   if event.event_type == "budget_settled"]
    assert len(settlements) == 1
    assert settlements[0].payload["usage"]["input_tokens"] == 11
    assert settlements[0].payload["usage"]["tokens_known"]
    assert not settlements[0].payload["ok"]


def test_failure_is_not_cached_or_passed_to_descendants_and_diagnostics_persist(runtime):
    calls = []
    diagnostic = {"status": "incomplete", "reason": "max_output_tokens", "text": "partial"}

    def provider(context):
        calls.append(context.step.step_id)
        if context.step.step_id == "parent":
            raise HandlerExecutionError(
                "response incomplete", usage=Usage(input_tokens=5, output_tokens=3),
                output=diagnostic,
            )
        return HandlerResult(output="should not execute")

    runtime.registry.register("model", provider, kind="model", cacheable=True)
    task = task_with(model_step("parent"), model_step("child", depends_on=["parent"]))
    config = RunConfig(permissions=["execute:*"])
    for _ in range(2):
        result = runtime.run_task(task, config)
        assert [step.status for step in result.steps] == ["failed", "blocked"]
        assert result.output == {} and result.reused_steps == 0
        assert result.steps[0].cache_key is None
        assert result.steps[0].output == diagnostic
        assert runtime.cache.lookup(task, task.steps[0], config, {}) is None
        saved = runtime.store.get_run(task.tenant_id, result.run_id).result
        assert saved.steps[0].output == diagnostic
        assert saved.steps[0].usage.input_tokens == 5
        assert saved.steps[0].usage.output_tokens == 3
        assert saved.steps[0].usage.elapsed_known
        assert runtime.store.verify_run(task.tenant_id, result.run_id).ok
    assert calls == ["parent", "parent"]


@pytest.mark.parametrize("structured", [False, True])
def test_transport_failure_without_usage_stays_unknown(runtime, structured):
    def provider(_context):
        if structured:
            raise HandlerExecutionError("transport failed")
        raise ConnectionError("transport failed")

    runtime.registry.register("model", provider, kind="model")
    task = task_with(model_step("call", estimated_usage=Usage(input_tokens=4, output_tokens=2)))
    config = RunConfig(permissions=["execute:*"], budget=BudgetLimits(max_tokens=20))
    result = runtime.run_task(task, config)
    step = result.steps[0]
    assert result.status == step.status == "failed"
    assert not step.usage.tokens_known and not result.usage.tokens_known
    assert step.usage.input_tokens == step.usage.output_tokens == 0
    assert result.usage.model_calls == 1 and result.usage.cost_usd is None
    assert step.usage.elapsed_known and step.output is None
    assert "transport failed" in step.reason and "unknown_actual" in step.reason
    saved = runtime.store.get_run(task.tenant_id, result.run_id).result
    assert not saved.steps[0].usage.tokens_known and not saved.usage.tokens_known


def test_unserializable_diagnostics_do_not_lose_measured_usage(runtime):
    def provider(_context):
        raise HandlerExecutionError(
            "invalid response", usage=Usage(input_tokens=2, output_tokens=1),
            output={"unsupported": {"a", "b"}},
        )

    runtime.registry.register("model", provider, kind="model")
    task = task_with(model_step("call"))
    result = runtime.run_task(task, RunConfig(permissions=["execute:*"]))
    assert result.status == "failed" and result.steps[0].output is None
    assert result.usage.tokens_known
    assert result.usage.input_tokens == 2 and result.usage.output_tokens == 1
    assert "diagnostic output could not be recorded as JSON" in result.reason
