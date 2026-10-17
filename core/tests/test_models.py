"""Real HTTP request construction and runtime integration without paid/network calls."""

from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import json

import httpx
import pytest

from canopy.contracts import Evidence, RunConfig, Step, Task, utcnow
from canopy.handlers import HandlerExecutionError, HandlerRegistry
from canopy.models import (
    AnthropicAdapter, GeminiAdapter, ModelConfig, ModelRequest, OpenAIChatAdapter,
    OpenAIResponsesAdapter, register_model_handler,
)
from canopy.runtime import create_runtime


def responses_body():
    return {
        "id": "resp_test", "model": "pinned-model", "status": "completed",
        "output": [{"type": "message", "status": "completed",
                    "content": [{"type": "output_text", "text": "Route to billing."}]}],
        "usage": {"input_tokens": 25, "output_tokens": 7,
                  "input_tokens_details": {"cached_tokens": 10}},
    }


def chat_body():
    return {
        "id": "chat_test", "model": "pinned-model",
        "choices": [{"finish_reason": "stop", "message": {"content": "Route to billing."}}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 7,
                  "prompt_tokens_details": {"cached_tokens": 10}},
    }


def anthropic_body():
    return {
        "id": "msg_test", "model": "pinned-model", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "Route to billing."}],
        "usage": {"input_tokens": 10, "output_tokens": 7,
                  "cache_creation_input_tokens": 5, "cache_read_input_tokens": 10},
    }


def gemini_body():
    return {
        "responseId": "gem_test", "modelVersion": "pinned-model",
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "private thought", "thought": True}, {"text": "Route to billing."},
        ]}}],
        "usageMetadata": {"promptTokenCount": 25, "candidatesTokenCount": 3,
                          "thoughtsTokenCount": 4, "cachedContentTokenCount": 10},
    }


CASES = [
    ("openai", OpenAIResponsesAdapter, responses_body, "/v1/responses", "authorization"),
    ("openai-compatible", OpenAIChatAdapter, chat_body, "/v1/chat/completions", "authorization"),
    ("anthropic", AnthropicAdapter, anthropic_body, "/v1/messages", "x-api-key"),
    ("gemini", GeminiAdapter, gemini_body, "/v1beta/models/pinned-model:generateContent", "x-goog-api-key"),
]


def config_for(provider, **updates):
    config = ModelConfig(provider, "pinned-model", api_key_env="TEST_MODEL_KEY",
                         base_url="https://example.test/v1" if provider == "openai-compatible" else None)
    return replace(config, **updates)


@pytest.mark.parametrize("provider,adapter_type,make_body,path,header", CASES)
def test_wire_format_real_usage_and_auth(provider, adapter_type, make_body, path, header, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json=make_body())

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = adapter_type(client).generate(ModelRequest('{"ticket":"invoice"}', {}),
                                               config_for(provider, max_output_tokens=150))
    request = requests[0]
    assert request.url.path == path and "test-secret" in request.headers[header]
    assert "test-secret" not in str(request.url) and "test-secret" not in request.content.decode()
    payload = json.loads(request.content)
    if provider == "openai":
        assert payload["store"] is False and payload["max_output_tokens"] == 150
    elif provider == "gemini":
        assert payload["generationConfig"]["maxOutputTokens"] == 150
    else:
        assert payload["max_tokens"] == 150
    assert result.output["text"] == "Route to billing."
    assert result.usage.input_tokens == 25 and result.usage.output_tokens == 7
    assert result.usage.provider_cached_input_tokens == 10 and result.usage.tokens_known
    assert result.usage.cost_usd is None and result.usage.energy_j is None


