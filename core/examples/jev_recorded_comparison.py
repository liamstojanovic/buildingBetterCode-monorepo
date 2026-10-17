"""Evaluate local OpenJEV mappings against retained, invented-data LLM recordings.

This is a retrospective, selection-only smoke evaluation. Original evidence is
never refreshed or used to authorize actions. New validity applies only to the
observation of historical fixture content presented to the local classifier.
The CLI loads a pinned checkpoint through the existing isolated Pollard-Jev
adapter; it never contacts a hosted model or executes a selected action.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
from statistics import median
from time import perf_counter
from typing import Any

from canopy.advisor_cli import read_comparison_report
from canopy.contracts import HandlerResult, Task, utcnow
from canopy.models import ModelConfig, ModelRequest, POLLARD_JEV_MODEL
from canopy.pollard_jev import PollardJevAdapter
from canopy.recording import canonical_json


FORMAT = "canopy-jev-recorded-comparison/v1"
FIELDS = {"business_decision": "priority", "tool_selection": "tool", "next_step": "next_step"}
LIMITATIONS = [
    "Seven invented support cases are a smoke evaluation, not a representative quality benchmark.",
    "The 63 hosted calls repeat seven cases; they are not 63 independent examples.",
    "JEV is evaluated retrospectively against preserved historical data; fresh evaluation validity "
    "does not renew original operational evidence or authorize any tool or workflow action.",
    "Business priority is compared separately from the original explanation; JEV does not replace "
    "the required reason field. Hosted explanations remain in the source and this report.",
    "Raw classifier choice and confidence/eligibility gate acceptance are reported separately. "
    "Abstention is not counted as a correct automatic replacement.",
    "Next-step states have a deterministic rule baseline. A classifier adds no demonstrated value "
    "for these explicit Boolean states.",
    "Local NLI input-pair tokens and hosted generative tokens have different meanings and are "
    "not interchangeable. Cost and energy savings are unknown.",
    "Historical hosted latency and present local latency are not a controlled speed benchmark. "
    "Cold worker/model loading is included in the first end-to-end call only.",
    "No JEV choice is automatically deployed, activated in a workflow, or treated as validated "
    "for unsampled inputs.",
]


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite JSON number")


def _json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def assess_output(text: str, *, field: str, expected: str, category: str) -> dict[str, Any]:
    """Allow one whole fenced JSON object for semantic comparison, never for strict JSON."""
    stripped = text.strip()
    bare, parsed = True, None
    try:
        parsed = _json(stripped)
    except (ValueError, TypeError):
        bare = False
        fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", stripped, re.IGNORECASE)
        if fenced:
            try:
                parsed = _json(fenced.group(1))
            except (ValueError, TypeError):
                pass
    parsed = parsed if isinstance(parsed, dict) else None
    choice = parsed.get(field) if parsed else None
    choice = choice if isinstance(choice, str) else None
    bare = bare and parsed is not None
    schema_ok = False
    if parsed is not None:
        if category == "business_decision":
            schema_ok = (set(parsed) == {field, "reason"} and isinstance(parsed.get("reason"), str)
                         and bool(parsed["reason"].strip()) and choice in {"high", "normal"})
        else:
            schema_ok = set(parsed) == {field} and choice is not None
    return {
        "normalized_choice": choice, "expected_choice": expected,
        "normalized_match": choice == expected, "bare_json_object": bare,
        "strict_contract_match": bare and schema_ok and choice == expected,
        "parsed_output": parsed,
        "reason_retained": parsed.get("reason") if parsed and category == "business_decision" else None,
    }


def _relative_file(directory: Path, value: str) -> Path:
    path = directory / value
    if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError("source path must be a file inside the retained recording directory")
    return path


def collect_cases(directory: Path) -> dict[str, Any]:
    """Validate retained imports and return seven inputs and all paid-call observations."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    manifest = _json(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "canopy-advisor-live-demo/v1" or manifest.get("sample_data") is not True:
        raise ValueError("requires the retained invented-data advisor demo")
    files = manifest["files"]
    # Every listed file is immutable. Inventory format is intentionally inherited
    # from export_advisor_live_demo, including README and independent expectations.
    if isinstance(files, list):
        files = {item["path"]: item["sha256"] for item in files}
    for name, expected_digest in files.items():
        actual_digest = digest(_relative_file(directory, name))
        expected_digest = expected_digest.get("sha256") if isinstance(expected_digest, dict) else expected_digest
        if actual_digest != expected_digest:
            raise ValueError(f"retained source digest mismatch: {name}")
    expected_path = _relative_file(directory, "expected-choices.json")
    expectations = _json(expected_path.read_text(encoding="utf-8"))["expected_choices"]
    cases: dict[str, dict] = {}
    calls, sources = [], []
    reused = 0
    for source in manifest["sources"]:
        relative = source["files"]["comparison"]
        path = _relative_file(directory, relative)
        if relative not in files:
            raise ValueError("comparison source is absent from immutable inventory")
        runs = read_comparison_report(path, tenant_id=manifest["tenant_id"])
        raw = _json(path.read_text(encoding="utf-8"))
        task = runs[0].task
        if task.metadata.get("sample_data") is not True:
            raise ValueError("comparison is not explicitly invented sample data")
        category = source["category"]
        field = FIELDS[category]
        source_record = {"path": relative, "sha256": digest(path), "provider": source["provider"],
                         "historical_evidence": [item.model_dump(mode="json") for item in task.evidence]}
        sources.append(source_record)
        for step in task.steps:
            if step.side_effect or step.kind != "model":
                raise ValueError("only selection-only model steps may be evaluated")
            case_id = f"{category}:{step.step_id}"
            # Provider binding and original observation timestamps differ. Compare
            # semantic source content, while retaining all timestamped evidence above.
            semantics = {"inputs": step.inputs, "evidence": [
                {"source": evidence.source, "version": evidence.version, "data": evidence.data}
                for evidence in task.evidence if evidence.evidence_id in step.evidence_ids
            ]}
            semantic_sha256 = sha256(canonical_json(semantics).encode()).hexdigest()
            if case_id in cases:
                if cases[case_id]["semantic_sha256"] != semantic_sha256:
                    raise ValueError("the same case differs semantically across providers")
                cases[case_id]["source_paths"].append(relative)
                continue
            if category == "business_decision":
                expected = "high"
                basis = ("Authored smoke expectation from the recorded policy: export workflow is "
                         "blocked and the stated deadline is tomorrow (within 48 hours). This "
                         "expectation was declared for this evaluation, not supplied to JEV, and "
                         "is not an independent held-out human annotation.")
            else:
                expected = expectations[category.replace("_", "-")][step.step_id][field]
                basis = "Preauthored selector fixture expectation in expected-choices.json."
            cases[case_id] = {
                "case_id": case_id, "category": category, "step_id": step.step_id,
                "field": field, "expected_choice": expected, "expectation_basis": basis,
                "semantic_sha256": semantic_sha256, "source_paths": [relative],
                "task": task.model_dump(mode="json"),
            }
        labels = {value["run_id"]: label for label, value in raw["runs"].items()}
        for run in runs:
            if run.result.synthetic or run.result.status != "completed":
                raise ValueError("requires completed actual hosted recordings")
            for step in run.steps:
                if step.status == "reused":
                    reused += 1
                    continue
                if step.status != "completed" or step.usage.model_calls != 1:
                    raise ValueError("each retained non-reused step must be one completed model call")
                case_id = f"{category}:{step.step_id}"
                output = step.output
                if not isinstance(output, dict) or output.get("provider") != source["provider"]:
                    raise ValueError("provider identity mismatch")
                assessment = assess_output(output["text"], field=field,
                                           expected=cases[case_id]["expected_choice"], category=category)
                calls.append({
                    "case_id": case_id, "category": category, "source_path": relative,
                    "run_id": run.run_id, "run_label": labels[run.run_id], "step_id": step.step_id,
                    "provider": output["provider"], "model": output["model"],
                    "output": output, "usage": step.usage.model_dump(mode="json"), **assessment,
                })
    if len(cases) != 7 or len(calls) != 63 or len(sources) != 9 or reused != 21:
        raise ValueError("expected exactly seven cases, nine comparisons, 63 calls and 21 reused steps")
    return {
        "source_manifest_sha256": digest(manifest_path), "source_files": sources,
        "expectations_sha256": digest(expected_path), "cases": list(cases.values()),
        "hosted_calls": calls, "excluded_reused_steps": reused,
    }


