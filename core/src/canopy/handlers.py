"""Explicit local callable registration and credential-free synthetic fixtures.

Handler names are opaque registry keys. They are never imported or evaluated.
The synthetic handlers below do no network I/O and do not generate LLM tokens.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from inspect import isasyncgenfunction, iscoroutinefunction, isgeneratorfunction
import re
from typing import Any, Literal

from canopy.contracts import (
    Evidence,
    HandlerResult,
    RunConfig,
    Step,
    Task,
    Usage,
    utcnow,
)


@dataclass(frozen=True)
class HandlerContext:
    """Inputs supplied by the runtime to one registered synchronous handler.

    Cacheable handlers may use only cache-bound data to determine their output.
    Full task/config objects also carry diagnostic values that are not cache keys.
    """

    run_id: str
    task: Task
    step: Step
    config: RunConfig
    inputs: dict[str, Any]
    dependencies: dict[str, Any]


@dataclass(frozen=True)
class HandlerRegistration:
    name: str
    handler: Callable[[HandlerContext], HandlerResult]
    version: str
    synthetic: bool
    side_effect: bool
    kind: Literal["model", "tool"]
    cacheable: bool = False


class HandlerRegistry:
    """One versioned callable per name; registration never replaces an entry.

    Create a new registry when updating a handler version. A registration is a
    trusted application choice, not something an HTTP request can supply.

    ``cacheable=True`` asserts that output depends only on explicit step inputs,
    direct dependency outputs, declared transitive evidence, and configuration
    bound in the cache key. Run/task IDs, mode/budget, clocks, randomness, and
    undeclared external state must not affect that output; use them only for
    diagnostics. Ordinary callables default to non-cacheable because Python
    hidden dependencies cannot be inferred. Non-cacheable does not mean a side
    effect: a read-only handler can still depend on untracked external state.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, HandlerRegistration] = {}

    def register(
        self,
        name: str,
        handler: Callable[[HandlerContext], HandlerResult],
        *,
        version: str = "1",
        synthetic: bool = False,
        side_effect: bool = False,
        kind: Literal["model", "tool"] = "tool",
        cacheable: bool = False,
    ) -> None:
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", name) is None:
            raise ValueError("handler name must be an identifier containing letters, digits, . _ : -")
        if not isinstance(version, str) or not version or version.strip() != version:
            raise ValueError("handler version must be a nonempty string without surrounding spaces")
        if not callable(handler):
            raise ValueError("handler must be callable")
        implementation = handler.__call__
        if any(
            predicate(candidate)
            for predicate in (iscoroutinefunction, isasyncgenfunction, isgeneratorfunction)
            for candidate in (handler, implementation)
        ):
            raise ValueError("handler must be a synchronous callable returning HandlerResult")
        if any(not isinstance(flag, bool) for flag in (synthetic, side_effect, cacheable)):
            raise ValueError("synthetic, side_effect, and cacheable must be booleans")
        if kind not in ("model", "tool"):
            raise ValueError("handler kind must be 'model' or 'tool'")
        if name in self._handlers:
            raise ValueError(f"handler {name!r} is already registered")
        self._handlers[name] = HandlerRegistration(
            name, handler, version, synthetic, side_effect, kind, cacheable
        )

    def resolve(self, name: str, version: str = "1") -> HandlerRegistration:
        try:
            registration = self._handlers[name]
        except (KeyError, TypeError) as error:
            raise ValueError(f"unknown handler {name!r}") from error
        if registration.version != version:
            raise ValueError(
                f"handler {name!r} version mismatch: requested {version!r}, "
                f"registered {registration.version!r}"
            )
        return registration

    def registered_names(self) -> tuple[str, ...]:
        """Return deterministic registry keys for inspection, not remote dispatch."""
        return tuple(sorted(self._handlers))


