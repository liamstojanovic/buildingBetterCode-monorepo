"""TypeSafe wire-contract tests; all requests use local HTTP mocks."""

from copy import deepcopy
import json

import httpx
import pytest

from canopy.handlers import HandlerExecutionError
from canopy.jev import JevAdapter
from canopy.models import ModelConfig, ModelRequest


def questions():
    return {
        "urgent": {"type": "noul", "instructions": "Does the ticket describe urgency?"},
        "department": {
            "type": "choice", "instructions": "Which department handles the ticket?",
            "criteria": {"billing": "Payments and refunds", "support": "Technical issues"},
        },
        "severity": {
            "type": "score", "instructions": "How severe is the reported problem?",
            "criteria": ["Minor", "Service impaired", "Service unavailable"],
        },
    }


def response():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "urgent": {"type": "noul", "noul": 0.9},
            "department": {
                "type": "choice", "choice": "billing",
                "probabilities": {"billing": 0.8, "support": 0.2}, "confidence": 0.7,
            },
            "severity": {
                "type": "score", "score": 1.2, "confidence": 0.7,
                "probabilities": {"0": 0, "1": 0.8, "2": 0.2},
                "legend": {"0": "Minor", "1": "Service impaired", "2": "Service unavailable"},
            },
        },
        # No generated prose does not mean that the response reports zero tokens.
        "usage": {"input_tokens": 150, "output_tokens": 20},
    }


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("CANOPY_TEST_JEV_KEY", "mock-only-credential")
    return ModelConfig(provider="jev", model="jev-latest", api_key_env="CANOPY_TEST_JEV_KEY")


def request(question_map=None):
    return ModelRequest(
        prompt='{"instructions":"Triage the ticket","inputs":{"ticket":"Refund overdue"}}',
        inputs={"questions": questions() if question_map is None else question_map},
    )


def test_jev_posts_typed_questions_and_records_actual_usage(config):
    seen = []

    def handle(req):
        seen.append(req)
        return httpx.Response(200, json=response())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = JevAdapter(client).generate(request(), config)
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.typesafe.ai/v1/systemone"
    assert seen[0].headers["Authorization"] == "Bearer mock-only-credential"
    assert json.loads(seen[0].content) == {
        "model": "jev-latest", "state": request().prompt, "questions": questions(),
    }
    assert result.output["provider"] == "jev"
    assert result.output["model"] == "jev-1.13.0"
    assert result.output["answers"] == response()["answers"]
    assert "text" not in result.output
    assert result.usage.tokens_known is True
    assert result.usage.input_tokens == 150
    assert result.usage.output_tokens == 20
    assert result.usage.cost_usd is None
    assert result.usage.energy_j is None


def test_jev_passes_structured_rubric_and_noul_criteria(config):
    rubric = [{"level": "low", "examples": ["cosmetic"]}, ["medium", "disruption"], "high"]
    qs = questions()
    qs["urgent"]["criteria"] = {"true": {"when": "service blocked"}, "false": None}
    qs["department"]["criteria"]["support"] = None
    qs["department"]["instructions"] = {"question": "Which team?", "context": ["ticket"]}
    qs["severity"]["criteria"] = rubric
    data = response()
    data["answers"]["severity"]["legend"] = {str(i): level for i, level in enumerate(rubric)}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = JevAdapter(client).generate(request(qs), config)
    assert result.output["answers"]["severity"]["legend"]["0"] == rubric[0]


@pytest.mark.parametrize("invalid", [
    {}, [], {"bad": "question"}, {"": {"type": "noul", "instructions": "Urgent?"}},
    {"bad": {"type": "noul"}},
    {"bad": {"type": "noul", "instructions": 123}},
    {"bad": {"type": "box", "instructions": "Location?"}},
    {"bad": {"type": "noul", "instructions": "Urgent?", "extra": "unsupported"}},
    {"bad": {"type": "noul", "instructions": "Urgent?", "criteria": {"yes": "yes"}}},
    {"bad": {"type": "choice", "instructions": "Team?", "criteria": ["a", "b"]}},
    {"bad": {"type": "choice", "instructions": "Team?", "criteria": {"only": "one"}}},
    {"bad": {"type": "choice", "instructions": "Team?", "criteria": {str(i): None for i in range(256)}}},
    {"bad": {"type": "choice", "instructions": "Team?", "criteria": {"a": 1, "b": 2}}},
    {"bad": {"type": "score", "instructions": "Urgency?", "criteria": ["only"]}},
    {"bad": {"type": "score", "instructions": "Urgency?", "criteria": [str(i) for i in range(11)]}},
    {"bad": {"type": "score", "instructions": "Urgency?", "criteria": [None, "high"]}},
    {"bad": {"type": "noul", "instructions": {"invalid": float("nan")}}},
    {"bad": {"type": "noul", "instructions": {"invalid": 10 ** 400}}},
    {"bad": {"type": "noul", "instructions": {"invalid": (1, 2)}}},
])
def test_invalid_question_fails_before_network(config, invalid):
    seen = []
    with httpx.Client(transport=httpx.MockTransport(lambda req: seen.append(req))) as client:
        with pytest.raises(HandlerExecutionError, match="valid typed questions"):
            JevAdapter(client).generate(request(invalid), config)
    assert not seen