def _metric(values: list[float]) -> dict[str, float | int | None]:
    return {"count": len(values), "min_ms": min(values) if values else None,
            "median_ms": median(values) if values else None, "max_ms": max(values) if values else None}


def evaluate_cases(
    data: dict[str, Any], config: ModelConfig, *, repeats: int = 2,
    evaluated_at: datetime | None = None, adapter=None, journal: Path | None = None,
) -> dict[str, Any]:
    """Run one persistent local worker. Adapter injection is a Python test seam only."""
    from canopy.jev_mappings import apply_mapping_result, map_recorded_step, next_step_rule

    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be between one and ten")
    evaluated_at = evaluated_at or utcnow()
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if config.provider != "pollard-jev":
        raise ValueError("evaluation only supports the pinned local Pollard-Jev adapter")
    adapter = adapter or PollardJevAdapter()
    mappings = {}
    cases = []
    for source_case in data["cases"]:
        task = Task.model_validate(source_case["task"])
        step = next(step for step in task.steps if step.step_id == source_case["step_id"])
        mapping = map_recorded_step(task, step, evaluated_at=evaluated_at)
        mappings[source_case["case_id"]] = mapping
        case = {key: value for key, value in source_case.items() if key != "task"}
        case.update(mapping_id=mapping.mapping_id, mapping_version=mapping.mapping_version,
                    mapping_source_sha256=mapping.source_sha256, text_request=mapping.text_request,
                    source_evidence=mapping.source_evidence,
                    requires_explanation=mapping.requires_explanation, jev_trials=[])
        if case["category"] == "next_step":
            rule_start = perf_counter()
            rule_choice = next_step_rule(step.inputs["current_state"])
            case["rule_baseline"] = {
                "choice": rule_choice, "expected_match": rule_choice == case["expected_choice"],
                "elapsed_ms": (perf_counter() - rule_start) * 1000, "model_calls": 0,
                "basis": "Ordered explicit state policy: stop, escalate, clarify, retrieve, respond.",
            }
        cases.append(case)
    journal_handle = Path(journal).open("x", encoding="utf-8", newline="\n") if journal else None

    def journal_event(event: dict) -> None:
        if journal_handle is not None:
            journal_handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            journal_handle.flush()

    try:
        journal_event({"event": "evaluation_started", "at": utcnow().isoformat(),
                       "source_manifest_sha256": data["source_manifest_sha256"],
                       "evaluation_kind": "retrospective_historical_sample_replay",
                       "distinct_cases": len(cases), "repeats": repeats})
        for repeat in range(repeats):
            for index, case in enumerate(cases):
                mapping = mappings[case["case_id"]]
                task = mapping.as_task()
                identity = {"case_id": case["case_id"], "trial": repeat + 1,
                            "mapping_id": mapping.mapping_id, "source_sha256": mapping.source_sha256,
                            "worker_cold": repeat == 0 and index == 0}
                journal_event({"event": "trial_started", "at": utcnow().isoformat(), **identity})
                started = perf_counter()
                result = None
                try:
                    result: HandlerResult = adapter.generate(
                        ModelRequest(prompt="", inputs=task.steps[0].inputs), config,
                    )
                    elapsed = (perf_counter() - started) * 1000
                    decision = result.output["decision"]
                    if decision["identity"].get("synthetic") is not False:
                        raise ValueError("synthetic outputs cannot be included as actual JEV evaluations")
                    projection = apply_mapping_result(mapping, decision)
                except Exception as error:
                    usage = getattr(error, "usage", None)
                    journal_event({
                        "event": "trial_failed", "at": utcnow().isoformat(), **identity,
                        "end_to_end_elapsed_ms": (perf_counter() - started) * 1000,
                        # Do not persist arbitrary exception text or secrets. The
                        # command still raises the original exception to its caller.
                        "error_type": type(error).__name__,
                        "usage": usage.model_dump(mode="json") if hasattr(usage, "model_dump") else None,
                        "result_before_validation": result.model_dump(mode="json") if result else None,
                    })
                    raise
                raw_choice = projection["raw_choice"]
                accepted = projection["accepted_choice"]
                trial_record = {
                    "trial": repeat + 1, "worker_cold": repeat == 0 and index == 0,
                    "end_to_end_elapsed_ms": elapsed,
                    "result": result.model_dump(mode="json"), "projection": projection,
                    "raw_expected_match": raw_choice == case["expected_choice"],
                    "accepted_expected_match": accepted == case["expected_choice"],
                }
                case["jev_trials"].append(trial_record)
                journal_event({"event": "trial_completed", "at": utcnow().isoformat(),
                               **identity, "result": trial_record})
    finally:
        try:
            adapter.close()
        finally:
            if journal_handle is not None:
                journal_handle.close()
    calls = []
    by_id = {case["case_id"]: case for case in cases}
    for source_call in data["hosted_calls"]:
        trial = by_id[source_call["case_id"]]["jev_trials"][0]
        calls.append({
            **source_call,
            "jev_first_trial_choice": trial["projection"]["raw_choice"],
            "jev_first_trial_accepted_choice": trial["projection"]["accepted_choice"],
            "jev_raw_agrees_with_hosted": trial["projection"]["raw_choice"] == source_call["normalized_choice"],
            "jev_accepted_agrees_with_hosted": (
                trial["projection"]["accepted_choice"] is not None
                and trial["projection"]["accepted_choice"] == source_call["normalized_choice"]
            ),
        })
    trials = [trial for case in cases for trial in case["jev_trials"]]
    first_trials = [case["jev_trials"][0] for case in cases]
    phase_timings = [trial["result"]["output"].get("phase_timings_ms", {}) for trial in trials]
    summary = {
        "distinct_cases": len(cases), "hosted_calls": len(calls), "jev_trials": len(trials),
        "excluded_hosted_reused_steps": data["excluded_reused_steps"],
        "hosted_normalized_expected_matches": sum(call["normalized_match"] for call in calls),
        "hosted_bare_json_objects": sum(call["bare_json_object"] for call in calls),
        "hosted_strict_contract_matches": sum(call["strict_contract_match"] for call in calls),
        "jev_first_trial_raw_expected_matches": sum(trial["raw_expected_match"] for trial in first_trials),
        "jev_first_trial_accepted_expected_matches": sum(trial["accepted_expected_match"] for trial in first_trials),
        "jev_raw_agreements_with_hosted_calls": sum(call["jev_raw_agrees_with_hosted"] for call in calls),
        "jev_accepted_agreements_with_hosted_calls": sum(call["jev_accepted_agrees_with_hosted"] for call in calls),
        "jev_dispositions": dict(Counter(trial["projection"]["disposition"] for trial in first_trials)),
        "jev_warm_end_to_end_latency": _metric([trial["end_to_end_elapsed_ms"] for trial in trials if not trial["worker_cold"]]),
        "jev_cold_end_to_end_ms": trials[0]["end_to_end_elapsed_ms"],
        "jev_model_load_ms": sum(item.get("model_load", 0) for item in phase_timings),
        "jev_inference_latency": _metric([item["inference"] for item in phase_timings if "inference" in item]),
        "hosted_recorded_latency": _metric([call["usage"]["elapsed_ms"] for call in calls if call["usage"]["elapsed_known"]]),
        "cost_savings_usd": None, "energy_savings_j": None,
    }
    return {
        "format": FORMAT, "generated_at": utcnow().isoformat(), "evaluated_at": evaluated_at.isoformat(),
        "evaluation_kind": "retrospective_historical_sample_replay", "sample_data": True,
        "synthetic": False, "automatic_activation": False,
        "source_manifest_sha256": data["source_manifest_sha256"],
        "expectations_sha256": data["expectations_sha256"], "source_files": data["source_files"],
        "configuration": {"provider": config.provider, "model": config.model,
                          "device": config.device, "max_context_tokens": config.max_context_tokens,
                          "repeats": repeats, "adapter_revision": adapter.revision},
        "summary": summary, "cases": cases, "hosted_calls": calls, "limitations": LIMITATIONS,
    }


