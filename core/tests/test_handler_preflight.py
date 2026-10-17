"""Handler-specific validation runs before any dispatch or reusable result."""

from datetime import timedelta

import pytest

from canopy.contracts import Evidence, HandlerResult, RunConfig, Step, Task, Usage, utcnow
from canopy.handlers import HandlerRegistry
from canopy.runtime import create_runtime


@pytest.fixture
def runtime(tmp_path):
    instance = create_runtime(str(tmp_path / "preflight.sqlite3"), registry=HandlerRegistry())
    yield instance
    instance.close()


def task_with(*steps, evidence=None):
    return Task(task_id="preflight-task", tenant_id="tenant", workflow_id="workflow",
                steps=list(steps), evidence=evidence or [], metadata={"nested": {"value": 1}})


def model_step(step_id, **kwargs):
    return Step(step_id=step_id, kind="model", handler="model", **kwargs)


def test_preflight_runs_before_cold_and_warm_cache_and_can_reject_a_warm_run(runtime):
    events = []
    permit = True

    def preflight(task, step, config):
        events.append("preflight")
        assert task.steps[0].step_id == step.step_id
        assert config.mode == "governed"
        if not permit:
            raise ValueError("declared validity no longer matches")

    def handler(_context):
        events.append("dispatch")
        return HandlerResult(output="completed", usage=Usage(input_tokens=2, output_tokens=1))

    runtime.registry.register("model", handler, kind="model", cacheable=True, preflight=preflight)
    task = task_with(model_step("one"))
    config = RunConfig(permissions=["execute:*"])
    cold = runtime.run_task(task, config)
    warm = runtime.run_task(task, config)
    assert cold.status == warm.status == "completed"
    assert warm.reused_steps == 1
    assert events == ["preflight", "dispatch", "preflight"]
    permit = False
    with pytest.raises(ValueError, match="declared validity"):
        runtime.run_task(task, config)
    assert events == ["preflight", "dispatch", "preflight", "preflight"]
    assert len(runtime.store.list_runs(task.tenant_id)) == 2


@pytest.mark.parametrize("mode", ["baseline", "governed"])
def test_mismatched_evidence_rejects_whole_task_before_an_earlier_step_dispatch(runtime, mode):
    calls = []
    observed = utcnow()
    evidence = Evidence(evidence_id="state", tenant_id="tenant", source="fixture://state",
                        version="1", observed_at=observed,
                        expires_at=observed + timedelta(minutes=5))

    def validate_state(task, step, _config):
        if step.step_id == "decision":
            declared = next(item for item in task.evidence if item.evidence_id == "state")
            if "state" not in step.evidence_ids or step.inputs["observed_at"] != declared.observed_at:
                raise ValueError("native state timestamp must match declared evidence")

    def handler(context):
        calls.append(context.step.step_id)
        return HandlerResult(output="unexpected")

    runtime.registry.register("model", handler, kind="model", preflight=validate_state)
    task = task_with(
        model_step("first"),
        model_step("decision", depends_on=["first"], evidence_ids=["state"],
                   inputs={"observed_at": observed - timedelta(minutes=1)}),
        evidence=[evidence],
    )
    with pytest.raises(ValueError, match="timestamp must match"):
        runtime.run_task(task, RunConfig(mode=mode, permissions=["execute:*"]))
    assert not calls
    assert runtime.store.list_runs(task.tenant_id) == []


def test_preflight_receives_detached_snapshots_for_each_step_and_config(runtime):
    checked = []

    def preflight(task, step, config):
        assert task.metadata == {"nested": {"value": 1}}
        assert step.inputs == {"nested": {"value": 2}}
        assert config.permissions == ["execute:*"]
        checked.append(step.step_id)
        task.metadata["nested"]["value"] = 999
        task.steps[0].inputs["nested"]["value"] = 888
        step.inputs["nested"]["value"] = 777
        config.permissions.clear()
        config.policy.allowed_tools.append("unexpected")

    def handler(context):
        assert context.task.metadata == {"nested": {"value": 1}}
        assert context.inputs == {"nested": {"value": 2}}
        assert context.config.permissions == ["execute:*"]
        assert context.config.policy.allowed_tools == []
        return HandlerResult(output="original", usage=Usage(input_tokens=1, output_tokens=1))

    runtime.registry.register("model", handler, kind="model", preflight=preflight)
    task = task_with(model_step("first", inputs={"nested": {"value": 2}}),
                     model_step("second", inputs={"nested": {"value": 2}}))
    config = RunConfig(permissions=["execute:*"])
    original_task, original_config = task.model_copy(deep=True), config.model_copy(deep=True)
    result = runtime.run_task(task, config)
    assert result.status == "completed" and checked == ["first", "second"]
    assert task == original_task and config == original_config


def test_registry_rejects_nonsynchronous_preflight_functions_and_callable_objects():
    async def asynchronous(_task, _step, _config):
        pass

    def generator(_task, _step, _config):
        yield None

    async def async_generator(_task, _step, _config):
        yield None

    class AsyncCallable:
        async def __call__(self, _task, _step, _config):
            pass

    class GeneratorCallable:
        def __call__(self, _task, _step, _config):
            yield None

    registry = HandlerRegistry()
    for invalid in (asynchronous, generator, async_generator, AsyncCallable(), GeneratorCallable()):
        with pytest.raises(ValueError, match="preflight must be a synchronous callable"):
            registry.register("model", lambda _context: HandlerResult(output=None),
                              preflight=invalid)
    with pytest.raises(ValueError, match="preflight must be callable"):
        registry.register("model", lambda _context: HandlerResult(output=None), preflight="invalid")
    assert registry.registered_names() == ()
