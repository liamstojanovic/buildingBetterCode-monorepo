"""Pollard-Jev's real provider interface, isolated from Canopy's Pollard version.

Only native DecisionRequest data crosses the worker boundary. Robotics policy,
DecisionLoop and simulated actions are not part of this adapter.
"""

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Lock, Thread

from pydantic import AwareDatetime, TypeAdapter

from .contracts import Evidence, HandlerResult, RunConfig, Step, Task, Usage, utcnow
from .handlers import HandlerExecutionError
from .models import ModelConfig, ModelRequest, POLLARD_JEV_MODEL
from .recording import canonical_json

_DATETIME = TypeAdapter(AwareDatetime)
_VERSIONS = {"pollard-jev": "0.13", "pollard": "1.6.0"}
_MODEL_REVISION = "552759daad712f1af6c4c13dabcb1e047886fc9c"
_MAX_LINE_BYTES = 8 * 1024 * 1024


def _native_request(step: Step) -> dict:
    request = step.inputs.get("decision_request")
    if not isinstance(request, dict):
        raise ValueError("Pollard-Jev requires inputs.decision_request with native typed observations and choices")
    return request


def _validity_evidence(step: Step, tenant_id: str) -> Evidence:
    request = _native_request(step)
    observations = request.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("Pollard-Jev requires at least one explicit numeric observation")
    try:
        starts = [_DATETIME.validate_python(request["created_at"])]
        ends = [_DATETIME.validate_python(request["valid_until"])]
        for observation in observations:
            starts.append(_DATETIME.validate_python(observation["observed_at"]))
            ends.append(_DATETIME.validate_python(observation["valid_until"]))
        observed, expires = max(starts), min(ends)
        if expires <= observed:
            raise ValueError("no shared validity interval")
    except (KeyError, TypeError, ValueError):
        raise ValueError("Pollard-Jev request and observations need a shared, aware validity interval") from None
    return Evidence(
        evidence_id=f"pollard-jev:{step.step_id}", tenant_id=tenant_id,
        source="pollard-jev://decision-request",
        version=sha256(canonical_json(request).encode()).hexdigest(),
        observed_at=observed, expires_at=expires, data={"decision_request": deepcopy(request)},
    )


def bind_pollard_jev_task(task: Task, version: str) -> Task:
    """Bind handler revision and native validity to ordinary Canopy evidence.

    The CLI uses this before either comparison arm. Python applications can use
    it on a model-only task, or reproduce the same exact evidence binding when
    building a mixed registry task.
    """
    task = task.model_copy(deep=True)
    generated = []
    for step in task.steps:
        if step.kind != "model" or step.handler != "model.generate":
            raise ValueError("Pollard-Jev CLI steps must use the model.generate model handler")
        evidence = _validity_evidence(step, task.tenant_id)
        step.handler_version = version
        if evidence.evidence_id not in step.evidence_ids:
            step.evidence_ids.append(evidence.evidence_id)
        generated.append(evidence)
    identities = {item.evidence_id for item in generated}
    task.evidence = [item for item in task.evidence if item.evidence_id not in identities] + generated
    return task


def pollard_jev_sample_task() -> Task:
    """Invented numeric replenishment decision; proposals do not place orders."""
    now = utcnow()
    end = now + timedelta(hours=4)
    observations = [
        {"observation_id": name, "source": "sample-inventory", "feature": name,
         "value": value, "unit": unit, "observed_at": now.isoformat(),
         "valid_until": end.isoformat(), "status": "known"}
        for name, value, unit in [
            ("stock_on_hand", 30.0, "items"), ("daily_demand", 10.0, "items/day"),
            ("supplier_lead_time", 5.0, "days"),
        ]
    ]
    native = {
        "request_id": "sample-replenishment", "created_at": now.isoformat(),
        "valid_until": end.isoformat(), "observations": observations,
        "question": "Which inventory recommendation is supported by these observations?",
        "choices": [
            {"name": "reorder", "hypothesis": "Stock will run out before new supplier stock arrives.",
             "parameters": {}},
            {"name": "wait", "hypothesis": "Stock covers demand until new supplier stock arrives.",
             "parameters": {}},
        ],
    }
    return Task(
        task_id="sample-replenishment", tenant_id="sample-tenant", workflow_id="sample-replenishment",
        steps=[Step(step_id="recommend", kind="model", handler="model.generate",
                    inputs={"decision_request": native})],
        metadata={"sample_data": True, "description": "Invented numeric decision; no order is placed."},
    )


