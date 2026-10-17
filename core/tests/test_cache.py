"""Cache behavior tests using explicitly synthetic tasks and usage measurements."""

import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from canopy.cache import CacheIntegrityError, CacheValidationError, ResultCache
from canopy.contracts import Evidence, Policy, RunConfig, Step, StepResult, Task, Usage, utcnow
from canopy.recording import ExecutionStore


@pytest.fixture
def workload():
    observed = utcnow() - timedelta(minutes=1)
    source = Step(
        step_id="source",
        kind="tool",
        handler="synthetic.lookup",
        inputs={"record_id": "invoice-7"},
        evidence_ids=["invoice"],
    )
    step = Step(
        step_id="summary",
        kind="model",
        handler="synthetic.summarize",
        inputs={"format": "brief"},
        depends_on=["source"],
    )
    task = Task(
        task_id="task-a",
        tenant_id="tenant-a",
        workflow_id="synthetic-invoice-review",
        steps=[source, step],
        evidence=[
            Evidence(
                evidence_id="invoice",
                tenant_id="tenant-a",
                source="synthetic-fixture",
                version="1",
                observed_at=observed,
                expires_at=observed + timedelta(days=1),
                data={"amount_minor": 2100, "currency": "USD"},
            )
        ],
        metadata={"synthetic": True},
    )
    config = RunConfig(
        actor_id="reader-a",
        permissions=["invoice:read", "summary:write"],
        policy=Policy(allowed_tools=["synthetic.lookup"]),
        model_fingerprint="synthetic-model-v1",
    )
    dependencies = {"source": {"amount_minor": 2100}}
    result = StepResult(
        step_id="summary",
        status="completed",
        output={"summary": "Synthetic invoice totals 2100 minor units."},
        usage=Usage(input_tokens=50, output_tokens=10, model_calls=1, cost_usd=0.001),
    )
    return task, step, config, dependencies, result


def test_persists_reopens_and_reuses_equivalent_new_task_id(tmp_path, workload):
    task, step, config, dependencies, result = workload
    path = tmp_path / "shared.sqlite3"
    with ResultCache(path) as cache:
        assert cache.lookup(task, step, config, dependencies) is None
        stored = cache.put(task, step, config, dependencies, result, source_run_id="run-original")
        assert stored is not None
    equivalent = task.model_copy(update={"task_id": "task-b"})
    with ResultCache(path) as cache:
        hit = cache.lookup(equivalent, step, config, dependencies)
        assert hit == stored
        assert hit.source_run_id == "run-original"
        assert hit.historical_usage == result.usage
        reused = hit.as_step_result("summary")
        assert reused.status == "reused"
        assert reused.cache_key == stored.key
        assert reused.source_run_id == "run-original"
        assert reused.usage.model_calls == reused.usage.input_tokens == reused.usage.output_tokens == 0
        assert reused.usage.tool_calls == 0
        assert reused.usage.cost_usd == 0
        assert reused.usage.energy_j is None
        assert reused.usage.tokens_known is True
        reused.output["summary"] = "changed by caller"
        assert cache.lookup(equivalent, step, config, dependencies).output == result.output