def render_html(report: dict[str, Any]) -> str:
    """Self-contained review surface; no JavaScript, service, model, or network needed."""
    def e(value):
        return escape(str(value))
    summary = report["summary"]
    rows = []
    for case in report["cases"]:
        trial = case["jev_trials"][0]
        projection = trial["projection"]
        hosted = [call for call in report["hosted_calls"] if call["case_id"] == case["case_id"]]
        disposition = {"accept_selection": "Accepted selection", "defer": "Deferred"}.get(
            projection["disposition"], projection["disposition"].replace("_", " "),
        )
        rows.append("<tr>" + "".join(f"<td>{e(value)}</td>" for value in (
            case["category"].replace("_", " "), case["step_id"], case["expected_choice"],
            projection["raw_choice"], projection["accepted_choice"] or "abstain",
            disposition, f"{sum(call['normalized_match'] for call in hosted)}/{len(hosted)}",
        )) + "</tr>")
    details = []
    for case in report["cases"]:
        trial = case["jev_trials"][0]
        records = []
        for call in report["hosted_calls"]:
            if call["case_id"] != case["case_id"]:
                continue
            records.append("<tr>" + "".join(f"<td>{e(value)}</td>" for value in (
                call["provider"], call["run_label"], call["normalized_choice"],
                "match" if call["normalized_match"] else "MISMATCH",
                "bare JSON" if call["bare_json_object"] else "format violation",
            )) + f"<td><pre>{e(call['output']['text'])}</pre></td></tr>")
        details.append(
            f"<details><summary>{e(case['step_id'])} · {e(case['mapping_id'])}</summary>"
            f"<p>{e(case['expectation_basis'])}</p>"
            f"<p>Explanation still required: {e(case['requires_explanation'])}. "
            f"Rule baseline: {e(case.get('rule_baseline', {}).get('choice', 'not applicable'))}.</p>"
            "<details><summary>Mapped model input · premise, hypotheses and source hash</summary>"
            "<p>The mapping supplies historical facts and candidate hypotheses. The new validity "
            "interval applies to this retrospective evaluation only.</p>"
            f"<pre>{e(json.dumps(case['text_request'], indent=2))}</pre></details>"
            f"<h3>Actual JEV result and gate</h3><pre>{e(json.dumps(trial, indent=2))}</pre>"
            "<h3>Preserved hosted calls</h3><div class='scroll'><table><thead><tr>"
            "<th>Provider</th><th>Run</th><th>Choice</th><th>Expectation</th><th>Format</th>"
            "<th>Original text</th></tr></thead><tbody>" + "".join(records) + "</tbody></table></div></details>"
        )
    limitations = "".join(f"<li>{e(item)}</li>" for item in report["limitations"])
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canopy · JEV vs recorded LLM decisions</title><style>
:root{color-scheme:dark}body{font:16px/1.55 system-ui,sans-serif;background:#101b20;color:#ecf4f3;
margin:0 auto;padding:32px;max-width:1380px}h1{line-height:1.15;font-size:clamp(28px,4vw,46px)}
a{color:#81dfbd}small,.muted{color:#b2c7c8}.cards{display:flex;flex-wrap:wrap;gap:16px;margin:24px 0}
.card{background:#1b3035;border:1px solid #38545a;border-radius:12px;padding:18px;min-width:200px}
.card strong{font-size:30px;display:block}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;
margin:16px 0}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #38545a;padding:12px}
th{color:#81dfbd}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;max-width:900px}
details{padding:16px;background:#15272c;border:1px solid #38545a;border-radius:8px;margin:14px 0}
summary{cursor:pointer;font-weight:650}li{margin:8px 0}.notice{border-left:4px solid #e4b665;padding:12px 20px}
</style><main><p><a href="../index.html">← Back to the combined demo</a></p>
<p class="muted">CANOPY / RETROSPECTIVE LOCAL INFERENCE</p>
<h1>Which decisions can JEV take over?</h1>
<p>Actual local OpenJEV inference compared with retained calls from OpenAI, Anthropic and Gemini.
Seven invented cases cover business priority, tool selection and next-step selection.</p>
<p><a href="comparison.json">Download full results JSON</a></p>""" + (
        f"<div class='cards'><div class='card'><strong>{summary['distinct_cases']}</strong>distinct sample cases</div>"
        f"<div class='card'><strong>{summary['hosted_calls']}</strong>retained hosted calls</div>"
        f"<div class='card'><strong>{summary['jev_first_trial_raw_expected_matches']}/7</strong>JEV raw choices match</div>"
        f"<div class='card'><strong>{summary['jev_first_trial_accepted_expected_matches']}/7</strong>gate-accepted choices match</div></div>"
        "<p class='notice'>Selection accuracy is separate from safe replacement. Raw scores, gate abstentions, "
        "business explanations and the deterministic next-step baseline remain visible. Nothing is activated.</p>"
        "<h2>First JEV trial per distinct case</h2><div class='scroll'><table><thead><tr>"
        "<th>Category</th><th>Case</th><th>Expected</th><th>Raw JEV</th><th>Accepted JEV</th>"
        "<th>Disposition</th><th>Hosted matches</th></tr></thead><tbody>" + "".join(rows) +
        "</tbody></table></div><h2>Inspect scores, gates and original responses</h2>" + "".join(details) +
        "<h2>Timing and interpretation</h2><p>First call includes worker startup and model loading. "
        "Warm calls reuse the loaded classifier. Hosted latency was recorded previously; these are "
        "different machines and execution conditions, not a controlled speed comparison.</p>"
        f"<p><strong>Model loading:</strong> {e(round(summary['jev_model_load_ms'], 2))} ms "
        "(measured separately inside the worker).</p>"
        f"<pre>{e(json.dumps({key: value for key, value in summary.items() if 'latency' in key or 'end_to_end' in key or 'model_load' in key}, indent=2))}</pre>"
        "<h2>Scope and limitations</h2><ul>" + limitations + "</ul>"
        f"<p class='muted'>Generated {e(report['generated_at'])}. Model: {e(report['configuration']['model'])}.</p>"
        "</main></html>"
    )


def write_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    (output_dir / "comparison.json").write_text(payload, encoding="utf-8", newline="\n")
    (output_dir / "index.html").write_text(render_html(report), encoding="utf-8", newline="\n")
    files = {name: digest(output_dir / name) for name in ("comparison.json", "index.html")}
    (output_dir / "manifest.json").write_text(json.dumps({
        "format": "canopy-jev-demo/v1", "sample_data": True, "synthetic": False,
        "files": files, "source_manifest_sha256": report["source_manifest_sha256"],
    }, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, default=Path(__file__).parent / "recordings" / "advisor-live-2026-10-17")
    parser.add_argument("--output-dir", type=Path, required=True, help="New output directory; never overwritten")
    parser.add_argument("--worker-python", required=True)
    parser.add_argument("--model-cache-dir")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-context-tokens", type=int, default=4096)
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--journal", type=Path,
                        help="Optional new JSONL file in an existing directory; flush each trial and any failure")
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output directory already exists")
    data = collect_cases(args.recordings)
    config = ModelConfig(provider="pollard-jev", model=POLLARD_JEV_MODEL,
                         worker_python=args.worker_python, model_cache_dir=args.model_cache_dir,
                         device=args.device, max_context_tokens=args.max_context_tokens,
                         timeout_seconds=args.timeout_seconds)
    report = evaluate_cases(data, config, repeats=args.repeats, journal=args.journal)
    write_report(report, args.output_dir)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
