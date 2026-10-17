"""Isolated Pollard-Jev worker; runnable by absolute filename with stdlib only.

The optional package is imported only after checking its separate environment.
JSONL requests use ``op=inspect``, ``op=infer`` (native numeric observations), or
``op=infer_text`` (a versioned Canopy historical text evaluation). No request can name a Python
factory, package, module, policy, or executable. The default factory loads only
the pinned, already cached OpenJev checkpoint. The parent owns timeout/termination.

Package interfaces: https://github.com/jemsbhai/pollard-jev/tree/main/src/pollard_jev
The context guard matches the package's scripts/benchmark_local.py integration.
"""

from contextlib import redirect_stdout
from datetime import UTC, datetime
from importlib import metadata
import json
import os
import re
import sys
from time import perf_counter
from typing import Any

PROTOCOL_VERSION = 1
EXPECTED_PACKAGES = {"pollard-jev": "0.13", "pollard": "1.6.0"}
MAX_LINE_CHARS = 32 * 1024 * 1024


def _unknown_usage() -> dict[str, Any]:
    return {"input_tokens": 0, "output_tokens": 0, "tokens_known": False, "model_calls": 0}


def _error(code: str, *, usage: dict | None = None, **details: Any) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION, "ok": False, "error_code": code,
        "usage": usage if usage is not None else _unknown_usage(), **details,
    }


def _options(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) - {"cache_dir", "device", "max_length"}:
        raise ValueError
    result = {"cache_dir": None, "device": None, "max_length": 4096, **value}
    for key in ("cache_dir", "device"):
        item = result[key]
        if item is not None and (
            not isinstance(item, str) or not item.strip() or any(ord(char) < 32 for char in item)
        ):
            raise ValueError
    if type(result["max_length"]) is not int or result["max_length"] < 1:
        raise ValueError
    return result