@pytest.mark.parametrize(
    "changed",
    [
        "inputs",
        "dependency_output",
        "evidence_data",
        "evidence_version",
        "evidence_source",
        "evidence_observed_at",
        "evidence_expires_at",
        "evidence_quality",
        "evidence_half_life",
        "ancestor_handler",
        "ancestor_inputs",
        "handler_version",
        "handler",
        "model",
        "prompt",
        "actor",
        "permissions",
        "policy_version",
        "policy_content",
        "workflow",
        "metadata",
    ],
)
def test_identity_binds_execution_context(workload, changed):
    task, step, config, dependencies, result = workload
    with ResultCache() as cache:
        original_key = cache.key(task, step, config, dependencies)
        cache.put(task, step, config, dependencies, result, source_run_id="original")
        task, step, config, dependencies = copy.deepcopy((task, step, config, dependencies))
        # The step must continue to be the exact declaration in the modified task.
        step = task.steps[-1]
        evidence = task.evidence[0]
        if changed == "inputs":
            step.inputs["format"] = "long"
        elif changed == "dependency_output":
            dependencies["source"]["amount_minor"] = 2200
        elif changed == "evidence_data":
            evidence.data["amount_minor"] = 2200
        elif changed == "evidence_version":
            evidence.version = "2"
        elif changed == "evidence_source":
            evidence.source = "another-fixture"
        elif changed == "evidence_observed_at":
            evidence.observed_at += timedelta(seconds=1)
        elif changed == "evidence_expires_at":
            evidence.expires_at += timedelta(seconds=1)
        elif changed == "evidence_quality":
            evidence.quality = 0.8
        elif changed == "evidence_half_life":
            evidence.half_life_seconds = 600
        elif changed == "ancestor_handler":
            task.steps[0].handler_version = "2"
        elif changed == "ancestor_inputs":
            task.steps[0].inputs["record_id"] = "invoice-8"
        elif changed == "handler_version":
            step.handler_version = "2"
        elif changed == "handler":
            step.handler = "synthetic.other"
        elif changed == "model":
            config.model_fingerprint = "synthetic-model-v2"
        elif changed == "prompt":
            config.prompt_version = "2"
        elif changed == "actor":
            config.actor_id = "reader-b"
        elif changed == "permissions":
            config.permissions = ["invoice:read"]
        elif changed == "policy_version":
            config.policy.version = "2"
        elif changed == "policy_content":
            config.policy.max_amount_minor = 1000
        elif changed == "workflow":
            task.workflow_id = "another-workflow"
        elif changed == "metadata":
            task.metadata["scope"] = "restricted"
        assert cache.key(task, step, config, dependencies) != original_key
        assert cache.lookup(task, step, config, dependencies) is None


def test_reordering_and_budget_estimates_do_not_invalidate(workload):
    task, step, config, dependencies, _ = workload
    with ResultCache() as cache:
        original = cache.key(task, step, config, dependencies)
        config.permissions.reverse()
        config.permissions.append(config.permissions[0])
        config.budget.max_tokens = 100
        step.estimated_usage = Usage(input_tokens=900)
        task.steps.reverse()
        assert cache.key(task, step, config, dependencies) == original
        config.mode = "baseline"
        assert cache.key(task, step, config, dependencies) == original


def test_unrelated_sibling_input_and_evidence_do_not_invalidate(workload):
    task, step, config, dependencies, _ = workload
    with ResultCache() as cache:
        original = cache.key(task, step, config, dependencies)
        task.steps.append(
            Step(step_id="unrelated", kind="model", handler="synthetic.other", inputs={"n": 7})
        )
        task.evidence.append(task.evidence[0].model_copy(update={"evidence_id": "unrelated"}))
        assert cache.key(task, step, config, dependencies) == original


def test_tenant_isolation_in_lookup_and_invalidation(workload):
    task, step, config, dependencies, result = workload
    other = task.model_copy(deep=True)
    other.tenant_id = "tenant-b"
    other.evidence[0].tenant_id = "tenant-b"
    with ResultCache() as cache:
        hit = cache.put(task, step, config, dependencies, result, source_run_id="tenant-a-run")
        assert cache.key(other, step, config, dependencies) != hit.key
        assert cache.lookup(other, step, config, dependencies) is None
        assert cache.invalidate("tenant-b", hit.key) is False
        assert cache.lookup(task, step, config, dependencies) == hit
        other_hit = cache.put(other, step, config, dependencies, result, source_run_id="tenant-b-run")
        assert other_hit.source_run_id == "tenant-b-run"
        assert cache.invalidate("tenant-a", hit.key) is True
        assert cache.lookup(task, step, config, dependencies) is None
        assert cache.lookup(other, step, config, dependencies) == other_hit


@pytest.mark.parametrize("reason", ["baseline", "replay", "disabled", "side_effect"])
def test_ineligible_context_neither_reads_nor_writes(workload, reason):
    task, step, config, dependencies, result = workload
    with ResultCache() as cache:
        original = cache.put(task, step, config, dependencies, result, source_run_id="original")
        if reason in {"baseline", "replay"}:
            config.mode = reason
        elif reason == "disabled":
            config.cache_enabled = False
        else:
            step.side_effect = True
        assert cache.lookup(task, step, config, dependencies) is None
        assert cache.put(task, step, config, dependencies, result, source_run_id="new") is None
        assert cache.invalidate(task.tenant_id, original.key)


