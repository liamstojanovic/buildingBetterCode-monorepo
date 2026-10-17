"""Validate the serialization and measurement boundary shared by all clients."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from canopy.contracts import BudgetLimits, Evidence, RunConfig, Task, Usage


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -0.1])
def test_budget_rejects_nonfinite_or_negative_measurements(value):
    with pytest.raises(ValidationError):
        BudgetLimits(max_cost_usd=value)


def test_evidence_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="e1", tenant_id="a", source="fixture", version="1",
            observed_at=datetime(2026, 1, 1),
        )


def test_unknown_energy_and_cost_survive_wire_roundtrip():
    usage = Usage.model_validate_json(Usage().model_dump_json())
    assert usage.cost_usd is None
    assert usage.energy_j is None
    assert usage.provider_cached_input_tokens == 0
    assert usage.tokens_known is False
    assert usage.elapsed_known is False


def test_explicit_zero_token_measurements_survive_wire_roundtrip():
    usage = Usage(input_tokens=0, output_tokens=0)
    assert usage.tokens_known is True
    assert Usage.model_validate_json(usage.model_dump_json()).tokens_known is True


def test_elapsed_measurement_completeness_survives_wire_roundtrip():
    unknown = Usage.model_validate_json(Usage().model_dump_json())
    measured = Usage.model_validate_json(Usage(elapsed_ms=0).model_dump_json())
    assert not unknown.elapsed_known
    assert measured.elapsed_known


def test_unsupported_schema_and_config_fields_are_rejected():
    with pytest.raises(ValidationError):
        Task(schema_version="2", task_id="t1", tenant_id="a", workflow_id="w1", steps=[])
    with pytest.raises(ValidationError):
        RunConfig(is_admin=True)


def test_task_roundtrip_retains_exact_payload_and_timezone():
    task = Task(
        task_id="t1", tenant_id="a", workflow_id="w1", steps=[],
        evidence=[Evidence(
            evidence_id="e1", tenant_id="a", source="fixture", version="1",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            data={"amount_minor": 123456789012345, "source_text": "αβγ"},
        )],
    )
    restored = Task.model_validate(json.loads(task.model_dump_json()))
    assert restored == task
    assert restored.evidence[0].data["amount_minor"] == 123456789012345
