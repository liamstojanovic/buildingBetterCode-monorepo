"""Explicit, metered model handlers for multiple providers.

Requests use synchronous HTTP with no automatic retry or provider tools. The
application owns credentials, endpoint selection and the decision to reuse a
previous completed answer. Importing this module never makes a network request.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import math
import os
import re
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from .contracts import HandlerResult, Usage
from .handlers import HandlerContext, HandlerExecutionError, HandlerRegistry
from .recording import canonical_json
from .temporal import relevant_evidence_ids


_ENDPOINTS = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "jev": "https://api.typesafe.ai",
}
_KEYS = {
    "openai": "OPENAI_API_KEY", "openai-compatible": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "jev": "JEV_API_KEY",
}


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    instructions: str = "Complete the requested business task using only the supplied data."
    max_output_tokens: int = 1024
    temperature: float | None = None
    timeout_seconds: float = 60.0
    cache_namespace: str = "default"

    def __post_init__(self) -> None:
        for label in ("provider", "model", "cache_namespace"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{label} must be a nonempty string without surrounding spaces")
        if not isinstance(self.instructions, str):
            raise ValueError("instructions must be a string")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be positive and finite")
        if self.temperature is not None and (
            isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2
        ):
            raise ValueError("temperature must be finite and between 0 and 2")
        if self.api_key_env is not None and (
            not isinstance(self.api_key_env, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env) is None
        ):
            raise ValueError("api_key_env must be an environment variable name")
        if self.base_url is not None:
            if not isinstance(self.base_url, str):
                raise ValueError("base_url must be a URL")
            url = urlsplit(self.base_url)
            if (not url.hostname or url.username or url.password or url.query or url.fragment
                    or (url.scheme != "https" and not (
                        url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
                    ))):
                raise ValueError("base_url needs HTTPS (or local HTTP), without credentials or query")
            object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if self.provider == "openai-compatible" and self.base_url is None:
            raise ValueError("openai-compatible requires base_url, including its API path prefix")
        if self.provider == "jev" and self.temperature is not None:
            raise ValueError("JEV does not support temperature")


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    inputs: dict[str, Any]


class ModelAdapter(Protocol):
    revision: str

    def generate(self, request: ModelRequest, config: ModelConfig) -> HandlerResult: ...


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage(input_tokens: Any, output_tokens: Any, cached_tokens: Any = 0) -> Usage:
    """Keep available partial counts, but never label missing usage measured zero."""
    incoming, outgoing, cached = map(_count, (input_tokens, output_tokens, cached_tokens))
    return Usage(
        input_tokens=incoming or 0, output_tokens=outgoing or 0,
        provider_cached_input_tokens=cached or 0,
        tokens_known=incoming is not None and outgoing is not None,
        model_calls=1,
    )


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list:
    return value if isinstance(value, list) else []


def _numeric_usage(value: dict) -> dict:
    """Persist numeric usage only; never retain raw provider error bodies."""
    return {key: item for key, item in value.items() if _count(item) is not None}


class HTTPAdapter:
    """A caller-supplied client is for application/test transport and remains owned by it."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def _post(
        self, config: ModelConfig, path: str, payload: dict,
        headers: dict[str, str] | None = None,
    ) -> dict:
        key_name = config.api_key_env or _KEYS.get(config.provider)
        key = os.environ.get(key_name, "") if key_name else ""
        if not key:
            raise HandlerExecutionError("Model credential environment variable is missing")
        base_url = config.base_url or _ENDPOINTS.get(config.provider)
        if not base_url:
            raise HandlerExecutionError("Model endpoint is not configured")
        auth = {"Authorization": f"Bearer {key}"}
        if config.provider == "anthropic":
            auth = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        elif config.provider == "gemini":
            auth = {"x-goog-api-key": key}
        request_headers = {**auth, "Content-Type": "application/json", **(headers or {})}
        # Default httpx transport retries=0; redirects cannot forward credentials.
        client = self._client or httpx.Client(trust_env=False)
        try:
            response = client.post(
                base_url + path, json=payload, headers=request_headers,
                timeout=config.timeout_seconds, follow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                raise HandlerExecutionError(f"Model provider returned HTTP {response.status_code}")
            result = response.json()
            if not isinstance(result, dict):
                raise HandlerExecutionError("Model provider returned an invalid response object")
            return result
        except httpx.TimeoutException:
            raise HandlerExecutionError("Model request timed out; provider usage is unknown") from None
        except httpx.HTTPError:
            raise HandlerExecutionError("Model transport failed; provider usage is unknown") from None
        except (ValueError, UnicodeError):
            raise HandlerExecutionError("Model provider returned invalid JSON; usage is unknown") from None
        finally:
            if self._client is None:
                client.close()

    def close(self) -> None:
        """Default calls close their own transport; injected clients belong to the caller."""


def _text_result(
    config: ModelConfig, body: dict, text: str, usage: Usage,
    *, complete: bool, provider_usage: dict,
) -> HandlerResult:
    output = {
        "provider": config.provider, "model": body.get("model") or config.model,
        "response_id": body.get("id"), "text": text,
        "provider_usage": _numeric_usage(provider_usage),
    }
    if not complete or not text.strip():
        raise HandlerExecutionError(
            "Model response was refused, incomplete, or lacked final text",
            usage=usage, output=output,
        )
    return HandlerResult(output=output, usage=usage)


class OpenAIResponsesAdapter(HTTPAdapter):
    revision = "openai-responses-text-v1"

    def generate(self, request: ModelRequest, config: ModelConfig) -> HandlerResult:
        payload = {
            "model": config.model, "instructions": config.instructions,
            "input": request.prompt, "max_output_tokens": config.max_output_tokens,
            "store": False,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        body = self._post(config, "/v1/responses", payload)
        raw_usage = _mapping(body.get("usage"))
        usage = _usage(raw_usage.get("input_tokens"), raw_usage.get("output_tokens"),
                       _mapping(raw_usage.get("input_tokens_details")).get("cached_tokens"))
        text, complete = [], body.get("status") == "completed" and not body.get("error")
        for item in _items(body.get("output")):
            if not isinstance(item, dict):
                complete = False
                continue
            if item.get("type") not in ("message", "reasoning"):
                complete = False  # This text-only adapter never executes provider tools.
            if item.get("type") == "message":
                if item.get("status") not in (None, "completed"):
                    complete = False
                for part in _items(item.get("content")):
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        value = part.get("text")
                        if isinstance(value, str):
                            text.append(value)
                    else:
                        complete = False
        return _text_result(config, body, "\n".join(text), usage, complete=complete,
                            provider_usage=raw_usage)


class OpenAIChatAdapter(HTTPAdapter):
    revision = "openai-compatible-chat-text-v1"

    def generate(self, request: ModelRequest, config: ModelConfig) -> HandlerResult:
        payload = {
            "model": config.model,
            "messages": [{"role": "system", "content": config.instructions},
                         {"role": "user", "content": request.prompt}],
            "max_tokens": config.max_output_tokens, "stream": False,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        body = self._post(config, "/chat/completions", payload)
        raw_usage = _mapping(body.get("usage"))
        usage = _usage(raw_usage.get("prompt_tokens"), raw_usage.get("completion_tokens"),
                       _mapping(raw_usage.get("prompt_tokens_details")).get("cached_tokens"))
        choices = body.get("choices")
        choice = _mapping(choices[0]) if isinstance(choices, list) and len(choices) == 1 else {}
        message = _mapping(choice.get("message"))
        text = message.get("content")
        complete = (choice.get("finish_reason") == "stop" and not message.get("refusal")
                    and not message.get("tool_calls") and not message.get("function_call"))
        return _text_result(config, body, text if isinstance(text, str) else "", usage,
                            complete=complete, provider_usage=raw_usage)


class AnthropicAdapter(HTTPAdapter):
    revision = "anthropic-messages-text-v1"

    def generate(self, request: ModelRequest, config: ModelConfig) -> HandlerResult:
        payload = {
            "model": config.model, "system": config.instructions,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": config.max_output_tokens,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        body = self._post(config, "/v1/messages", payload)
        raw_usage = _mapping(body.get("usage"))
        input_parts = [_count(raw_usage.get("input_tokens")),
                       _count(raw_usage.get("cache_creation_input_tokens", 0)),
                       _count(raw_usage.get("cache_read_input_tokens", 0))]
        incoming = sum(input_parts) if all(item is not None for item in input_parts) else None
        usage = _usage(incoming, raw_usage.get("output_tokens"),
                       raw_usage.get("cache_read_input_tokens", 0))
        complete = (body.get("stop_reason") == "end_turn"
                    and _mapping(body.get("stop_details")).get("type") != "refusal")
        text = []
        for block in _items(body.get("content")):
            if isinstance(block, dict) and block.get("type") == "text":
                if isinstance(block.get("text"), str):
                    text.append(block["text"])
            elif not isinstance(block, dict) or block.get("type") not in ("thinking", "redacted_thinking"):
                complete = False
        return _text_result(config, body, "\n".join(text), usage, complete=complete,
                            provider_usage=raw_usage)


class GeminiAdapter(HTTPAdapter):
    revision = "gemini-generate-content-text-v1"

    def generate(self, request: ModelRequest, config: ModelConfig) -> HandlerResult:
        generation = {"maxOutputTokens": config.max_output_tokens, "candidateCount": 1}
        if config.temperature is not None:
            generation["temperature"] = config.temperature
        payload = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "systemInstruction": {"parts": [{"text": config.instructions}]},
            "generationConfig": generation,
        }
        model = config.model.removeprefix("models/")
        body = self._post(config, f"/v1beta/models/{quote(model, safe='')}:generateContent", payload)
        raw_usage = _mapping(body.get("usageMetadata"))
        outgoing = _count(raw_usage.get("candidatesTokenCount"))
        thinking = _count(raw_usage.get("thoughtsTokenCount", 0))
        usage = _usage(raw_usage.get("promptTokenCount"),
                       outgoing + thinking if outgoing is not None and thinking is not None else None,
                       raw_usage.get("cachedContentTokenCount", 0))
        candidates = body.get("candidates")
        candidate = _mapping(candidates[0]) if isinstance(candidates, list) and len(candidates) == 1 else {}
        complete = (candidate.get("finishReason") == "STOP"
                    and not _mapping(body.get("promptFeedback")).get("blockReason"))
        text = []
        for part in _items(_mapping(candidate.get("content")).get("parts")):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                if not part.get("thought"):
                    text.append(part["text"])
            else:
                complete = False
        return _text_result(
            config, {"model": body.get("modelVersion"), "id": body.get("responseId")},
            "\n".join(text), usage, complete=complete, provider_usage=raw_usage,
        )


def _adapter(config: ModelConfig) -> ModelAdapter:
    factories = {"openai": OpenAIResponsesAdapter, "openai-compatible": OpenAIChatAdapter,
                 "anthropic": AnthropicAdapter, "gemini": GeminiAdapter}
    if config.provider == "jev":
        from .jev import JevAdapter
        return JevAdapter()
    if config.provider not in factories:
        raise ValueError("Unknown provider: supply an explicit ModelAdapter for custom providers")
    return factories[config.provider]()


class ModelHandler:
    def __init__(self, config: ModelConfig, adapter: ModelAdapter | None = None) -> None:
        if not isinstance(config, ModelConfig):
            raise TypeError("config must be ModelConfig")
        self._config = config
        self._adapter = adapter if adapter is not None else _adapter(config)
        if not isinstance(self._adapter.revision, str) or not self._adapter.revision.strip():
            raise ValueError("Model adapter must declare a nonempty revision")
        identity = {"config": asdict(config), "adapter_revision": self._adapter.revision,
                    "request_envelope": "canopy-model-request-v1"}
        self._fingerprint = sha256(canonical_json(identity).encode()).hexdigest()

    @property
    def config(self) -> ModelConfig:
        """Immutable, credential-free request settings for benchmark provenance."""
        return self._config

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def version(self) -> str:
        return self._fingerprint

    def __call__(self, context: HandlerContext) -> HandlerResult:
        if context.step.handler_version != self.version:
            raise HandlerExecutionError("Model handler version does not match its configuration")
        relevant = relevant_evidence_ids(context.task, context.step.step_id)
        evidence = [item.model_dump(mode="json") for item in context.task.evidence
                    if item.evidence_id in relevant]
        inputs = deepcopy(context.inputs)
        prompt = canonical_json({
            "instructions": self._config.instructions, "inputs": inputs,
            "dependencies": deepcopy(context.dependencies),
            "evidence": sorted(evidence, key=lambda item: item["evidence_id"]),
        })
        return self._adapter.generate(ModelRequest(prompt, inputs), self._config)

    def close(self) -> None:
        close = getattr(self._adapter, "close", None)
        if close is not None:
            close()


def register_model_handler(
    registry: HandlerRegistry, config: ModelConfig, *, name: str = "model.generate",
    cacheable: bool = False, adapter: ModelAdapter | None = None,
) -> ModelHandler:
    """Register one real model handler; opting into reuse accepts a previous answer.

    Cache identity includes provider/model/settings via the handler version.
    Provider aliases may change remotely: use a pinned model and change
    cache_namespace when changing credentials, deployment or provider revision.
    """
    handler = ModelHandler(config, adapter)
    registry.register(name, handler, version=handler.version, kind="model",
                      synthetic=False, cacheable=cacheable)
    return handler
