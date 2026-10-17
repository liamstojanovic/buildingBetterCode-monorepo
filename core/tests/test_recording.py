"""Synthetic fixtures exercising persistence, integrity, and dispatch-free replay."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
import json
import sqlite3

from pollard import Runtime, SQLiteStore
import pytest

from canopy.contracts import Evidence, RunConfig, RunResult, Step, StepResult, Task, Usage, utcnow
from canopy.recording import (
    ExecutionStore,
    IncompleteRun,
    RecordingConflict,
    RecordingIntegrityError,
    RunNotFound,
    canonical_json,
    fingerprint,
)


@pytest.fixture
def task():
    return Task(
        task_id="synthetic-task", tenant_id="alpha", workflow_id="synthetic-workflow",
        steps=[Step(step_id="charge", kind="tool", handler="synthetic-charge", side_effect=True)],
    )


def completed(store, task, *, run_id="run-1", status="completed", source_run_id=None):
    """Record an explicitly synthetic operation with example, not measured, usage."""
    config = RunConfig()
    store.create_run(task, config, run_id=run_id)
    saved = store.get_run(task.tenant_id, run_id)
    usage = Usage(input_tokens=4, output_tokens=2, tool_calls=1, cost_usd=0.1)
    result = RunResult(
        run_id=run_id, task_id=task.task_id, tenant_id=task.tenant_id,
        mode=config.mode, status=status, started_at=saved.started_at, finished_at=utcnow(),
        steps=[StepResult(step_id=task.steps[0].step_id, status=status,
                          output={"receipt": "synthetic"}, usage=usage,
                          source_run_id=source_run_id)],
        output={"receipt": "synthetic"}, usage=usage, policy_version=config.policy.version,
        synthetic=True,
    )
    store.finish_run(result)
    return result


def test_reopen_and_strict_replay_has_no_dispatch_or_writes(tmp_path, task, monkeypatch):
    path = tmp_path / "recordings.sqlite3"
    calls = []
    with ExecutionStore(path) as store:
        store.create_run(task, RunConfig(), run_id="live")
        if store.claim_step("alpha", "live", "charge"):
            calls.append("synthetic external effect")
        original = completed(store, task, run_id="live")
        history = store.events("alpha", "live")
        trace_ids = [node.id for node in store.trace("alpha", "live")]
    assert calls == ["synthetic external effect"]

    def forbidden(*args, **kwargs):
        raise AssertionError("strict replay attempted dispatch or trace mutation")

    monkeypatch.setattr(Runtime, "run", forbidden)
    monkeypatch.setattr(SQLiteStore, "put", forbidden)
    with ExecutionStore(path, read_only=True) as store:
        replay = store.replay("alpha", "live", replay_run_id="inspection", task_id="new-task")
        assert replay.output == original.output
        assert replay.run_id == "inspection" and replay.task_id == "new-task"
        assert replay.mode == "replay" and replay.replay_source_run_id == "live"
        assert replay.steps[0].source_run_id == "live"
        assert replay.usage == Usage(cost_usd=0, tokens_known=True)
        assert replay.steps[0].usage == Usage(cost_usd=0, tokens_known=True)
        assert replay.usage.energy_j is None
        assert store.get_run("alpha", "live").result.usage.tool_calls == 1
        assert store.get_run("alpha", "live").result.usage.cost_usd == 0.1
        assert store.events("alpha", "live") == history
        assert [node.id for node in store.trace("alpha", "live")] == trace_ids
        assert store.verify_run("alpha", "live").ok
        assert [item.run_id for item in store.list_runs("alpha")] == ["live"]
    assert calls == ["synthetic external effect"]


def test_claim_survives_crash_and_reopen_without_authorizing_retry(tmp_path, task):
    path = tmp_path / "claims.sqlite3"
    with ExecutionStore(path) as store:
        store.create_run(task, RunConfig(), run_id="uncertain")
        assert store.claim_step("alpha", "uncertain", "charge")
    with ExecutionStore(path) as store:
        assert not store.claim_step("alpha", "uncertain", "charge")
        with pytest.raises(IncompleteRun):
            store.replay("alpha", "uncertain")
        assert len(store.events("alpha", "uncertain")) == 1
        assert store.verify_run("alpha", "uncertain").ok


def test_two_store_instances_compete_for_single_dispatch_claim(tmp_path, task):
    path = tmp_path / "claims.sqlite3"
    with ExecutionStore(path) as store:
        store.create_run(task, RunConfig(), run_id="race")
    with ExecutionStore(path) as one, ExecutionStore(path) as two:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(s.claim_step, "alpha", "race", "charge") for s in (one, two)]
            assert sorted(f.result() for f in futures) == [False, True]
        assert one.verify_run("alpha", "race").ok
        assert len(one.events("alpha", "race")) == 1


def test_concurrent_event_writers_have_one_contiguous_history(tmp_path, task):
    path = tmp_path / "events.sqlite3"
    with ExecutionStore(path) as one, ExecutionStore(path) as two:
        one.create_run(task, RunConfig(), run_id="race")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(
                (one if i % 2 else two).append_event, "alpha", "race", "observation",
                payload={"item": i},
            ) for i in range(20)]
            assert sorted(f.result().sequence for f in futures) == list(range(20))
        assert one.verify_run("alpha", "race").ok
        events = one.events("alpha", "race", after_sequence=16, limit=2)
        assert [event.sequence for event in events] == [17, 18]
        links = one.trace_links("alpha", "race")
        assert list(links) == list(range(20))
        assert len(one.trace("alpha", "race")) == 21


def test_recording_retries_are_idempotent_and_conflicting_writes_fail(task):
    with ExecutionStore() as store:
        result = completed(store, task)
        events = store.events("alpha", result.run_id)
        store.record_step("alpha", result.run_id, result.steps[0])
        store.finish_run(result)
        store.create_run(task, RunConfig(), run_id=result.run_id)
        assert store.events("alpha", result.run_id) == events
        assert not store.claim_step("alpha", result.run_id, "charge")
        with pytest.raises(RecordingConflict):
            store.record_step("alpha", result.run_id, result.steps[0].model_copy(
                update={"output": "conflicting receipt"}
            ))
        with pytest.raises(RecordingConflict):
            store.finish_run(result.model_copy(update={"output": {"different": True}}))
        with pytest.raises(RecordingConflict):
            store.create_run(task, RunConfig(prompt_version="changed"), run_id=result.run_id)
        with pytest.raises(RecordingConflict):
            store.append_event("alpha", result.run_id, "late")


@pytest.mark.parametrize("operation", [
    lambda s: s.get_run("beta", "run-1"),
    lambda s: s.events("beta", "run-1"),
    lambda s: s.trace("beta", "run-1"),
    lambda s: s.trace_links("beta", "run-1"),
    lambda s: s.verify_run("beta", "run-1"),
    lambda s: s.replay("beta", "run-1"),
    lambda s: s.claim_step("beta", "run-1", "charge"),
    lambda s: s.append_event("beta", "run-1", "injected"),
    lambda s: s.record_step("beta", "run-1", StepResult(step_id="charge", status="completed")),
])
def test_all_run_apis_enforce_tenant_scope(task, operation):
    with ExecutionStore() as store:
        completed(store, task)
        with pytest.raises(RunNotFound):
            operation(store)
        assert store.list_runs("beta") == []


def test_same_run_id_is_isolated_between_tenants(task):
    with ExecutionStore() as store:
        first = completed(store, task)
        second_task = task.model_copy(update={"tenant_id": "beta", "task_id": "beta-task"})
        second = completed(store, second_task)
        alpha = store.get_run("alpha", first.run_id)
        beta = store.get_run("beta", second.run_id)
        assert alpha.pollard_root_id != beta.pollard_root_id
        assert store.replay("alpha", first.run_id).task_id == task.task_id
        assert store.replay("beta", second.run_id).task_id == "beta-task"
        assert store.verify_run("alpha", first.run_id).ok
        assert store.verify_run("beta", second.run_id).ok
        with pytest.raises(RunNotFound):
            store.finish_run(first.model_copy(update={"tenant_id": "uncreated"}))


@pytest.mark.parametrize("kind", ["event", "result", "step", "task", "claim", "trace", "missing"])
def test_replay_rejects_corrupt_history(tmp_path, task, kind):
    path = tmp_path / "corrupt.sqlite3"
    with ExecutionStore(path) as store:
        store.create_run(task, RunConfig(), run_id="run-1")
        store.claim_step("alpha", "run-1", "charge")
        completed(store, task)
        head = store.get_run("alpha", "run-1").pollard_head_id
        if kind in {"trace", "missing"}:
            with sqlite3.connect(store.trace_path) as conn:
                if kind == "trace":
                    conn.execute("UPDATE nodes SET payload='{}' WHERE id=?", (head,))
                else:
                    conn.execute("DELETE FROM nodes WHERE id=?", (head,))
        else:
            with sqlite3.connect(path) as conn:
                if kind == "claim":
                    conn.execute("DELETE FROM canopy_step_claims")
                elif kind == "event":
                    conn.execute("UPDATE canopy_events SET event_json=replace(event_json, ?, ?) "
                                 "WHERE sequence=0", ("canopy.step.claimed", "forged"))
                elif kind == "step":
                    conn.execute("UPDATE canopy_steps SET result_json=replace(result_json, ?, ?)",
                                 ("synthetic", "forged"))
                elif kind == "result":
                    conn.execute("UPDATE canopy_runs SET result_json=replace(result_json, ?, ?)",
                                 ("synthetic", "forged"))
                else:
                    conn.execute("UPDATE canopy_runs SET task_json=replace(task_json, ?, ?)",
                                 ("synthetic", "forged"))
        assert not store.verify_run("alpha", "run-1").ok
        with pytest.raises(RecordingIntegrityError):
            store.replay("alpha", "run-1")


def test_replay_preserves_blocked_outcome_and_original_cache_provenance(task):
    with ExecutionStore() as store:
        completed(store, task, status="blocked", source_run_id="earlier-executed-source")
        replay = store.replay("alpha", "run-1")
        assert replay.status == "blocked"
        assert replay.steps[0].status == "blocked"
        assert replay.steps[0].source_run_id == "earlier-executed-source"
        assert replay.replay_source_run_id == "run-1"


@pytest.mark.parametrize("change", [
    {"task_id": "other"}, {"mode": "baseline"}, {"policy_version": "v2"},
    {"started_at": datetime(2000, 1, 1, tzinfo=UTC)},
    {"finished_at": datetime(2000, 1, 1, tzinfo=UTC)}, {"steps": []},
    {"steps": [StepResult(step_id="charge", status="blocked")]},
    {"steps": [StepResult(step_id="unknown", status="completed")]},
    {"steps": [StepResult(step_id="charge", status="completed")] * 2},
])
def test_terminal_result_must_match_run_snapshot(task, change):
    with ExecutionStore() as source:
        result = completed(source, task)
    with ExecutionStore() as target:
        target.create_run(task, RunConfig(), run_id=result.run_id, started_at=result.started_at)
        with pytest.raises(ValueError):
            target.finish_run(result.model_copy(update=change))
        assert target.get_run("alpha", "run-1").result is None
        assert target.events("alpha", "run-1") == []
        assert target.verify_run("alpha", "run-1").ok


def test_omitted_or_conflicting_recorded_steps_cannot_finish(task):
    with ExecutionStore() as source:
        result = completed(source, task)
    with ExecutionStore() as store:
        store.create_run(task, RunConfig(), run_id=result.run_id, started_at=result.started_at)
        store.record_step("alpha", result.run_id, result.steps[0])
        with pytest.raises(ValueError, match="omits"):
            store.finish_run(result.model_copy(update={"status": "blocked", "steps": []}))
        with pytest.raises(RecordingConflict):
            store.finish_run(result.model_copy(update={"steps": [
                result.steps[0].model_copy(update={"output": "changed"})
            ]}))
        assert len(store.events("alpha", "run-1")) == 1


def test_canonical_json_and_fingerprints_are_stable_across_order_timezone_and_reopen():
    first = {"b": [None, True, "café", 2.5], "a": 1}
    second = {"a": 1, "b": [None, True, "café", 2.5]}
    assert canonical_json(first) == '{"a":1,"b":[null,true,"café",2.5]}'
    assert fingerprint(first) == fingerprint(second)
    instant = datetime(2026, 10, 17, tzinfo=UTC)
    assert fingerprint(instant) == fingerprint(instant.astimezone(timezone(timedelta(hours=-4))))
    config = RunConfig()
    assert canonical_json(config) == canonical_json(RunConfig.model_validate_json(
        canonical_json(config)
    ))
    assert fingerprint({"value": 1}) != fingerprint({"value": "1"})
    assert fingerprint(first).startswith("canopy-json-v1:")


@pytest.mark.parametrize("invalid", [
    float("nan"), float("inf"), {1: "integer key"}, {"x": {1, 2}},
    b"bytes", object(), (1, 2), datetime(2026, 1, 1),
])
def test_serialization_rejects_ambiguous_or_unsupported_data(invalid):
    with pytest.raises((ValueError, TypeError)):
        canonical_json(invalid)


def test_invalid_task_tenant_and_unknown_step_fail_before_writes(task):
    with ExecutionStore() as store:
        other = Evidence(evidence_id="foreign", tenant_id="beta", source="synthetic",
                         version="v1", observed_at=utcnow())
        with pytest.raises(ValueError, match="tenant"):
            store.create_run(task.model_copy(update={"evidence": [other]}), RunConfig())
        store.create_run(task, RunConfig(), run_id="run-1")
        with pytest.raises(ValueError, match="declared"):
            store.claim_step("alpha", "run-1", "absent")
        with pytest.raises(ValueError, match="prefix"):
            store.append_event("alpha", "run-1", "canopy.run.finished")
        with pytest.raises(TypeError):
            store.append_event("alpha", "run-1", "bad-payload", payload={"unsupported": b"x"})
        assert store.events("alpha", "run-1") == []


def test_readonly_store_rejects_mutations(tmp_path, task):
    path = tmp_path / "read-only.sqlite3"
    with ExecutionStore(path) as store:
        completed(store, task)
    with ExecutionStore(path, read_only=True) as store:
        with pytest.raises(PermissionError):
            store.create_run(task, RunConfig())
    with pytest.raises(ValueError, match="on-disk"):
        ExecutionStore(read_only=True)


def test_replay_mode_never_authorizes_dispatch_claim(task):
    with ExecutionStore() as store:
        store.create_run(task, RunConfig(mode="replay", replay_run_id="source"), run_id="replay")
        assert not store.claim_step("alpha", "replay", "charge")
        assert store.events("alpha", "replay") == []


def test_reused_results_require_provenance_and_cannot_claim_side_effect_reuse(task):
    with ExecutionStore() as store:
        store.create_run(task, RunConfig(), run_id="side-effect")
        with pytest.raises(ValueError, match="side-effect"):
            store.record_step("alpha", "side-effect", StepResult(
                step_id="charge", status="reused", source_run_id="source"
            ))
        pure = task.model_copy(update={"steps": [task.steps[0].model_copy(
            update={"side_effect": False}
        )]})
        store.create_run(pure, RunConfig(), run_id="pure")
        with pytest.raises(ValueError, match="provenance"):
            store.record_step("alpha", "pure", StepResult(step_id="charge", status="reused"))


def test_missing_trace_file_is_an_integrity_failure(tmp_path, task):
    path = tmp_path / "missing-trace.sqlite3"
    with ExecutionStore(path) as store:
        completed(store, task)
        store.trace_path.unlink()
        assert not store.verify_run("alpha", "run-1").ok
        with pytest.raises(RecordingIntegrityError, match="Pollard"):
            store.replay("alpha", "run-1")
        assert not store.trace_path.exists()


def test_missing_interned_pollard_blob_is_an_integrity_failure(tmp_path, task):
    path = tmp_path / "interned.sqlite3"
    large_task = task.model_copy(update={"metadata": {"synthetic_long_text": "a" * 3000}})
    with ExecutionStore(path) as store:
        completed(store, large_task)
        with sqlite3.connect(store.trace_path) as conn:
            assert conn.execute("SELECT count(*) FROM blobs").fetchone()[0] > 0
            conn.execute("DELETE FROM blobs")
        assert not store.verify_run("alpha", "run-1").ok
        with pytest.raises(RecordingIntegrityError):
            store.replay("alpha", "run-1")


def test_corrupted_claim_table_cannot_authorize_duplicate_effect(tmp_path, task):
    path = tmp_path / "claim-corruption.sqlite3"
    with ExecutionStore(path) as store:
        store.create_run(task, RunConfig(), run_id="run-1")
        assert store.claim_step("alpha", "run-1", "charge")
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM canopy_step_claims")
        with pytest.raises(RecordingIntegrityError, match="claims"):
            store.claim_step("alpha", "run-1", "charge")
        assert len(store.events("alpha", "run-1")) == 1


def test_read_results_are_detached(task):
    with ExecutionStore() as store:
        result = completed(store, task)
        result.output["mutated"] = True
        replay = store.replay("alpha", "run-1")
        replay.steps[0].output["mutated"] = True
        snapshot = store.get_run("alpha", "run-1")
        snapshot.task.steps[0].inputs["mutated"] = True
        assert store.replay("alpha", "run-1").steps[0].output == {"receipt": "synthetic"}
        assert store.get_run("alpha", "run-1").result.output == {"receipt": "synthetic"}
        assert store.verify_run("alpha", "run-1").ok


@pytest.mark.parametrize("operation", ["create", "step", "finish"])
def test_write_snapshots_do_not_follow_caller_mutation_while_waiting(task, monkeypatch, operation):
    with ExecutionStore() as source:
        result = completed(source, task)
    with ExecutionStore() as store:
        if operation != "create":
            store.create_run(task, RunConfig(), run_id="run-1", started_at=result.started_at)
        transaction = store._transaction

        @contextmanager
        def mutate_before_lock(*, write=False):
            # Simulate caller mutation while this operation waits on another
            # writer. Persisted bytes and trace must still bind one snapshot.
            if write:
                task.steps[0].inputs["concurrent_mutation"] = True
                result.steps[0].output["concurrent_mutation"] = True
                result.output["concurrent_mutation"] = True
            with transaction(write=write):
                yield

        monkeypatch.setattr(store, "_transaction", mutate_before_lock)
        if operation == "create":
            store.create_run(task, RunConfig(), run_id="run-1")
        elif operation == "step":
            store.record_step("alpha", "run-1", result.steps[0])
        else:
            store.finish_run(result)
        assert store.verify_run("alpha", "run-1").ok
        saved = store.get_run("alpha", "run-1")
        assert not saved.task.steps[0].inputs
        if saved.steps:
            assert "concurrent_mutation" not in saved.steps[0].output
        if saved.result:
            assert "concurrent_mutation" not in saved.result.output


def test_persisted_records_are_plain_json_with_real_pollard_nodes(tmp_path, task):
    path = tmp_path / "inspect.sqlite3"
    with ExecutionStore(path) as store:
        completed(store, task)
        root = store.get_run("alpha", "run-1").pollard_root_id
    with SQLiteStore(f"{path}.pollard.sqlite3", read_only=True) as trace:
        node = trace.get(root)
        assert node.id == node.expected_id
        assert json.loads(node.payload["snapshot"])["task"]["tenant_id"] == "alpha"
        assert len(list(trace.walk(root))) == 3
