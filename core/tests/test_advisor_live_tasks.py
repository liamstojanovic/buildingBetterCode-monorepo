"""Fresh, independent selector fixtures: no provider calls or SDK requirements."""

from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path

import pytest

from canopy._advisor_detection import inspect_step
from canopy.contracts import RunConfig, StepResult, Task

_spec = importlib.util.spec_from_file_location(
    "canopy_advisor_live_tasks", Path(__file__).parents[1] / "examples" / "advisor_live_tasks.py",
)
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)


def test_tasks_have_fresh_evidence_and_do_not_include_expected_answers():
    now = datetime(2026, 10, 17, tzinfo=UTC)
    tasks = fixtures.build_tasks(tenant_id="test-tenant", observed_at=now)
    assert set(tasks) == {"tool-selection", "next-step"}
    for task in tasks.values():
        assert task.tenant_id == "test-tenant"
        assert len(task.steps) == 3
        assert task.metadata["sample_data"] is True
        assert "expected_choices" not in task.model_dump_json()
        evidence = task.evidence[0]
        assert evidence.tenant_id == task.tenant_id
        assert evidence.observed_at == now
        assert evidence.expires_at == now + timedelta(hours=4)
        for step in task.steps:
            assert step.kind == "model" and step.handler == "model.generate"
            assert not step.side_effect and not step.depends_on
            assert step.evidence_ids == [evidence.evidence_id]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
@pytest.mark.parametrize("category, field", [("tool-selection", "tool"), ("next-step", "next_step")])
def test_structural_detection_recognizes_selector_without_category_hint(provider, category, field):
    task = fixtures.build_tasks()[category]
    expected = fixtures.expected_choices()[category]
    for step in task.steps:
        result = StepResult(
            step_id=step.step_id, status="completed",
            output={"provider": provider, "text": json.dumps(expected[step.step_id])},
        )
        found = inspect_step(task, step, result, RunConfig())
        assert found.finding.category == category.replace("-", "_")
        assert found.finding.strategy == "evaluate_openjev"
        assert found.finding.status == "needs_mapping"
        assert not found.finding.partial_substitution
        assert found.selected_choice == expected[step.step_id][field]
        assert found.selected_choice in found.finding.choices
        assert not any("argument" in blocker.lower() for blocker in found.finding.blockers)
        hints = task.metadata.get("advisor", {}).get("steps", {}).get(step.step_id, {})
        assert "category" not in hints and "choices" not in hints
        schema = step.inputs["output_schema"]
        assert schema["required"] == [field]
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {field}


def test_tool_arguments_are_prebound_and_not_returned_or_executed():
    task = fixtures.build_tasks()["tool-selection"]
    for step in task.steps:
        for tool in step.inputs["tools"]:
            assert tool["read_only"] is True
            assert tool["arguments"] and all(tool["arguments"].values())
        assert "arguments" not in step.inputs["output_schema"]["properties"]
        result = StepResult(step_id=step.step_id, status="completed", output={"tool": "knowledge_search"})
        without_hint = task.model_copy(update={"metadata": {"sample_data": True}})
        found = inspect_step(without_hint, step, result, RunConfig())
        assert found.finding.category == "tool_selection"
        assert any("source of tool arguments" in blocker for blocker in found.finding.blockers)


def test_unrequested_generated_reason_remains_partial_substitution():
    for task in fixtures.build_tasks().values():
        step = task.steps[0]
        field = next(iter(step.inputs["output_schema"]["properties"]))
        choice = step.inputs["output_schema"]["properties"][field]["enum"][0]
        result = StepResult(step_id=step.step_id, status="completed", output={
            "provider": "openai", "text": json.dumps({field: choice, "reason": "Generated text"}),
        })
        assert inspect_step(task, step, result, RunConfig()).finding.partial_substitution


def test_suite_writes_complete_contracts_and_separate_manifest_without_overwrite(tmp_path):
    first = fixtures.write_suite(tmp_path, tenant_id="test-tenant")
    original = {path: Path(path).read_bytes() for path in [
        first["manifest_path"], *first["task_files"].values(),
    ]}
    second = fixtures.write_suite(tmp_path, tenant_id="test-tenant")
    assert first["directory"] != second["directory"]
    assert first["model_calls_by_generator"] == 0
    assert first["comparison_calls_per_provider"] == 18
    assert first["expected_choices"] == fixtures.expected_choices()
    assert json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8")) == first
    for name, path in first["task_files"].items():
        task = Task.model_validate_json(Path(path).read_text(encoding="utf-8"))
        assert set(first["expected_choices"][name]) == {step.step_id for step in task.steps}
    assert original == {path: Path(path).read_bytes() for path in original}
    assert any("Unknown facts" in limitation for limitation in first["limitations"])


def test_cli_only_generates_files(tmp_path, capsys):
    assert fixtures.main(["--output-dir", str(tmp_path), "--tenant-id", "cli-tenant"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["tenant_id"] == "cli-tenant"
    assert Path(manifest["manifest_path"]).is_file()


@pytest.mark.parametrize("kwargs", [{"tenant_id": " "}, {"observed_at": datetime(2026, 10, 17)}])
def test_invalid_identity_or_naive_time_is_rejected(kwargs):
    with pytest.raises(ValueError):
        fixtures.build_tasks(**kwargs)
