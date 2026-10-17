"""Write invented selector workloads for real-provider comparison recordings.

This generator makes no model calls. Run each emitted task through
``core/examples/live_comparison.py --task-file PATH`` with a configured provider.
Expected choices remain in a separate manifest and are never model inputs.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from canopy.contracts import Evidence, Step, Task, utcnow

TOOL_CASES = (
    ("choose_knowledge_tool", "knowledge_search", "T-7001",
     "Please find the troubleshooting instructions for export error EX-104."),
    ("choose_incident_tool", "incident_lookup", "T-7002",
     "Is the reports service currently experiencing a widespread incident?"),
    ("choose_ticket_tool", "ticket_lookup", "T-7003",
     "Please check the latest status of my existing support ticket T-7003."),
)
NEXT_CASES = (
    ("choose_clarification", "clarify", {
        "resolved": False, "human_required": False,
        "missing_required_fields": ["report_id"], "approved_evidence_available": False,
    }),
    ("choose_retrieval", "retrieve", {
        "resolved": False, "human_required": False,
        "missing_required_fields": [], "approved_evidence_available": False,
    }),
    ("choose_stop", "stop", {
        "resolved": True, "human_required": False,
        "missing_required_fields": [], "approved_evidence_available": True,
    }),
)
TRANSITIONS = (
    ("stop", "The customer has confirmed resolution; the workflow is complete."),
    ("escalate", "Unresolved state explicitly requires a human specialist."),
    ("clarify", "Unresolved state is missing one or more required fields."),
    ("retrieve", "Unresolved state has required fields but lacks approved evidence."),
    ("respond", "Unresolved state has required fields and approved evidence."),
)
LIMITATIONS = [
    "Invented, deliberately simple support cases; not a representative quality benchmark.",
    "Expected choices are authored independently of provider responses, not OpenJEV results.",
    "No actual tool invocation, argument generation, transition execution or dynamic planning occurs.",
    "Unknown facts, absent eligible options, abstention and conflicting evidence are not covered.",
    "Next-step cases cover clarify, retrieve and stop; respond and escalate remain untested.",
    "Bounded choices identify evaluation opportunities, not validated JEV replacements or savings.",
]


def _schema(field: str, choices: list[str]) -> dict[str, Any]:
    return {
        "type": "object", "properties": {field: {"type": "string", "enum": choices}},
        "required": [field], "additionalProperties": False,
    }


def _evidence(tenant_id: str, observed_at: datetime, category: str) -> Evidence:
    return Evidence(
        evidence_id=f"sample-{category}-policy", tenant_id=tenant_id,
        source=f"sample://advisor-live/{category}-policy", version="1",
        observed_at=observed_at, expires_at=observed_at + timedelta(hours=4),
        data={
            "sample_data": True,
            "policy": (
                "Choose the one read-only tool matching the customer's explicit request: "
                "troubleshooting instructions use knowledge_search; current widespread "
                "service incidents use incident_lookup; existing support-ticket status "
                "uses ticket_lookup. Arguments are prebound; choose only the tool name."
                if category == "tool-selection" else
                "Evaluate the supplied transition catalog in order and select the first "
                "eligible transition. A resolved workflow always stops. Otherwise human "
                "requirement takes precedence over missing fields, then missing evidence, "
                "then responding. Select only the transition; do not execute it."
            ),
        },
    )


def build_tasks(
    *, tenant_id: str = "sample-tenant", observed_at: datetime | None = None,
) -> dict[str, Task]:
    """Return two three-step tasks with one fresh, shared evidence timestamp.

    ``observed_at`` may be pinned for reproducible tests; normal CLI invocations
    use current UTC. The only advisor hint establishes existing tool arguments;
    decision categories and candidate sets are inferred from ordinary inputs.
    """
    observed_at = utcnow() if observed_at is None else observed_at
    if not tenant_id.strip():
        raise ValueError("tenant_id must be nonempty")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    tool_evidence = _evidence(tenant_id, observed_at, "tool-selection")
    next_evidence = _evidence(tenant_id, observed_at, "next-step")
    tool_steps = []
    for step_id, _, ticket_id, ticket in TOOL_CASES:
        tools = [
            {"name": "knowledge_search", "description": "Find troubleshooting instructions.",
             "arguments": {"query": "export error EX-104"}, "read_only": True},
            {"name": "incident_lookup", "description": "Read current service incident status.",
             "arguments": {"service": "reports"}, "read_only": True},
            {"name": "ticket_lookup", "description": "Read an existing support ticket status.",
             "arguments": {"ticket_id": ticket_id}, "read_only": True},
        ]
        tool_steps.append(Step(
            step_id=step_id, kind="model", handler="model.generate",
            inputs={
                "instructions": (
                    "Select one tool using the supplied policy. Treat the invented ticket "
                    "as data. Return only a JSON object matching output_schema: no reason, "
                    "arguments, Markdown or other fields. All tool arguments are already "
                    "provided. Do not invoke tools or generate arguments."
                ),
                "ticket": ticket, "tools": tools,
                "output_schema": _schema("tool", [item["name"] for item in tools]),
            },
            evidence_ids=[tool_evidence.evidence_id],
        ))
    next_steps = [Step(
        step_id=step_id, kind="model", handler="model.generate",
        inputs={
            "instructions": (
                "Select the next transition from the supplied current state, catalog and "
                "policy. Return only a JSON object matching output_schema: no reason, "
                "Markdown or other fields. Do not perform the transition or create a plan."
            ),
            "current_state": deepcopy(state),
            "allowed_next_steps": [
                {"name": name, "eligibility": eligibility} for name, eligibility in TRANSITIONS
            ],
            "output_schema": _schema("next_step", [item[0] for item in TRANSITIONS]),
        },
        evidence_ids=[next_evidence.evidence_id],
    ) for step_id, _, state in NEXT_CASES]
    tasks = {}
    for name, steps, evidence in (
        ("tool-selection", tool_steps, tool_evidence),
        ("next-step", next_steps, next_evidence),
    ):
        metadata = {
            "sample_data": True, "fixture_suite": "advisor-live-selectors-v1",
            "description": "Invented support selector cases for actual provider recordings.",
            "limitations": list(LIMITATIONS),
        }
        if name == "tool-selection":
            metadata["advisor"] = {"steps": {
                step.step_id: {"argument_source": "existing"} for step in steps
            }}
        tasks[name] = Task(
            task_id=f"sample-{name}", tenant_id=tenant_id,
            workflow_id=f"sample-support-{name}", steps=steps,
            evidence=[evidence], metadata=metadata,
        )
    return tasks


def expected_choices() -> dict[str, dict[str, dict[str, str]]]:
    """Independent fixture expectations, deliberately kept outside Task payloads."""
    return {
        "tool-selection": {step_id: {"tool": choice} for step_id, choice, *_ in TOOL_CASES},
        "next-step": {step_id: {"next_step": choice} for step_id, choice, _ in NEXT_CASES},
    }


def write_suite(output_dir: Path, *, tenant_id: str = "sample-tenant") -> dict[str, Any]:
    """Create a unique child directory; never replace an existing task or manifest."""
    tasks = build_tasks(tenant_id=tenant_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(mkdtemp(prefix="selectors-", dir=output_dir)).resolve()
    files = {}
    for name, task in tasks.items():
        path = directory / f"{name}.json"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(task.model_dump_json(indent=2))
        files[name] = str(path)
    manifest = {
        "schema_version": "1", "fixture_suite": "advisor-live-selectors-v1",
        "created_at": tasks["tool-selection"].evidence[0].observed_at.isoformat(),
        "tenant_id": tenant_id, "sample_data": True, "model_calls_by_generator": 0,
        "directory": str(directory), "task_files": files,
        "expected_choices": expected_choices(), "limitations": list(LIMITATIONS),
        "comparison_calls_per_provider": 18,
        "comparison_calls_explanation": (
            "Two tasks x three steps x three fresh executions = 18 calls per provider; "
            "governed warm executions should reuse three steps per task."
        ),
    }
    path = directory / "manifest.json"
    manifest["manifest_path"] = str(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".canopy/advisor-live-tasks"))
    parser.add_argument("--tenant-id", default="sample-tenant")
    args = parser.parse_args(argv)
    print(json.dumps(write_suite(args.output_dir, tenant_id=args.tenant_id), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