@pytest.mark.parametrize("status", ["reused", "failed", "blocked", "awaiting_approval"])
def test_unsuccessful_or_already_reused_result_is_not_stored(workload, status):
    task, step, config, dependencies, result = workload
    result.status = status
    with ResultCache() as cache:
        assert cache.put(task, step, config, dependencies, result, source_run_id="run") is None
        assert cache.lookup(task, step, config, dependencies) is None


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_output",
        "extra_output",
        "missing_step",
        "different_step",
        "duplicate_steps",
        "duplicate_dependencies",
        "missing_evidence",
        "duplicate_evidence",
        "duplicate_evidence_binding",
        "foreign_evidence",
        "cycle",
        "nan",
        "nonstring_key",
    ],
)
def test_invalid_bindings_fail_closed(workload, invalid):
    task, step, config, dependencies, result = workload
    if invalid == "missing_output":
        dependencies.clear()
    elif invalid == "extra_output":
        dependencies["unknown"] = None
    elif invalid == "missing_step":
        task.steps.remove(task.steps[0])
    elif invalid == "different_step":
        step = step.model_copy(update={"handler": "other"})
    elif invalid == "duplicate_steps":
        task.steps.append(task.steps[0])
    elif invalid == "duplicate_dependencies":
        step.depends_on.append("source")
    elif invalid == "missing_evidence":
        task.evidence.clear()
    elif invalid == "duplicate_evidence":
        task.evidence.append(task.evidence[0])
    elif invalid == "duplicate_evidence_binding":
        task.steps[0].evidence_ids.append("invoice")
    elif invalid == "foreign_evidence":
        task.evidence[0].tenant_id = "tenant-b"
    elif invalid == "cycle":
        task.steps[0].depends_on = ["summary"]
    elif invalid == "nan":
        step.inputs["invalid"] = float("nan")
    elif invalid == "nonstring_key":
        step.inputs["invalid"] = {1: "ambiguous"}
    with ResultCache() as cache:
        with pytest.raises(CacheValidationError):
            cache.key(task, step, config, dependencies)
        with pytest.raises(CacheValidationError):
            cache.lookup(task, step, config, dependencies)
        with pytest.raises(CacheValidationError):
            cache.put(task, step, config, dependencies, result, source_run_id="run")


def test_expiry_is_capped_by_transitively_bound_evidence(workload):
    task, step, config, dependencies, result = workload
    deadline = task.evidence[0].expires_at
    with ResultCache() as cache:
        hit = cache.put(
            task,
            step,
            config,
            dependencies,
            result,
            source_run_id="run",
            expires_at=deadline + timedelta(days=1),
        )
        assert hit.expires_at == deadline
        assert cache.lookup(task, step, config, dependencies, now=deadline - timedelta(seconds=1))
        assert cache.lookup(task, step, config, dependencies, now=deadline) is None


def test_explicit_expiry_and_expired_write(workload):
    task, step, config, dependencies, result = workload
    deadline = utcnow() + timedelta(minutes=5)
    with ResultCache() as cache:
        hit = cache.put(
            task, step, config, dependencies, result, source_run_id="run", expires_at=deadline
        )
        assert hit.expires_at == deadline
        assert cache.lookup(task, step, config, dependencies, now=deadline) is None
    with ResultCache() as cache:
        assert cache.put(
            task,
            step,
            config,
            dependencies,
            result,
            source_run_id="run",
            expires_at=utcnow() - timedelta(seconds=1),
        ) is None


def test_expired_evidence_is_never_stored(workload):
    task, step, config, dependencies, result = workload
    task.evidence[0].expires_at = utcnow() - timedelta(seconds=1)
    with ResultCache() as cache:
        assert cache.put(task, step, config, dependencies, result, source_run_id="run") is None


def test_naive_time_is_rejected(workload):
    task, step, config, dependencies, result = workload
    with ResultCache() as cache:
        with pytest.raises(CacheValidationError, match="timezone-aware"):
            cache.lookup(task, step, config, dependencies, now=datetime(2030, 1, 1))
        with pytest.raises(CacheValidationError, match="timezone-aware"):
            cache.put(
                task,
                step,
                config,
                dependencies,
                result,
                source_run_id="run",
                expires_at=datetime(2030, 1, 1),
            )


def test_timezone_equivalent_evidence_has_same_identity(workload):
    task, step, config, dependencies, _ = workload
    with ResultCache() as cache:
        original = cache.key(task, step, config, dependencies)
        local_zone = timezone(timedelta(hours=-4))
        task.evidence[0].observed_at = task.evidence[0].observed_at.astimezone(local_zone)
        task.evidence[0].expires_at = task.evidence[0].expires_at.astimezone(local_zone)
        assert cache.key(task, step, config, dependencies) == original