@pytest.mark.parametrize("provider,adapter_type,make_body,path,header", CASES)
def test_missing_usage_is_unknown(provider, adapter_type, make_body, path, header, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    body = make_body()
    body.pop("usageMetadata" if provider == "gemini" else "usage")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        result = adapter_type(client).generate(ModelRequest("task", {}), config_for(provider))
    assert not result.usage.tokens_known
    assert result.usage.input_tokens == result.usage.output_tokens == 0


@pytest.mark.parametrize("defect", ["incomplete", "refusal", "null_content", "bad_type", "tool_call"])
def test_openai_failed_or_malformed_response_keeps_measured_usage(defect, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    body = responses_body()
    if defect == "incomplete":
        body["status"] = "incomplete"
    elif defect == "refusal":
        body["output"][0]["content"] = [{"type": "refusal", "refusal": "No"}]
    elif defect == "null_content":
        body["output"][0]["content"] = None
    elif defect == "bad_type":
        body["output"][0]["type"] = []
    else:
        body["output"].append({"type": "function_call"})
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(HandlerExecutionError) as failure:
            OpenAIResponsesAdapter(client).generate(ModelRequest("task", {}), config_for("openai"))
    assert failure.value.usage.input_tokens == 25 and failure.value.usage.output_tokens == 7


@pytest.mark.parametrize("provider,adapter_type,make_body", [
    ("anthropic", AnthropicAdapter, anthropic_body), ("gemini", GeminiAdapter, gemini_body),
    ("openai-compatible", OpenAIChatAdapter, chat_body),
])
@pytest.mark.parametrize("defect", ["truncated", "refusal", "malformed"])
def test_native_failure_retains_usage(provider, adapter_type, make_body, defect, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    body = make_body()
    if provider == "anthropic":
        if defect == "truncated":
            body["stop_reason"] = "max_tokens"
        elif defect == "refusal":
            body["stop_details"] = {"type": "refusal"}
        else:
            body["content"] = [{"type": []}]
    elif provider == "gemini":
        if defect == "truncated":
            body["candidates"][0]["finishReason"] = "MAX_TOKENS"
        elif defect == "refusal":
            body["promptFeedback"] = {"blockReason": "SAFETY"}
        else:
            body["candidates"][0]["content"]["parts"] = None
    elif defect == "truncated":
        body["choices"][0]["finish_reason"] = "length"
    elif defect == "refusal":
        body["choices"][0]["message"]["refusal"] = "No"
    else:
        body["choices"] = None
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(HandlerExecutionError) as failure:
            adapter_type(client).generate(ModelRequest("task", {}), config_for(provider))
    assert failure.value.usage.input_tokens == 25 and failure.value.usage.output_tokens == 7


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_errors_never_retry_or_leak_provider_body(status, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "test-secret echoed by provider"},
                              headers={"location": "https://other.example"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(HandlerExecutionError) as failure:
            OpenAIResponsesAdapter(client).generate(ModelRequest("task", {}), config_for("openai"))
    assert len(calls) == 1
    assert "test-secret" not in str(failure.value)
    assert failure.value.usage is None


def test_timeout_is_unknown_and_safe(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")

    def transport(request):
        raise httpx.ReadTimeout("sensitive provider message", request=request)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(HandlerExecutionError) as failure:
            OpenAIResponsesAdapter(client).generate(ModelRequest("task", {}), config_for("openai"))
    assert "sensitive" not in str(failure.value) and failure.value.usage is None


@pytest.mark.parametrize("changes", [
    {"model": "other"}, {"provider": "anthropic"}, {"instructions": "new"},
    {"temperature": 0.5}, {"max_output_tokens": 10}, {"cache_namespace": "revision-2"},
    {"base_url": "https://new.example"}, {"api_key_env": "NEW_KEY"},
])
def test_request_configuration_changes_handler_identity(changes):
    first, second = HandlerRegistry(), HandlerRegistry()
    original = config_for("openai")
    a = register_model_handler(first, original)
    b = register_model_handler(second, replace(original, **changes))
    assert a.version != b.version and a.fingerprint != b.fingerprint


@pytest.mark.parametrize("changes", [
    {"base_url": "https://user:secret@example.test"}, {"base_url": "https://example.test?key=x"},
    {"base_url": "http://remote.example"}, {"timeout_seconds": float("nan")},
    {"max_output_tokens": True}, {"temperature": float("inf")},
])
def test_invalid_config_rejected(changes):
    with pytest.raises(ValueError):
        replace(config_for("openai"), **changes)


def test_real_registry_baseline_governed_and_changed_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=responses_body())

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        registry = HandlerRegistry()
        handler = register_model_handler(registry, config_for("openai"), cacheable=True,
                                         adapter=OpenAIResponsesAdapter(client))
        now = utcnow()
        evidence = Evidence(evidence_id="ticket", tenant_id="tenant", source="local://ticket",
                            version="1", observed_at=now, expires_at=now + timedelta(hours=1),
                            data={"text": "Invoice amount is wrong"})
        task = Task(task_id="run-one", tenant_id="tenant", workflow_id="triage", evidence=[evidence],
                    steps=[Step(step_id="triage", kind="model", handler="model.generate",
                                handler_version=handler.version, evidence_ids=["ticket"],
                                inputs={"prompt": "Pick billing or technical"})])
        config = RunConfig(permissions=["execute:*"], model_fingerprint=handler.fingerprint)
        with closing(create_runtime(str(tmp_path / "run.sqlite3"), registry=registry)) as runtime:
            baseline = runtime.run_task(task, config.model_copy(update={"mode": "baseline"}))
            cold = runtime.run_task(task, config)
            warm = runtime.run_task(task.model_copy(update={"task_id": "new-id"}), config)
            task.evidence[0].version = "2"
            task.evidence[0].data["text"] = "Application is down"
            changed = runtime.run_task(task, config)
        assert all(result.status == "completed" for result in (baseline, cold, warm, changed))
        assert cold.reused_steps == 0 and warm.reused_steps == 1 and changed.reused_steps == 0
        assert not cold.synthetic and cold.usage.tokens_known
        assert warm.usage.model_calls == warm.usage.input_tokens == warm.usage.output_tokens == 0
        assert len(calls) == 3 and calls[0] == calls[1]
        envelope = json.loads(calls[0]["input"])
        assert set(envelope) == {"instructions", "inputs", "dependencies", "evidence"}
        assert "new-id" not in calls[0]["input"]
