"""TypeSafe JEV structured decisions through the common model registry seam.

Official wire contract: https://docs.typesafe.ai/api
Response types: https://docs.typesafe.ai/sdk/python/api/types/responses

JEV evaluates a state against typed questions; it does not generate prose. The
caller supplies ``questions`` in the step inputs. The canonical request prompt
becomes the state, including declared evidence/dependencies and instructions.
``max_output_tokens`` has no JEV equivalent and is not sent; ``temperature`` is
rejected instead of silently implying that it changes JEV inference. Usage is
reported from the response, including any nonzero output token measurement.
"""

from copy import deepcopy
import json
import math
import re
from typing import Any

from canopy.contracts import HandlerResult
from canopy.handlers import HandlerExecutionError
from canopy.models import HTTPAdapter, ModelConfig, ModelRequest, _usage

_MAX_BODY_BYTES = 32 * 1024 * 1024
_MAX_DEPTH = 64
_DISTRIBUTION_TOLERANCE = 0.001


class _InvalidJevData(ValueError):
    """Internal validation signal; never contains provider or user content."""


def _json_content(value: Any, *, nullable: bool = False) -> None:
    """Check TypeSafe content without coercion or recursion on untrusted data."""
    if value is None and nullable:
        return
    if not isinstance(value, (str, dict, list)):
        raise _InvalidJevData
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > _MAX_DEPTH:
            raise _InvalidJevData
        if item is None or isinstance(item, (str, bool)):
            continue
        if isinstance(item, (int, float)):
            if not math.isfinite(item):
                raise _InvalidJevData
            continue
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise _InvalidJevData
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        else:
            raise _InvalidJevData


def _questions(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise _InvalidJevData
    for question_id, question in value.items():
        if not isinstance(question_id, str) or not question_id:
            raise _InvalidJevData
        if not isinstance(question, dict):
            raise _InvalidJevData
        if set(question) - {"type", "instructions", "criteria"}:
            raise _InvalidJevData
        question_type = question.get("type")
        if question_type not in ("noul", "choice", "score"):
            raise _InvalidJevData
        # The REST contract requires instructions, although newer SDKs also
        # allow criteria-only questions. This adapter deliberately supports the
        # documented REST subset and validates it before spending a request.
        _json_content(question.get("instructions"))
        criteria = question.get("criteria")
        if question_type == "noul":
            if criteria is None:
                continue
            if not isinstance(criteria, dict) or set(criteria) - {"true", "false"}:
                raise _InvalidJevData
            for description in criteria.values():
                _json_content(description, nullable=True)
        elif question_type == "choice":
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
                raise _InvalidJevData
            for option, description in criteria.items():
                if not isinstance(option, str) or not option:
                    raise _InvalidJevData
                _json_content(description, nullable=True)
        else:
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise _InvalidJevData
            for description in criteria:
                _json_content(description)
    return deepcopy(value)


def _number(value: Any, *, upper: float = 1) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _InvalidJevData
    if not math.isfinite(value) or not 0 <= value <= upper:
        raise _InvalidJevData
    return value


def _probabilities(value: Any, keys: set[str]) -> dict[str, int | float]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _InvalidJevData
    probabilities = {key: _number(probability) for key, probability in value.items()}
    if not math.isclose(
        sum(probabilities.values()), 1, rel_tol=0, abs_tol=_DISTRIBUTION_TOLERANCE
    ):
        raise _InvalidJevData
    return probabilities


def _answers(
    value: Any, questions: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(questions):
        raise _InvalidJevData
    result: dict[str, dict[str, Any]] = {}
    for question_id, question in questions.items():
        answer = value[question_id]
        question_type = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != question_type:
            raise _InvalidJevData
        if question_type == "noul":
            result[question_id] = {"type": "noul", "noul": _number(answer.get("noul"))}
            continue
        confidence = _number(answer.get("confidence"))
        if question_type == "choice":
            probabilities = _probabilities(answer.get("probabilities"), set(question["criteria"]))
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in probabilities:
                raise _InvalidJevData
            if probabilities[choice] + _DISTRIBUTION_TOLERANCE < max(probabilities.values()):
                raise _InvalidJevData
            result[question_id] = {
                "type": "choice", "choice": choice,
                "probabilities": probabilities, "confidence": confidence,
            }
        else:
            legend = {str(index): item for index, item in enumerate(question["criteria"])}
            if answer.get("legend") != legend:
                raise _InvalidJevData
            probabilities = _probabilities(answer.get("probabilities"), set(legend))
            result[question_id] = {
                "type": "score", "score": _number(answer.get("score"), upper=len(legend) - 1),
                "legend": deepcopy(legend), "probabilities": probabilities,
                "confidence": confidence,
            }
    return result


def _measured_tokens(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class JevAdapter(HTTPAdapter):
    """One TypeSafe System One request per dispatch, with no implicit retry.

    This adapter supports text/JSON state and Noul, Choice, and Score questions.
    It does not execute actions or turn model confidence into authorization.
    """

    revision = "jev-systemone-v1"

    def generate(self, request: ModelRequest, config: ModelConfig) -> HandlerResult:
        if config.temperature is not None:
            raise HandlerExecutionError("JEV does not support temperature configuration")
        try:
            questions = _questions(request.inputs.get("questions"))
            payload = {"state": request.prompt, "model": config.model, "questions": questions}
            if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > _MAX_BODY_BYTES:
                raise _InvalidJevData
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise HandlerExecutionError("JEV requires valid typed questions and a bounded JSON state") from None

        data = self._post(config, "/v1/systemone", payload)
        provider_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        counts = {
            "input_tokens": _measured_tokens(provider_usage.get("input_tokens")),
            "output_tokens": _measured_tokens(provider_usage.get("output_tokens")),
        }
        usage = _usage(counts["input_tokens"], counts["output_tokens"])
        try:
            model = data.get("model")
            if not isinstance(model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", model) is None:
                raise _InvalidJevData
            answers = _answers(data.get("answers"), questions)
        except (TypeError, ValueError, OverflowError):
            raise HandlerExecutionError(
                "JEV returned an invalid or incomplete decision response",
                usage=usage,
                output={"provider": "jev", "status": "invalid_response", "provider_usage": counts},
            ) from None
        return HandlerResult(
            output={"provider": "jev", "model": model, "answers": answers, "provider_usage": counts},
            usage=usage,
        )
