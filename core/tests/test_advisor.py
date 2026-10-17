"""Offline analysis boundaries, source provenance, grouping and honest accounting."""

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3

import pytest

from canopy.advisor import analyze_runs, analyze_store
from canopy.advisor_contracts import AdvisorLimits, AdvisorReport, DecisionDescriptor
from canopy.contracts import (
    HandlerResult, Policy, RunConfig, RunResult, Step, StepResult, Task, Usage, utcnow,
)
from canopy.handlers import HandlerRegistry
from canopy.recording import ExecutionStore, RecordedRun, RecordingIntegrityError, canonical_json
from canopy.runtime import create_runtime


def record(number=1, *, tenant="team-a", category="business_decision", mode="baseline",
           status="completed", usage=None, annotated=True, payload="private customer one"):
    choices = ["billing", "technical"]
    step = Step(step_id="select", kind="model", handler="classify",
                inputs={"ticket": payload, "allowed_labels": choices})
    hints = {"category": category, "template_id": "triage", "template_version": "1",
             "instance_fields": ["ticket"], "choices": choices, "output_field": "label",
             "evidence_kind": "text"}
    task = Task(task_id=f"case-{number}", tenant_id=tenant, workflow_id="triage", steps=[step],
                metadata={"advisor": {"steps": {"select": hints}}} if annotated else {})
    config = RunConfig(mode=mode, model_fingerprint="model-v1", permissions=["execute:*"])
    step_result = StepResult(
        step_id="select", status=status, output={"label": "billing"},
        source_run_id="original" if status == "reused" else None,
        usage=usage if usage is not None else Usage(
            input_tokens=10, output_tokens=2, model_calls=1, elapsed_ms=15,
            elapsed_known=True, cost_usd=0.01,
        ),
    )
    now = utcnow()
    result = RunResult(
        run_id=f"run-{number}", task_id=task.task_id, tenant_id=tenant, mode=mode,
        status="completed" if status == "reused" else status,
        started_at=now, finished_at=now, steps=[step_result],
        usage=step_result.usage, policy_version="1", synthetic=True,
    )
    return RecordedRun(result.run_id, task, config, now, result, [step_result], "", "")


def test_declared_templates_group_instances_without_disclosing_prompts():
    records = [record(1), record(2, payload="private customer two", mode="governed")]
    before = [(canonical_json(item.task), canonical_json(item.result)) for item in records]
    report = analyze_runs(records, tenant_id="team-a")
    assert len(report.recommendations) == 1
    recommendation = report.recommendations[0]
    assert recommendation.finding.category == "business_decision"
    assert recommendation.grouping_basis == "declared_template"
    assert recommendation.observed.model_calls == 2
    assert recommendation.observed.input_tokens == 20
    assert recommendation.observed.cost_usd == 0.02
    assert recommendation.evaluation_status == "not_evaluated"
    assert recommendation.estimated_savings_usd is recommendation.measured_quality is None
    assert report.analysis.model_calls == report.analysis.tool_calls == 0
    assert "private customer" not in report.model_dump_json()
    assert [(canonical_json(item.task), canonical_json(item.result)) for item in records] == before
    assert AdvisorReport.model_validate_json(report.model_dump_json()) == report


def test_unannotated_calls_group_only_exact_inputs_and_ids_are_stable():
    first = record(1, annotated=False)
    same = record(2, annotated=False)
    changed = record(3, annotated=False, payload="different ticket")
    report = analyze_runs([first, same, changed], tenant_id="team-a")
    assert len(report.recommendations) == 2
    assert report.recommendations[0].observed.observations == 2
    again = analyze_runs([same, first], tenant_id="team-a")
    assert report.recommendations[0].recommendation_id == again.recommendations[0].recommendation_id


