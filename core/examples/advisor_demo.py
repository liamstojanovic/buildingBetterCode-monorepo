"""Generate real recordings of synthetic decisions and analyze them offline.

Run ``python core/examples/advisor_demo.py --output-dir .canopy/advisor-demo``.
Each invocation creates a fresh child directory with recordings.sqlite3,
advisor-report.json and demo.json. No model weights, credentials or network are
used. The report describes observed Python fixture dispatches, not model quality
or potential OpenJEV savings.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from canopy.advisor import analyze_store
from canopy.advisor_cli import write_report_new
from canopy.contracts import HandlerResult, RunConfig, Step, Task, Usage
from canopy.handlers import HandlerContext, HandlerRegistry
from canopy.recording import ExecutionStore
from canopy.runtime import create_runtime

TENANT_ID = "advisor-demo"


def demo_task() -> Task:
    """Seven illustrative operations; task metadata explicitly marks synthetic data."""
    observed_at = datetime.now(UTC)
    expires_at = observed_at + timedelta(hours=1)
    numeric_request = {
        "request_id": "synthetic-operations-review", "question": "Choose an operational review action.",
        "created_at": observed_at.isoformat(), "valid_until": expires_at.isoformat(),
        "observations": [{
            "observation_id": name, "source": "synthetic://service-metrics", "feature": name,
            "value": value, "unit": unit, "observed_at": observed_at.isoformat(),
            "valid_until": expires_at.isoformat(), "status": "known",
        } for name, value, unit in (
            ("queue_depth", 42, "requests"), ("p95_delay", 12, "seconds"),
            ("queue_growth", 7, "requests_per_minute"),
        )],
        "choices": [
            {"name": "investigate", "hypothesis": "The service indicators support investigating "
             "a growing request backlog.", "parameters": {}},
            {"name": "monitor", "hypothesis": "The service indicators support routine monitoring "
             "without investigating a growing request backlog.", "parameters": {}},
        ],
    }
    descriptions = {
        "review_operations": {
            "category": "business_decision", "choices": ["investigate", "monitor"],
            "output_field": "decision", "evidence_kind": "native",
        },
        "classify_ticket": {
            "category": "business_decision", "choices": ["billing", "technical", "account"],
            "output_field": "category", "evidence_kind": "text",
            "instance_fields": ["ticket", "fixture_output"],
            "candidate_hypotheses": {
                "billing": "The ticket concerns a charge or invoice.",
                "technical": "The ticket concerns a software failure.",
                "account": "The ticket concerns account access or settings.",
            },
        },
        "choose_tool": {
            "category": "tool_selection", "choices": ["lookup_order", "search_docs", "clarify"],
            "output_field": "tool", "evidence_kind": "structured",
            "tool_catalog_version": "demo-tools-v1", "state_schema_version": "demo-state-v1",
            "argument_source": "existing",
            "instance_fields": ["state", "fixture_output"],
        },
        "choose_next_step": {
            "category": "next_step", "choices": ["retrieve", "respond", "clarify"],
            "output_field": "next_step", "evidence_kind": "structured",
            "state_schema_version": "demo-state-v1",
            "instance_fields": ["state", "fixture_output"],
        },
        "write_response": {
            "generation_required": True, "explanation_required": True, "evidence_kind": "text",
        },
        "check_threshold": {
            "category": "business_decision", "choices": ["approve", "review"],
            "output_field": "decision", "evidence_kind": "structured", "deterministic_rule": True,
        },
        "unmapped_decision": {
            "category": "business_decision", "choices": ["accept", "review"],
            "output_field": "decision", "evidence_kind": "unknown",
        },
    }
    inputs = {
        "review_operations": {
            "decision_request": numeric_request,
            "fixture_output": {"decision": "investigate"},
        },
        "classify_ticket": {
            "prompt": "Choose a queue for the supplied invented ticket.",
            "ticket": "Export fails with EX-104.",
            "fixture_output": {"category": "technical"},
        },
        "choose_tool": {
            "prompt": "Select an eligible tool to look up the supplied order.",
            "tools": ["lookup_order", "search_docs", "clarify"],
            "state": {"order_id": "DEMO-42"},
            "fixture_output": {"tool": "lookup_order", "arguments": {"order_id": "DEMO-42"}},
        },
        "choose_next_step": {
            "prompt": "Choose the next authored transition; the order status is missing.",
            "transitions": ["retrieve", "respond", "clarify"],
            "state": {"order_id": "DEMO-42", "order_status_known": False},
            "fixture_output": {"next_step": "retrieve"},
        },
        "write_response": {
            "prompt": "Write a personalized explanation of the next support action.",
            "fixture_output": {"text": "Please share the report identifier for this demo ticket."},
        },
        "check_threshold": {
            "prompt": "Approve amounts at or below 10000 minor units; otherwise request review.",
            "amount_minor": 2500, "threshold_minor": 10000,
            "fixture_output": {"decision": "approve"},
        },
        "unmapped_decision": {
            "prompt": "Select accept or review using an unspecified external business policy.",
            "fixture_output": {"decision": "review"},
        },
    }
    for step_id, descriptor in descriptions.items():
        descriptor.update(template_id=f"demo-{step_id}", template_version="1")
    return Task(
        task_id="advisor-demo-task", tenant_id=TENANT_ID, workflow_id="advisor-demo-workflow",
        steps=[Step(
            step_id=step_id, kind="model", handler="advisor.fixture", handler_version="demo-v1",
            inputs=value, estimated_usage=Usage(input_tokens=0, output_tokens=0),
        ) for step_id, value in inputs.items()],
        metadata={
            "synthetic": True, "sample_data": True,
            "description": "Invented cases and predetermined Python fixture answers; no LLM or "
            "OpenJEV inference. Native request shape does not establish decision quality.",
            "advisor": {"steps": descriptions},
        },
    )


def _fixture(context: HandlerContext) -> HandlerResult:
    return HandlerResult(
        output=context.inputs["fixture_output"], usage=Usage(input_tokens=0, output_tokens=0),
    )


def run_demo(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(mkdtemp(prefix="run-", dir=output_dir)).resolve()
    db = directory / "recordings.sqlite3"
    registry = HandlerRegistry()
    registry.register(
        "advisor.fixture", _fixture, version="demo-v1", synthetic=True, kind="model", cacheable=True,
    )
    task = demo_task()
    config = RunConfig(
        permissions=["execute:*"], model_fingerprint="synthetic-advisor-fixture-v1",
        actor_id="advisor-demo-operator",
    )
    runs = {}
    with closing(create_runtime(str(db), registry=registry)) as runtime:
        for label, mode in (
            ("baseline_first", "baseline"), ("baseline_repeat", "baseline"),
            ("governed_cold", "governed"), ("governed_warm", "governed"),
        ):
            effective = config.model_copy(update={"mode": mode, "cache_enabled": mode == "governed"})
            result = runtime.run_task(task, effective)
            if result.status != "completed":
                raise RuntimeError("synthetic advisor demonstration did not complete")
            runs[label] = result.model_dump(mode="json")
    with ExecutionStore(db, read_only=True) as store:
        report = analyze_store(store, tenant_id=TENANT_ID)
    report_path = directory / "advisor-report.json"
    write_report_new(report_path, report)
    manifest = {
        "schema_version": "1", "demo_type": "canopy.offline_advisor.synthetic_demo",
        "synthetic": True, "tenant_id": TENANT_ID, "artifacts_directory": str(directory),
        "recording_path": str(db), "advisor_report_path": str(report_path),
        "description": "Actual local fixture recordings: zero LLM tokens, measured elapsed time "
        "and dispatch counts. No OpenJEV inference occurs. Findings are unevaluated opportunities, "
        "not measured quality or savings.",
        "task": task.model_dump(mode="json"), "runs": runs,
    }
    (directory / "demo.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".canopy/advisor-demo"))
    args = parser.parse_args(argv)
    manifest = run_demo(args.output_dir)
    print(json.dumps({key: manifest[key] for key in (
        "synthetic", "tenant_id", "artifacts_directory", "recording_path", "advisor_report_path",
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