def test_missing_questions_fails_before_network(config):
    seen = []
    with httpx.Client(transport=httpx.MockTransport(lambda req: seen.append(req))) as client:
        with pytest.raises(HandlerExecutionError, match="valid typed questions"):
            JevAdapter(client).generate(ModelRequest(prompt="ticket", inputs={}), config)
    assert not seen


def test_excessive_nesting_fails_before_network(config):
    nested = {}
    for _ in range(66):
        nested = {"item": nested}
    seen = []
    qs = {"question": {"type": "noul", "instructions": nested}}
    with httpx.Client(transport=httpx.MockTransport(lambda req: seen.append(req))) as client:
        with pytest.raises(HandlerExecutionError, match="valid typed questions"):
            JevAdapter(client).generate(request(qs), config)
    assert not seen


def test_oversized_body_fails_before_network(config, monkeypatch):
    monkeypatch.setattr("canopy.jev._MAX_BODY_BYTES", 10)
    seen = []
    with httpx.Client(transport=httpx.MockTransport(lambda req: seen.append(req))) as client:
        with pytest.raises(HandlerExecutionError, match="bounded JSON state"):
            JevAdapter(client).generate(request(), config)
    assert not seen


def test_temperature_is_rejected_during_configuration():
    with pytest.raises(ValueError, match="does not support temperature"):
        ModelConfig(provider="jev", model="jev-latest", temperature=0)


@pytest.mark.parametrize("field,value", [
    ("noul", True), ("noul", -0.1), ("noul", 1.1), ("noul", "0.9"),
    ("noul", None), ("type", "choice"),
])
def test_invalid_noul_preserves_usage_and_redacts_raw_output(config, field, value):
    data = response()
    data["answers"]["urgent"][field] = value
    data["private"] = "response-body-secret"
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        with pytest.raises(HandlerExecutionError) as caught:
            JevAdapter(client).generate(request(), config)
    assert caught.value.usage.tokens_known is True
    assert caught.value.usage.input_tokens == 150
    assert caught.value.usage.output_tokens == 20
    assert caught.value.output == {
        "provider": "jev", "status": "invalid_response",
        "provider_usage": {"input_tokens": 150, "output_tokens": 20},
    }
    assert "response-body-secret" not in str(caught.value)


@pytest.mark.parametrize("change", [
    lambda data: data["answers"].pop("urgent"),
    lambda data: data["answers"].update(unexpected={"type": "noul", "noul": 0.5}),
    lambda data: data.update(answers=[]),
    lambda data: data.pop("model"),
    lambda data: data.update(model="invalid model\n"),
    lambda data: data["answers"]["department"].update(choice="unknown"),
    lambda data: data["answers"]["department"].update(choice="support"),
    lambda data: data["answers"]["department"].update(confidence=2),
    lambda data: data["answers"]["department"].update(probabilities={"billing": 0.2, "support": 0.2}),
    lambda data: data["answers"]["department"]["probabilities"].update(extra=0),
    lambda data: data["answers"]["department"]["probabilities"].update(billing=True),
    lambda data: data["answers"]["severity"].update(score=3),
    lambda data: data["answers"]["severity"]["legend"].update({"0": "changed rubric"}),
    lambda data: data["answers"]["severity"]["probabilities"].pop("2"),
])
def test_invalid_or_incomplete_response_is_failure(config, change):
    data = response()
    change(data)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        with pytest.raises(HandlerExecutionError, match="invalid or incomplete") as caught:
            JevAdapter(client).generate(request(), config)
    assert caught.value.usage.tokens_known is True


@pytest.mark.parametrize("usage", [
    None, {}, {"input_tokens": 1}, {"output_tokens": 2},
    {"input_tokens": -1, "output_tokens": 2},
    {"input_tokens": True, "output_tokens": 2},
    {"input_tokens": "10", "output_tokens": 2},
])
def test_missing_or_invalid_usage_remains_unknown(config, usage):
    data = response()
    data["usage"] = usage
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = JevAdapter(client).generate(request(), config)
    assert result.usage.tokens_known is False


def test_valid_measured_zero_is_preserved_and_extras_are_dropped(config):
    data = response()
    data["usage"] = {"input_tokens": 0, "output_tokens": 0, "secret": "not-for-audit"}
    data["answers"]["urgent"]["debug"] = "not-for-audit"
    before = deepcopy(data)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = JevAdapter(client).generate(request(), config)
    assert result.usage.tokens_known is True
    assert result.usage.input_tokens == result.usage.output_tokens == 0
    assert "not-for-audit" not in result.model_dump_json()
    assert data == before


def test_one_http_error_is_not_retried_and_does_not_leak_body(config):
    seen = []

    def handle(req):
        seen.append(req)
        return httpx.Response(429, json={"message": "response-body-secret"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(HandlerExecutionError) as caught:
            JevAdapter(client).generate(request(), config)
    assert len(seen) == 1
    assert "response-body-secret" not in str(caught.value)
    assert "mock-only-credential" not in str(caught.value)