@pytest.mark.parametrize("change", ["model", "prompt", "policy", "choices", "shape", "handler"])
def test_material_operation_changes_split_groups(change):
    first, second = record(1), record(2)
    if change == "model":
        second.config.model_fingerprint = "model-v2"
    elif change == "prompt":
        second.config.prompt_version = "2"
    elif change == "policy":
        second.config.policy = Policy(version="2")
    elif change == "choices":
        second.task.steps[0].inputs["allowed_labels"].append("sales")
    elif change == "shape":
        second.task.steps[0].inputs["ticket"] = {"text": "new schema"}
    else:
        second.task.steps[0].handler_version = "2"
    report = analyze_runs([first, second], tenant_id="team-a")
    assert len(report.recommendations) == 2


def test_replay_and_reuse_do_not_duplicate_original_usage():
    original = record(1)
    replay = record(2, mode="replay")
    warm = record(3, status="reused", mode="governed",
                  usage=Usage(input_tokens=0, output_tokens=0, elapsed_known=True, cost_usd=0))
    report = analyze_runs([original, replay, warm, original], tenant_id="team-a")
    observed = report.recommendations[0].observed
    assert observed.model_calls == 1 and observed.input_tokens == 10
    assert observed.observations == 2 and observed.reused == 1
    assert report.analysis.skipped_replay_runs == 1
    assert report.analysis.skipped_duplicate_runs == 1


def test_unknown_and_failed_usage_preserves_unknown_totals():
    failed = record(2, status="failed", usage=Usage(model_calls=1))
    report = analyze_runs([record(1), failed], tenant_id="team-a")
    observed = report.recommendations[0].observed
    assert observed.model_calls == 2 and observed.failed == 1
    assert observed.input_tokens is observed.output_tokens is observed.cost_usd is observed.elapsed_ms is None
    assert observed.unknown_token_observations == observed.unknown_elapsed_observations == 1


def test_foreign_tenant_and_result_binding_fail_closed():
    with pytest.raises(ValueError, match="tenant"):
        analyze_runs([record(tenant="other")], tenant_id="team-a")
    item = record()
    item.result.task_id = "unrelated"
    with pytest.raises(ValueError, match="bindings"):
        analyze_runs([item], tenant_id="team-a")
    item = record(status="reused")
    with pytest.raises(ValueError, match="zero new model usage"):
        analyze_runs([item], tenant_id="team-a")


def test_scan_group_sample_and_size_limits_are_explicit():
    runs = [record(n, annotated=False, payload=str(n)) for n in range(5)]
    report = analyze_runs(runs, tenant_id="team-a", limits=AdvisorLimits(max_runs=3, max_groups=1))
    assert report.analysis.scanned_runs == 3
    assert set(report.analysis.limit_reasons) == {"max_runs", "max_groups"}
    assert report.analysis.skipped_group_observations == 2
    repeated = [record(n) for n in range(5)]
    report = analyze_runs(repeated, tenant_id="team-a", limits=AdvisorLimits(max_samples_per_group=2))
    assert len(report.recommendations[0].samples) == 2
    assert report.recommendations[0].sample_limit_reached
    report = analyze_runs([record(payload="x" * 1000)], tenant_id="team-a",
                          limits=AdvisorLimits(max_step_chars=256))
    assert report.analysis.skipped_oversized_steps == 1 and not report.recommendations
    assert report.analysis.limit_reasons == ["max_step_chars"]


def test_samples_preserve_distinct_declared_outcomes_when_possible():
    runs = [record(n) for n in range(5)]
    runs[-1].result.steps[0].output = {"label": "technical"}
    report = analyze_runs(runs, tenant_id="team-a", limits=AdvisorLimits(max_samples_per_group=2))
    assert {sample.selected_choice for sample in report.recommendations[0].samples} == {
        "billing", "technical",
    }


def test_run_budget_never_reads_or_verifies_an_extra_record():
    def source():
        yield record()
        raise AssertionError("read beyond the run budget")

    limits = AdvisorLimits(max_runs=1)
    report = analyze_runs(source(), tenant_id="team-a", limits=limits)
    assert report.analysis.scanned_runs == 1
    assert report.analysis.limit_reasons == ["max_runs"]

    class BoundedStore:
        def list_runs(self, tenant_id, *, limit, offset):
            assert (tenant_id, limit, offset) == ("team-a", 1, 0)
            return [record()]

        def verify_run(self, tenant_id, run_id):
            assert (tenant_id, run_id) == ("team-a", "run-1")
            return type("Verification", (), {"ok": True})()

    report = analyze_store(BoundedStore(), tenant_id="team-a", limits=limits)
    assert report.analysis.scanned_runs == 1
    assert report.source_integrity == "verified_recordings"