def test_concurrent_writers_preserve_one_source_and_output(tmp_path, workload):
    task, step, config, dependencies, result = workload
    path = tmp_path / "concurrent.sqlite3"
    caches = [ResultCache(path) for _ in range(4)]

    def write(index):
        candidate = result.model_copy(update={"output": {"winner": index}})
        return caches[index].put(
            task, step, config, dependencies, candidate, source_run_id=f"run-{index}"
        )

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            hits = list(pool.map(write, range(4)))
        assert all(hit == hits[0] for hit in hits)
        assert hits[0].source_run_id == f"run-{hits[0].output['winner']}"
    finally:
        for cache in caches:
            cache.close()


def test_persisted_output_corruption_is_detected(tmp_path, workload):
    task, step, config, dependencies, result = workload
    path = tmp_path / "corrupt.sqlite3"
    with ResultCache(path) as cache:
        cache.put(task, step, config, dependencies, result, source_run_id="run")
    with sqlite3.connect(path) as database:
        database.execute("UPDATE canopy_cache_entries SET output_json = ?", ('{"tampered":true}',))
    with ResultCache(path) as cache:
        with pytest.raises(CacheIntegrityError, match="digest mismatch"):
            cache.lookup(task, step, config, dependencies)


@pytest.mark.parametrize("bad_field", ["step_id", "source_run_id", "cache_key", "output"])
def test_inconsistent_results_are_rejected(workload, bad_field):
    task, step, config, dependencies, result = workload
    if bad_field == "step_id":
        result.step_id = "wrong"
    elif bad_field == "source_run_id":
        result.source_run_id = "wrong"
    elif bad_field == "cache_key":
        result.cache_key = "wrong"
    else:
        result.output = {"unserializable": object()}
    with ResultCache() as cache:
        with pytest.raises(CacheValidationError):
            cache.put(task, step, config, dependencies, result, source_run_id="run")
        assert cache.lookup(task, step, config, dependencies) is None


def test_close_is_idempotent_and_blocks_database_operations(workload):
    task, step, config, dependencies, _ = workload
    cache = ResultCache()
    cache.close()
    cache.close()
    with pytest.raises(RuntimeError, match="closed"):
        cache.lookup(task, step, config, dependencies)


def test_can_share_sqlite_file_with_execution_store(tmp_path, workload):
    task, step, config, dependencies, result = workload
    path = tmp_path / "shared.sqlite3"
    with ExecutionStore(path) as store, ResultCache(path) as cache:
        run_id = store.create_run(task, config)
        cached = cache.put(task, step, config, dependencies, result, source_run_id=run_id)
        assert store.get_run(task.tenant_id, run_id).task == task
        assert cached.source_run_id == run_id
    with ResultCache(path) as cache, ExecutionStore(path) as store:
        assert cache.lookup(task, step, config, dependencies) == cached
        assert store.get_run(task.tenant_id, run_id).run_id == run_id


def test_expired_row_can_be_replaced_without_waiting(monkeypatch, workload):
    task, step, config, dependencies, result = workload
    now = utcnow()
    deadline = now + timedelta(minutes=1)
    with ResultCache() as cache:
        original = cache.put(
            task, step, config, dependencies, result, source_run_id="old", expires_at=deadline
        )
        monkeypatch.setattr("canopy.cache.utcnow", lambda: deadline)
        result.output = {"fresh": True}
        replacement = cache.put(task, step, config, dependencies, result, source_run_id="new")
        assert replacement.key == original.key
        assert replacement.output == {"fresh": True}
        assert replacement.source_run_id == "new"
        assert replacement.created_at == deadline
        assert cache.lookup(task, step, config, dependencies) == replacement


def test_key_survives_contract_json_roundtrip(workload):
    task, step, config, dependencies, _ = workload
    # Integer assignment to a float-typed contract field must normalize on both
    # sides of persistence, while arbitrary payload numeric types are preserved.
    config.policy.freshness_threshold = 1
    with ResultCache() as cache:
        original_key = cache.key(task, step, config, dependencies)
        restored_task = Task.model_validate_json(task.model_dump_json())
        restored_config = RunConfig.model_validate_json(config.model_dump_json())
        restored_step = restored_task.steps[-1]
        assert cache.key(restored_task, restored_step, restored_config, dependencies) == original_key


