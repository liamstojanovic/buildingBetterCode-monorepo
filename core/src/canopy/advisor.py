"""Bounded, offline discovery over recorded calls; no provider or runtime dispatch.

Grouping identities describe opportunities, never execution-cache equivalence.
Report values are observations, not measured quality or predicted savings.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from time import perf_counter

from ._advisor_detection import StepInspection, inspect_step
from .advisor_contracts import (
    AdvisorAnalysis, AdvisorFinding, AdvisorLimits, AdvisorMetrics, AdvisorRecommendation,
    AdvisorReport, AdvisorSample,
)
from .contracts import Step, StepResult
from .recording import ExecutionStore, RecordedRun, RecordingIntegrityError, canonical_json, fingerprint
from .temporal import relevant_evidence_ids


def _shape(value):
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        # Set of item schemas, not order/length or instance values.
        return {"array": sorted({canonical_json(_shape(item)) for item in value})}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _identity(record: RecordedRun, step: Step, inspection: StepInspection) -> str:
    task, config = record.task, record.config
    evidence_ids = relevant_evidence_ids(task, step.step_id)
    evidence = [
        {"source": item.source, "version": item.version, "shape": _shape(item.data)}
        for item in task.evidence if item.evidence_id in evidence_ids
    ]
    dependency_ids = set(step.depends_on)
    dependencies = [
        {"step_id": item.step_id, "handler": item.handler, "version": item.handler_version,
         "kind": item.kind, "side_effect": item.side_effect}
        for item in task.steps if item.step_id in dependency_ids
    ]
    return fingerprint({
        "analyzer": "offline-advisor-v1", "tenant": task.tenant_id,
        "workflow": task.workflow_id, "step": step.step_id, "handler": step.handler,
        "handler_version": step.handler_version, "side_effect": step.side_effect,
        "model": config.model_fingerprint, "prompt": config.prompt_version,
        "actor": config.actor_id, "permissions": sorted(config.permissions),
        "policy": config.policy, "evidence": sorted(evidence, key=canonical_json),
        "dependencies": sorted(dependencies, key=canonical_json),
        "operation": inspection.grouping,
    })


def _validate_record(record: RecordedRun, tenant_id: str) -> None:
    if not isinstance(record, RecordedRun):
        raise TypeError("advisor input must contain RecordedRun objects")
    task = record.task
    if task.tenant_id != tenant_id or any(item.tenant_id != tenant_id for item in task.evidence):
        raise ValueError("advisor input does not match the requested tenant")
    ExecutionStore._validate_task(task)
    if record.result is None:
        return
    result = record.result
    if (result.tenant_id != tenant_id or result.run_id != record.run_id
            or result.task_id != task.task_id or result.mode != record.config.mode):
        raise ValueError("advisor run/result bindings are inconsistent")
    declared = {step.step_id for step in task.steps}
    identities = [step.step_id for step in result.steps]
    if len(identities) != len(set(identities)) or not set(identities) <= declared:
        raise ValueError("advisor step results do not match the recorded task")
    for step_result in result.steps:
        if step_result.status == "reused" and (
            step_result.usage.model_calls or step_result.usage.input_tokens
            or step_result.usage.output_tokens or not step_result.source_run_id
        ):
            raise ValueError("reused step must reference its source with zero new model usage")
    if canonical_json(sorted(record.steps, key=lambda item: item.step_id)) != canonical_json(
        sorted(result.steps, key=lambda item: item.step_id)
    ):
        raise ValueError("advisor result and recorded steps disagree")


def _merge_finding(first: AdvisorFinding, other: AdvisorFinding) -> AdvisorFinding:
    merged = first.model_copy(deep=True)
    for name in ("signals", "reasons", "blockers", "required_changes"):
        setattr(merged, name, sorted(set(getattr(first, name)) | set(getattr(other, name))))
    merged.partial_substitution |= other.partial_substitution
    severity = {"candidate": 0, "needs_mapping": 1, "unsupported": 2}
    if severity[other.status] > severity[first.status]:
        merged.status, merged.strategy = other.status, other.strategy
    if first.category != other.category:
        merged.category = "unknown"
        merged.status, merged.strategy = "needs_mapping", "retain_llm"
        merged.blockers.append("Observed decision categories disagree within this operation.")
    if first.native_compatibility != other.native_compatibility:
        merged.native_compatibility = "requires_mapping"
        if merged.status == "candidate":
            merged.status = "needs_mapping"
    return merged


@dataclass
class _Group:
    record: RecordedRun
    step: Step
    inspection: StepInspection
    finding: AdvisorFinding
    metrics: AdvisorMetrics = field(default_factory=AdvisorMetrics)
    samples: list[AdvisorSample] = field(default_factory=list)
    input_total: int = 0
    output_total: int = 0
    cost_total: float = 0
    elapsed_total: float = 0

    def add(self, record: RecordedRun, result: StepResult, inspection: StepInspection,
            sample_limit: int) -> None:
        self.finding = _merge_finding(self.finding, inspection.finding)
        metrics, usage = self.metrics, result.usage
        metrics.observations += 1
        setattr(metrics, result.status, getattr(metrics, result.status) + 1)
        metrics.synthetic_observations += int(record.result.synthetic)
        # Reuse contains historical output but incurs no new model dispatch.
        # Never import historical usage from source_run_id or event payloads.
        metrics.model_calls += usage.model_calls
        metrics.unknown_token_observations += int(not usage.tokens_known)
        metrics.unknown_cost_observations += int(usage.cost_usd is None)
        metrics.unknown_elapsed_observations += int(not usage.elapsed_known)
        if usage.tokens_known:
            self.input_total += usage.input_tokens
            self.output_total += usage.output_tokens
        if usage.cost_usd is not None:
            self.cost_total += usage.cost_usd
        if usage.elapsed_known:
            self.elapsed_total += usage.elapsed_ms
        metrics.input_tokens = None if metrics.unknown_token_observations else self.input_total
        metrics.output_tokens = None if metrics.unknown_token_observations else self.output_total
        metrics.cost_usd = None if metrics.unknown_cost_observations else self.cost_total
        metrics.elapsed_ms = None if metrics.unknown_elapsed_observations else self.elapsed_total
        sample = AdvisorSample(
            run_id=record.run_id, step_id=result.step_id, status=result.status,
            mode=record.config.mode, synthetic=record.result.synthetic,
            selected_choice=inspection.selected_choice if result.status == "completed" else None,
        )
        if len(self.samples) < sample_limit:
            self.samples.append(sample)
        else:
            def bucket(item):
                return item.status, item.mode, item.synthetic, item.selected_choice
            counts = Counter(bucket(item) for item in self.samples)
            if bucket(sample) not in counts:
                for index in range(len(self.samples) - 1, -1, -1):
                    if counts[bucket(self.samples[index])] > 1:
                        self.samples[index] = sample
                        break

    def recommendation(self, group_id: str) -> AdvisorRecommendation:
        config = self.record.config
        return AdvisorRecommendation(
            recommendation_id=f"advisor-{group_id.split(':')[-1]}", group_fingerprint=group_id,
            workflow_id=self.record.task.workflow_id, step_id=self.step.step_id,
            handler=self.step.handler, handler_version=self.step.handler_version,
            model_fingerprint=config.model_fingerprint, prompt_version=config.prompt_version,
            grouping_basis=self.inspection.grouping_basis, finding=self.finding,
            observed=self.metrics, samples=self.samples,
            sample_limit_reached=self.metrics.observations > len(self.samples),
        )


def analyze_runs(
    runs: Iterable[RecordedRun], *, tenant_id: str, limits: AdvisorLimits | None = None,
) -> AdvisorReport:
    """Analyze snapshots without mutation, inference, or execution.

    Limits are cooperative between records/steps; they cannot interrupt an
    individual input read or integrity check. Input order controls the bounded
    scan, so a caller seeking recent activity should supply newest records first.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id is required")
    limits = AdvisorLimits() if limits is None else AdvisorLimits.model_validate(limits)
    started = perf_counter()
    analysis = AdvisorAnalysis()
    groups: dict[str, _Group] = {}
    seen = set()

    def limited(reason: str) -> None:
        if reason not in analysis.limit_reasons:
            analysis.limit_reasons.append(reason)

    iterator = iter(runs)
    while True:
        if analysis.scanned_runs >= limits.max_runs:
            # Do not read/verify another source record just to detect truncation.
            # Reaching the cap means additional records may remain.
            limited("max_runs")
            break
        if perf_counter() - started >= limits.max_seconds:
            limited("max_seconds")
            break
        try:
            record = next(iterator)
        except StopIteration:
            break
        analysis.scanned_runs += 1
        _validate_record(record, tenant_id)
        if record.run_id in seen:
            analysis.skipped_duplicate_runs += 1
            continue
        seen.add(record.run_id)
        if record.config.mode == "replay":
            analysis.skipped_replay_runs += 1
            continue
        if record.result is None:
            analysis.skipped_incomplete_runs += 1
            continue
        results = {item.step_id: item for item in record.result.steps}
        if len(record.task.steps) > limits.max_steps_per_run:
            limited("max_steps_per_run")
        for step in record.task.steps[:limits.max_steps_per_run]:
            if perf_counter() - started >= limits.max_seconds:
                limited("max_seconds")
                break
            if step.kind != "model" or step.step_id not in results:
                continue
            analysis.scanned_model_steps += 1
            result = results[step.step_id]
            # Bound structural analysis and report material without trimming a
            # request into a misleading, apparently complete decision example.
            material = {"inputs": step.inputs, "output": result.output,
                        "metadata": record.task.metadata, "evidence": record.task.evidence}
            if len(canonical_json(material)) > limits.max_step_chars:
                analysis.skipped_oversized_steps += 1
                limited("max_step_chars")
                continue
            inspection = inspect_step(record.task, step, result, record.config)
            key = _identity(record, step, inspection)
            if key not in groups:
                if len(groups) >= limits.max_groups:
                    analysis.skipped_group_observations += 1
                    limited("max_groups")
                    continue
                groups[key] = _Group(record, step, inspection, inspection.finding.model_copy(deep=True))
            groups[key].add(record, result, inspection, limits.max_samples_per_group)
    recommendations = [group.recommendation(key) for key, group in groups.items()]

    def rank(item: AdvisorRecommendation):
        measured = getattr(item.observed, limits.rank_by)
        return (measured is None, -(measured or 0), -item.observed.observations, item.group_fingerprint)

    recommendations.sort(key=rank)
    analysis.elapsed_ms = (perf_counter() - started) * 1000
    return AdvisorReport(
        tenant_id=tenant_id, source="python_records", source_integrity="caller_supplied",
        limits=limits, analysis=analysis, recommendations=recommendations,
        limitations=[
            "Structural recommendations are not evaluated replacements or execution authority.",
            "Observed resource totals describe scanned records, not predicted savings or quality.",
            "Replay runs are excluded; reused outputs do not add historical model usage.",
            "Samples are bounded diagnostic references, not a representative evaluation dataset.",
            "Time limits are cooperative; a source read or integrity check can exceed the deadline.",
            "No model calls, routing changes, text adapter, or automatic activation are performed.",
        ],
    )


def analyze_store(
    store: ExecutionStore, *, tenant_id: str, limits: AdvisorLimits | None = None,
) -> AdvisorReport:
    """Read tenant-scoped, integrity-verified records, newest first.

    This function never writes to the store. CLI callers open it read-only;
    Python callers retain ownership of the supplied store's lifetime.
    """
    limits = AdvisorLimits() if limits is None else AdvisorLimits.model_validate(limits)

    def records() -> Iterator[RecordedRun]:
        offset = 0
        remaining = limits.max_runs
        while remaining:
            batch = store.list_runs(tenant_id, limit=min(50, remaining), offset=offset)
            if not batch:
                return
            for record in batch:
                if not store.verify_run(tenant_id, record.run_id).ok:
                    raise RecordingIntegrityError("Advisor source recording failed integrity verification")
                yield record
            offset += len(batch)
            remaining -= len(batch)

    report = analyze_runs(records(), tenant_id=tenant_id, limits=limits)
    report.source, report.source_integrity = "recording_store", "verified_recordings"
    return report