class PollardJevAdapter:
    """One persistent, serialized local worker; model weights load once per instance."""

    revision = "pollard-jev-0.13-openjev-json-v1-552759daad712f1af6c4c13dabcb1e047886fc9c"

    def __init__(self) -> None:
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._lines: Queue = Queue()
        self._config: ModelConfig | None = None

    def validate_task(self, task: Task, step: Step, config: RunConfig) -> None:
        expected = _validity_evidence(step, task.tenant_id)
        found = [item for item in task.evidence if item.evidence_id == expected.evidence_id]
        if (expected.evidence_id not in step.evidence_ids or len(found) != 1 or found[0] != expected):
            raise ValueError("Pollard-Jev requires exact native-validity evidence; use bind_pollard_jev_task")

    def _start(self, config: ModelConfig) -> None:
        if not config.worker_python or not Path(config.worker_python).is_file():
            raise HandlerExecutionError(
                "Set worker_python to the separate Pollard-Jev environment's Python executable"
            )
        if config.model != POLLARD_JEV_MODEL:
            raise HandlerExecutionError("Pollard-Jev model does not match the pinned package checkpoint")
        if self._config is not None and config != self._config:
            raise HandlerExecutionError("A Pollard-Jev worker cannot change provider settings")
        self._config = config
        worker = Path(__file__).with_name("_pollard_jev_worker.py")
        try:
            self._process = subprocess.Popen(
                [str(Path(config.worker_python).resolve()), "-u", "-I", str(worker.resolve())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            raise HandlerExecutionError("Could not start the configured Pollard-Jev worker") from None
        self._lines = Queue()
        process, lines = self._process, self._lines

        def read_lines() -> None:
            try:
                while True:
                    line = process.stdout.readline(_MAX_LINE_BYTES + 1)
                    if not line:
                        break
                    if len(line.encode("utf-8")) > _MAX_LINE_BYTES or not line.endswith("\n"):
                        lines.put(None)
                        return
                    lines.put(line)
            except (OSError, ValueError):
                pass
            finally:
                lines.put(None)

        Thread(target=read_lines, daemon=True).start()

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()

    def _exchange(self, payload: dict, config: ModelConfig) -> dict:
        if self._process is None:
            self._start(config)
        try:
            encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > _MAX_LINE_BYTES:
                raise HandlerExecutionError("Pollard-Jev request exceeds the local protocol size limit")
            process, lines = self._process, self._lines

            def write_request() -> None:
                try:
                    process.stdin.write(encoded + "\n")
                    process.stdin.flush()
                except (OSError, ValueError):
                    lines.put(None)

            # Include blocked pipe writes in the deadline, not just inference.
            Thread(target=write_request, daemon=True).start()
            line = self._lines.get(timeout=config.timeout_seconds)
            if line is None:
                raise HandlerExecutionError("Pollard-Jev worker stopped before producing a response")
            response = json.loads(line)
            if (not isinstance(response, dict) or response.get("protocol_version") != 1
                    or not isinstance(response.get("ok"), bool)):
                raise HandlerExecutionError("Pollard-Jev worker returned an invalid protocol response")
            return response
        except Empty:
            self._stop()
            raise HandlerExecutionError("Pollard-Jev worker timed out; it was stopped and usage is unknown") from None
        except (OSError, ValueError):
            self._stop()
            raise HandlerExecutionError("Pollard-Jev worker communication failed; usage is unknown") from None
        except HandlerExecutionError:
            self._stop()
            raise

    def generate(self, request: ModelRequest, config: ModelConfig) -> HandlerResult:
        with self._lock:
            if self._config is not None and config != self._config:
                raise HandlerExecutionError("A Pollard-Jev worker cannot change provider settings")
            if self._process is None:
                inspected = self._exchange({"op": "inspect"}, config)
                if not inspected["ok"] or inspected.get("package_versions") != _VERSIONS:
                    self._stop()
                    raise HandlerExecutionError("Worker needs pollard-jev==0.13 and pollard==1.6.0")
            response = self._exchange({
                "op": "infer", "request": deepcopy(request.inputs.get("decision_request")),
                "options": {"cache_dir": config.model_cache_dir, "device": config.device,
                            "max_length": config.max_context_tokens},
            }, config)
            try:
                usage = Usage.model_validate(response.get("usage", {}))
            except ValueError:
                usage = Usage()
            if not response["ok"]:
                # Only a fixed code is surfaced. Worker stderr and arbitrary output
                # are never copied into execution records.
                code = response.get("error_code")
                known = {
                    "invalid_request", "stale_request", "stale_after_inference", "missing_observations",
                    "model_load_failed", "inference_failed", "context_limit_exceeded",
                    "invalid_result", "configuration_changed", "invalid_options", "dependency_import_failed",
                    "missing_dependencies", "unsupported_package_versions",
                }
                safe_code = code if isinstance(code, str) and code in known else "worker_failure"
                raise HandlerExecutionError(f"Pollard-Jev failed: {safe_code}", usage=usage)
            result = response.get("result")
            identity = result.get("identity") if isinstance(result, dict) else None
            native = request.inputs.get("decision_request", {})
            if (not isinstance(identity, dict) or identity.get("synthetic") is not False
                    or identity.get("model") != POLLARD_JEV_MODEL
                    or identity.get("provider") != "alexwortega-openjev"
                    or not isinstance(identity.get("model_version"), str)
                    or identity["model_version"].split(";")[0] != _MODEL_REVISION
                    or result.get("request_id") != native.get("request_id")
                    or response.get("package_versions") != _VERSIONS):
                raise HandlerExecutionError("Pollard-Jev returned inconsistent result provenance", usage=usage)
            return HandlerResult(output={
                "provider": "pollard-jev", "model": identity.get("model"),
                "decision": result, "package_versions": response["package_versions"],
                "measurement_basis": response.get("measurement_basis"),
            }, usage=usage)

    def close(self) -> None:
        with self._lock:
            self._stop()