def _text_request(value: Any) -> dict:
    """Validate Canopy's separate text envelope without inventing observations."""
    keys = {"schema_version", "request_id", "mapping_id", "mapping_version", "source_sha256",
            "evaluation_mode", "created_at", "valid_until", "premise", "choices"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError
    if (value["schema_version"] != "canopy-jev-text/v1"
            or value["evaluation_mode"] != "historical_re_evaluation"
            or value["mapping_version"] != "1"
            or value["mapping_id"] not in {
                "canopy-support-business-decision-v1", "canopy-support-tool-selection-v1",
                "canopy-support-next-step-v1"}):
        raise ValueError
    if not isinstance(value["source_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", value["source_sha256"]):
        raise ValueError
    for key, maximum in (("request_id", 200), ("premise", 100000)):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > maximum:
            raise ValueError
    for field in ("created_at", "valid_until"):
        if not isinstance(value[field], str):
            raise ValueError
        stamp = datetime.fromisoformat(value[field].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
    if datetime.fromisoformat(value["valid_until"]) <= datetime.fromisoformat(value["created_at"]):
        raise ValueError
    choices = value["choices"]
    if not isinstance(choices, list) or not 2 <= len(choices) <= 32:
        raise ValueError
    for choice in choices:
        if not isinstance(choice, dict) or set(choice) != {"name", "hypothesis"}:
            raise ValueError
        for key, maximum in (("name", 200), ("hypothesis", 4000)):
            if not isinstance(choice[key], str) or not choice[key].strip() or len(choice[key]) > maximum:
                raise ValueError
    if len({choice["name"] for choice in choices}) != len(choices):
        raise ValueError
    return value


def _text_fresh(request: dict, now: datetime) -> bool:
    return datetime.fromisoformat(request["created_at"]) <= now < datetime.fromisoformat(request["valid_until"])


class Worker:
    """Single synchronous provider; an injected factory is only a Python test seam.

    ``provider_factory(**options, representation='json-v1')`` must return a real
    ``OpenJevProvider``. ``clock()`` must return an aware datetime. Neither seam
    is accessible through the subprocess protocol.
    """

    def __init__(self, provider_factory=None, clock=None) -> None:
        self._factory = provider_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._provider = None
        self._guard = None
        self._loaded_options = None

    def inspect(self) -> dict:
        versions = {}
        try:
            for name in EXPECTED_PACKAGES:
                versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            return _error("missing_dependencies", package_versions=versions)
        if any(versions.get(name) != expected for name, expected in EXPECTED_PACKAGES.items()):
            return _error("unsupported_package_versions", package_versions=versions)
        return {"protocol_version": PROTOCOL_VERSION, "ok": True, "package_versions": versions}

    def handle(self, message: Any) -> dict:
        # The subprocess entrypoint additionally redirects the underlying stdout
        # file descriptor, covering native-library logs as well as Python print.
        with redirect_stdout(sys.stderr):
            try:
                return self._handle(message)
            except Exception:
                return _error("worker_failure")

    def _handle(self, message: Any) -> dict:
        if not isinstance(message, dict):
            return _error("invalid_message")
        protocol = message.get("protocol_version", PROTOCOL_VERSION)
        if type(protocol) is not int or protocol != PROTOCOL_VERSION:
            return _error("unsupported_protocol")
        operation = message.get("op")
        allowed = {"op", "protocol_version"}
        if operation in ("infer", "infer_text"):
            allowed |= {"request", "options"}
        if set(message) - allowed:
            return _error("invalid_message")
        if operation not in ("inspect", "infer", "infer_text"):
            return _error("unsupported_operation")
        inspection = self.inspect()
        if operation == "inspect" or not inspection["ok"]:
            return inspection
        versions = inspection["package_versions"]
        try:
            options = _options(message.get("options", {}))
        except (TypeError, ValueError):
            return _error("invalid_options")
        if self._loaded_options is not None and options != self._loaded_options:
            return _error("configuration_changed")
        try:
            from pollard_jev.contracts import DecisionRequest, ProviderResult, fresh
            from pollard_jev.providers.context import ContextCheckedEncoder
            from pollard_jev.providers.openjev import OpenJevProvider, _validated_rows
        except Exception:
            return _error("dependency_import_failed")
        try:
            text_mode = operation == "infer_text"
            request = (_text_request(message.get("request")) if text_mode
                       else DecisionRequest.model_validate(message.get("request")))
        except Exception:
            return _error("invalid_request")
        if not text_mode and not request.observations:
            return _error("missing_observations")
        is_fresh = _text_fresh if text_mode else fresh
        if not is_fresh(request, self._clock()):
            return _error("stale_request")

        load_started = perf_counter()
        loaded_now = self._provider is None
        if self._provider is None:
            try:
                # These affect this isolated process only. The package's factory
                # also calls snapshot_download(local_files_only=True).
                for name in (
                    "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_IMPLICIT_TOKEN",
                    "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_PROGRESS_BARS",
                ):
                    os.environ[name] = "1"
                factory = self._factory or OpenJevProvider.from_local_cache
                provider = factory(**options, representation="json-v1")
                if not isinstance(provider, OpenJevProvider) or provider.representation != "json-v1":
                    raise ValueError
                guard = ContextCheckedEncoder(provider._encoder)
                provider._encoder = guard
                self._provider, self._guard = provider, guard
                self._loaded_options = dict(options)
            except Exception:
                return _error("model_load_failed", package_versions=versions)

        # Model loading may outlast evidence validity; do not start inference
        # merely because the request was fresh before loading the checkpoint.
        load_ms = (perf_counter() - load_started) * 1000 if loaded_now else 0.0
        if not is_fresh(request, self._clock()):
            return _error("stale_request")
        self._guard.measurements.clear()
        calls_before = self._guard.forward_calls
        inference_started = perf_counter()
        try:
            if text_mode:
                # Native DecisionRequest observations are numeric-only. This
                # explicit bridge uses the package-loaded real encoder directly;
                # it never smuggles natural language into numeric observations.
                raw = self._guard.predict_hypotheses(
                    request["premise"], [choice["hypothesis"] for choice in request["choices"]],
                )
                rows = _validated_rows(raw, len(request["choices"]))
                details = {choice["name"]: row for choice, row in zip(request["choices"], rows, strict=True)}
                scores = {name: row.entailment for name, row in details.items()}
                identity = self._provider.identity.model_copy(update={"settings": {
                    **self._provider.identity.settings, "input_representation": "canopy-text-v1",
                    "mapping_id": request["mapping_id"], "mapping_version": request["mapping_version"],
                    "source_sha256": request["source_sha256"],
                }})
                results = (ProviderResult(
                    request_id=request["request_id"], identity=identity,
                    semantics="independent_entailment", scores=scores,
                    proposed_action=max(scores, key=scores.__getitem__), evidence="sufficient",
                    nli_scores=details,
                ),)
            else:
                identity = self._provider.identity
                results = self._provider.infer((request,))
        except Exception:
            usage, measurements = self._measurements(calls_before)
            overflow = any(item["context_limit_exceeded"] for item in measurements)
            return _error(
                "context_limit_exceeded" if overflow else "inference_failed",
                usage=usage, measurements=measurements,
                measurement_basis="complete_unpadded_pairs", package_versions=versions,
            )
        usage, measurements = self._measurements(calls_before)
        inference_ms = (perf_counter() - inference_started) * 1000
        if not is_fresh(request, self._clock()):
            return _error(
                "stale_after_inference", usage=usage, measurements=measurements,
                measurement_basis="complete_unpadded_pairs", package_versions=versions,
            )
        try:
            if not isinstance(results, tuple) or len(results) != 1:
                raise ValueError
            result = ProviderResult.model_validate(results[0].model_dump())
            choices = ({choice["name"]: {} for choice in request["choices"]} if text_mode
                       else {choice.name: choice.parameters for choice in request.choices})
            request_id = request["request_id"] if text_mode else request.request_id
            if result.request_id != request_id or result.identity != identity:
                raise ValueError
            if set(result.scores) != set(choices):
                raise ValueError
            if result.proposed_action not in choices:
                raise ValueError
            if result.parameters != choices[result.proposed_action]:
                raise ValueError
            if not usage["tokens_known"] or usage["model_calls"] != 1:
                raise ValueError
            serialized = result.model_dump(mode="json")
            json.dumps(serialized, allow_nan=False)
        except Exception:
            return _error(
                "invalid_result", usage=usage, measurements=measurements,
                measurement_basis="complete_unpadded_pairs", package_versions=versions,
            )
        return {
            "protocol_version": PROTOCOL_VERSION, "ok": True, "result": serialized,
            "usage": usage, "measurements": measurements,
            "phase_timings_ms": {"model_load": load_ms, "inference": inference_ms},
            "measurement_basis": "complete_unpadded_pairs", "package_versions": versions,
        }

    def _measurements(self, calls_before: int) -> tuple[dict, list[dict]]:
        attempts = self._guard.forward_calls - calls_before
        measurements = [dict(item) for item in self._guard.measurements]
        self._guard.measurements.clear()
        usage = _unknown_usage()
        usage["model_calls"] = attempts
        # Token counts describe complete unpadded input pairs presented to the
        # classifier, not GPU work, generated prose, or provider billing.
        if attempts == 1 and len(measurements) == 1 and not measurements[0]["context_limit_exceeded"]:
            usage.update(input_tokens=measurements[0]["unshared_input_tokens"], tokens_known=True)
        return usage, measurements


def _reject_constant(_value):
    raise ValueError


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def serve(worker=None, input_stream=None, output_stream=None) -> None:
    """Read one JSON object per line and emit exactly one safe JSON response."""
    worker = worker or Worker()
    source = input_stream if input_stream is not None else sys.stdin
    destination = output_stream if output_stream is not None else sys.stdout
    while True:
        try:
            line = source.readline(MAX_LINE_CHARS + 1)
        except UnicodeError:
            destination.write(json.dumps(_error("invalid_json")) + "\n")
            destination.flush()
            return
        if not line:
            return
        if len(line) > MAX_LINE_CHARS:
            while line and not line.endswith("\n"):
                line = source.readline(MAX_LINE_CHARS + 1)
            result = _error("message_too_large")
        else:
            try:
                message = json.loads(line, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
            except (ValueError, TypeError, RecursionError):
                result = _error("invalid_json")
            else:
                result = worker.handle(message)
        destination.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        destination.flush()


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    # Keep a private protocol descriptor while both Python and native stdout
    # logging go to stderr. This changes descriptors only in the worker process.
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        serve(output_stream=protocol)
    finally:
        protocol.close()


if __name__ == "__main__":
    main()