@pytest.mark.parametrize(
    ("field", "corrupted_value"),
    [
        ("source_run_id", "incorrect-source"),
        ("usage_json", '{"cost_usd":1000}'),
        ("created_at", "2000-01-01T00:00:00+00:00"),
        ("expires_at", None),
    ],
)
def test_persisted_provenance_usage_and_expiry_corruption_is_detected(
    tmp_path, workload, field, corrupted_value
):
    task, step, config, dependencies, result = workload
    path = tmp_path / "metadata-corruption.sqlite3"
    with ResultCache(path) as cache:
        cache.put(task, step, config, dependencies, result, source_run_id="original-source")
    with sqlite3.connect(path) as database:
        database.execute(f"UPDATE canopy_cache_entries SET {field} = ?", (corrupted_value,))
    with ResultCache(path) as cache:
        with pytest.raises(CacheIntegrityError, match="whole-entry digest mismatch"):
            cache.lookup(task, step, config, dependencies)
        # A new writer must not silently legitimize corrupt historical evidence.
        with pytest.raises(CacheIntegrityError, match="whole-entry digest mismatch"):
            cache.put(task, step, config, dependencies, result, source_run_id="new-source")


def test_legacy_schema_migrates_without_blessing_unverified_rows(tmp_path, workload):
    task, step, config, dependencies, result = workload
    path = tmp_path / "legacy.sqlite3"
    with ResultCache(path) as cache:
        original = cache.put(task, step, config, dependencies, result, source_run_id="legacy-run")
    # This is precisely the old on-disk table shape: payload/output checksums
    # existed, but provenance, usage and expiry had no whole-entry checksum.
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE canopy_cache_entries DROP COLUMN entry_digest")
    with ResultCache(path) as cache:
        assert cache.lookup(task, step, config, dependencies) is None
        with sqlite3.connect(path) as database:
            preserved = database.execute(
                "SELECT source_run_id, entry_digest FROM canopy_cache_entries"
            ).fetchall()
        assert preserved == [("legacy-run", None)]
        replacement = cache.put(task, step, config, dependencies, result, source_run_id="fresh-run")
        assert replacement.key == original.key
        assert replacement.source_run_id == "fresh-run"
    with ResultCache(path) as cache:
        assert cache.lookup(task, step, config, dependencies) == replacement


def test_caller_mutation_while_waiting_for_sqlite_writer_cannot_change_cached_snapshot(
    tmp_path, workload
):
    task, step, config, dependencies, result = workload
    original_task, original_step, original_config, original_dependencies, original_result = (
        copy.deepcopy(workload)
    )
    path = tmp_path / "mutation.sqlite3"
    waiting_for_writer = Event()
    with ResultCache(path) as cache, sqlite3.connect(path) as blocker:
        expected_key = cache.key(original_task, original_step, original_config, original_dependencies)
        cache._connection.set_trace_callback(
            lambda sql: waiting_for_writer.set() if sql == "BEGIN IMMEDIATE" else None
        )
        blocker.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                cache.put,
                task,
                step,
                config,
                dependencies,
                result,
                source_run_id="original-run",
            )
            try:
                assert waiting_for_writer.wait(timeout=5), "writer never reached SQLite lock"
                assert not pending.done(), "the blocking write transaction was not honored"
                task.tenant_id = "different-tenant"
                task.metadata["scope"] = "changed"
                task.evidence[0].tenant_id = "different-tenant"
                task.evidence[0].expires_at = utcnow() - timedelta(days=1)
                step.inputs["format"] = "changed"
                config.permissions.clear()
                config.cache_enabled = False
                dependencies["source"]["amount_minor"] = 999999
                result.output["summary"] = "changed after cache call"
                result.usage.cost_usd = 1000
                result.status = "failed"
            finally:
                blocker.commit()
            hit = pending.result(timeout=5)
        assert hit.key == expected_key
        assert hit.output == original_result.output
        assert hit.historical_usage == original_result.usage
        assert hit.expires_at == original_task.evidence[0].expires_at
        assert cache.lookup(
            original_task, original_step, original_config, original_dependencies
        ) == hit
    with ResultCache(path) as cache:
        assert cache.lookup(
            original_task, original_step, original_config, original_dependencies
        ) == hit