def _synthetic_catalog(context: HandlerContext) -> HandlerResult:
    if "synthetic-catalog" not in context.step.evidence_ids:
        raise ValueError("synthetic.catalog requires declared synthetic-catalog evidence")
    evidence = next(
        item for item in context.task.evidence if item.evidence_id == "synthetic-catalog"
    )
    return HandlerResult(
        output={
            "sku": context.inputs["sku"],
            "amount_minor": evidence.data["amount_minor"],
            "currency": "USD",
            "source_version": evidence.version,
            "synthetic": True,
        },
        usage=Usage(input_tokens=0, output_tokens=0),
    )


def _synthetic_summary(context: HandlerContext) -> HandlerResult:
    quote = context.dependencies["read_catalog"]
    return HandlerResult(
        output={
            "summary": (
                f"Synthetic quote for {quote['sku']}: "
                f"{quote['amount_minor']} USD minor units."
            ),
            "amount_minor": quote["amount_minor"],
            "synthetic": True,
            "provider": "deterministic-python-fixture",
        },
        usage=Usage(input_tokens=0, output_tokens=0),
    )


def _synthetic_greeting(context: HandlerContext) -> HandlerResult:
    return HandlerResult(
        output={"message": f"Hello, {context.inputs['name']}.", "synthetic": True},
        usage=Usage(input_tokens=0, output_tokens=0),
    )


def synthetic_registry() -> HandlerRegistry:
    """Build an explicit demo registry. No credentials or providers are involved."""
    registry = HandlerRegistry()
    registry.register("synthetic.catalog", _synthetic_catalog, synthetic=True, cacheable=True)
    registry.register(
        "synthetic.summary", _synthetic_summary, synthetic=True, kind="model", cacheable=True
    )
    registry.register("synthetic.greeting", _synthetic_greeting, synthetic=True, cacheable=True)
    return registry


def synthetic_task(
    *,
    source_version: str = "1",
    source_amount_minor: int = 2400,
    tenant_id: str = "demo-tenant",
    observed_at: datetime | None = None,
) -> Task:
    """Make a small synthetic DAG whose independent greeting can stay reusable.

    Reuse the same returned task for a warm run. To simulate a new source snapshot,
    construct another task with a new source version and amount. ``observed_at``
    makes fixture time explicit and allows deterministic caller-controlled tests.
    Token counts are explicitly zero and cost/energy are unknown; the runtime
    measures elapsed time and counts dispatches, including synthetic model dispatches.
    """
    if isinstance(source_amount_minor, bool) or not isinstance(source_amount_minor, int):
        raise ValueError("source_amount_minor must be a nonnegative integer")
    if source_amount_minor < 0:
        raise ValueError("source_amount_minor must be a nonnegative integer")
    if not isinstance(source_version, str) or not source_version.strip():
        raise ValueError("source_version must be a nonempty string")
    observed_at = observed_at or utcnow()
    return Task(
        task_id="synthetic-quote-task",
        tenant_id=tenant_id,
        workflow_id="synthetic-quote-workflow",
        steps=[
            Step(
                step_id="read_catalog",
                kind="tool",
                handler="synthetic.catalog",
                inputs={"sku": "DEMO-001"},
                evidence_ids=["synthetic-catalog"],
                estimated_usage=Usage(input_tokens=0, output_tokens=0),
            ),
            Step(
                step_id="summarize_quote",
                kind="model",
                handler="synthetic.summary",
                depends_on=["read_catalog"],
                estimated_usage=Usage(input_tokens=0, output_tokens=0),
            ),
            Step(
                step_id="greeting",
                kind="tool",
                handler="synthetic.greeting",
                inputs={"name": "Canopy demo"},
                estimated_usage=Usage(input_tokens=0, output_tokens=0),
            ),
        ],
        evidence=[
            Evidence(
                evidence_id="synthetic-catalog",
                tenant_id=tenant_id,
                source="synthetic://catalog",
                version=source_version,
                observed_at=observed_at,
                expires_at=observed_at + timedelta(hours=1),
                data={"amount_minor": source_amount_minor, "synthetic": True},
            )
        ],
        metadata={
            "synthetic": True,
            "description": "Deterministic local fixture; no live model or external tool calls.",
        },
    )