def test_elapsed_deadline_reports_partial_work(monkeypatch):
    ticks = iter([0, 0, 0, 2, 2])
    monkeypatch.setattr("canopy.advisor.perf_counter", lambda: next(ticks, 2))
    report = analyze_runs([record(1), record(2)], tenant_id="team-a",
                          limits=AdvisorLimits(max_seconds=1))
    assert report.analysis.scanned_runs == 1
    assert report.analysis.limit_reasons == ["max_seconds"]


def test_empty_and_incomplete_sources_are_not_optimization_success():
    item = record()
    item = replace(item, result=None, steps=[])
    report = analyze_runs([item], tenant_id="team-a")
    assert report.analysis.skipped_incomplete_runs == 1 and report.recommendations == []
    assert analyze_runs([], tenant_id="team-a").recommendations == []


def test_readonly_store_has_verified_provenance_and_no_dispatch_or_writes(tmp_path, monkeypatch):
    path = tmp_path / "runs.sqlite3"
    registry = HandlerRegistry()
    calls = []

    def classify(context):
        calls.append(context.run_id)
        return HandlerResult(output={"label": "billing"}, usage=Usage(input_tokens=0, output_tokens=0))

    registry.register("classify", classify, kind="model", synthetic=True)
    with closing(create_runtime(str(path), registry=registry)) as runtime:
        for item in [record(1), record(2), record(3, tenant="foreign")]:
            runtime.run_task(item.task, item.config)
    files = [path, path.with_name(path.name + ".pollard.sqlite3")]
    snapshots = [sha256(file.read_bytes()).hexdigest() for file in files]

    def forbidden(*args, **kwargs):
        raise AssertionError("offline analysis dispatched a handler")

    monkeypatch.setattr(HandlerRegistry, "resolve", forbidden)
    with ExecutionStore(path, read_only=True) as store:
        report = analyze_store(store, tenant_id="team-a")
    assert len(calls) == 3 and report.analysis.scanned_runs == 2
    assert report.source_integrity == "verified_recordings"
    assert report.recommendations[0].observed.synthetic_observations == 2
    assert [sha256(file.read_bytes()).hexdigest() for file in files] == snapshots

    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT run_id, task_json FROM canopy_runs WHERE tenant_id='team-a'").fetchone()
        task = json.loads(row[1])
        task["workflow_id"] = "tampered"
        connection.execute("UPDATE canopy_runs SET task_json=? WHERE run_id=?", (json.dumps(task), row[0]))
    with ExecutionStore(path, read_only=True) as store:
        with pytest.raises(RecordingIntegrityError):
            analyze_store(store, tenant_id="team-a")


@pytest.mark.parametrize("catalog", ["tools", "questions"])
def test_descriptor_cannot_hide_catalogs_as_instance_data(catalog):
    settings = {"template_id": "template", "template_version": "1", "instance_fields": [catalog]}
    with pytest.raises(ValueError, match="excluding catalogs"):
        DecisionDescriptor(**settings)
    with pytest.raises(ValueError, match="require a declared template"):
        DecisionDescriptor(instance_fields=["ticket"])


def test_cost_ranking_places_unknown_after_measured():
    first = record(1, annotated=False, payload="unknown", usage=Usage(model_calls=1))
    second = record(2, annotated=False, payload="measured")
    third = deepcopy(second)
    report = analyze_runs([first, second, third], tenant_id="team-a",
                          limits=AdvisorLimits(rank_by="cost_usd"))
    assert report.recommendations[0].observed.cost_usd == 0.01
    assert report.recommendations[1].observed.cost_usd is None
