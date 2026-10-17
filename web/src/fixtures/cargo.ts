import {
  unknownUsage,
  type ComparisonBundle,
  type Evidence,
  type Recording,
  type RunEvent,
  type RunResult,
  type Step,
  type StepResult,
  type Task,
} from "../api/types";

const observed = "2026-10-17T16:00:00Z";
const source = (
  id: string,
  name: string,
  data: Record<string, unknown>,
): Evidence => ({
  evidence_id: id,
  tenant_id: "demo-tenant",
  source: `synthetic://portmiami/${name}`,
  version: "1",
  observed_at: observed,
  expires_at: null,
  half_life_seconds: null,
  quality: 1,
  data,
});
const step = (
  id: string,
  kind: Step["kind"],
  dependencies: string[],
  evidence: string[],
): Step => ({
  step_id: id,
  kind,
  handler: `cargo.${id}`,
  handler_version: "1",
  inputs: {},
  depends_on: dependencies,
  evidence_ids: evidence,
  side_effect: false,
  estimated_usage: unknownUsage(),
});
export const cargoTask: Task = {
  schema_version: "1",
  task_id: "mock-cargo-packet",
  tenant_id: "demo-tenant",
  workflow_id: "cargo-review-v1",
  metadata: {
    synthetic: true,
    description:
      "Frontend mock: illustrative cargo review, no model or business actions.",
  },
  evidence: [
    source("invoice", "commercial-invoice", {
      shipment: "MIA-0248",
      invoice: "INV-2026-0842",
      amount_minor: 4825000,
      currency: "USD",
      packages: 240,
    }),
    source("manifest", "bill-of-lading", {
      shipment: "MIA-0248",
      container: "CNPY 482019-6",
      packages: 240,
      destination: "PortMiami",
    }),
    source("certificate", "insurance-certificate", {
      shipment: "MIA-0248",
      certificate: "COI-1094",
      coverage_minor: 6000000,
      currency: "USD",
    }),
  ],
  steps: [
    step("extract_invoice", "model", [], ["invoice"]),
    step("extract_manifest", "model", [], ["manifest"]),
    step("extract_certificate", "model", [], ["certificate"]),
    step("reconcile", "tool", ["extract_invoice", "extract_manifest"], []),
    step("coverage", "tool", ["extract_certificate"], []),
    step("review_summary", "model", ["reconcile", "coverage"], []),
    step("prepare_packet", "tool", ["review_summary"], []),
  ],
};

export const stepLabels: Record<
  string,
  { title: string; subtitle: string; agent: string }
> = {
  extract_invoice: {
    title: "Read invoice",
    subtitle: "Amounts · line items",
    agent: "Document agent",
  },
  extract_manifest: {
    title: "Read bill of lading",
    subtitle: "Cargo · destination",
    agent: "Document agent",
  },
  extract_certificate: {
    title: "Read insurance",
    subtitle: "Coverage · validity",
    agent: "Document agent",
  },
  reconcile: {
    title: "Reconcile shipment",
    subtitle: "Match invoice + manifest",
    agent: "Validation step",
  },
  coverage: {
    title: "Check coverage",
    subtitle: "Validate certificate",
    agent: "Validation step",
  },
  review_summary: {
    title: "Review packet",
    subtitle: "Evidence-backed summary",
    agent: "Review agent",
  },
  prepare_packet: {
    title: "Prepare handoff",
    subtitle: "Ready for human review",
    agent: "Output step",
  },
  read_catalog: {
    title: "Read catalog",
    subtitle: "Declared source evidence",
    agent: "Synthetic tool",
  },
  summarize_quote: {
    title: "Summarize quote",
    subtitle: "Deterministic fixture",
    agent: "Synthetic model",
  },
  greeting: {
    title: "Independent greeting",
    subtitle: "Unaffected by catalog",
    agent: "Synthetic tool",
  },
};
export function labelFor(step: Step) {
  return (
    stepLabels[step.step_id] ?? {
      title: step.step_id.replaceAll("_", " "),
      subtitle: step.handler,
      agent: step.kind === "model" ? "Model step" : "Tool step",
    }
  );
}

export type Scenario =
  "cold" | "warm" | "changed" | "unrelated" | "approval" | "refused" | "failed";
export const scenarioLabels: Record<Scenario, string> = {
  cold: "First encounter",
  warm: "Same packet, again",
  changed: "Certificate updated",
  unrelated: "Unrelated source updated",
  approval: "Approval required",
  refused: "Budget refusal",
  failed: "Handler failure",
};

export function scenarioLabel(scenario: Scenario, task: Task): string {
  const cargo = task.evidence.some(
    (evidence) => evidence.evidence_id === "certificate",
  );
  if (!cargo && scenario === "changed") return "Relevant source updated";
  if (!cargo && scenario === "warm") return "Same task, again";
  return scenarioLabels[scenario];
}

function mockOutput(id: string, task: Task): unknown {
  if (id === "extract_invoice") return task.evidence[0].data;
  if (id === "extract_manifest") return task.evidence[1].data;
  if (id === "extract_certificate") return task.evidence[2].data;
  if (id === "review_summary")
    return {
      summary:
        "Shipment MIA-0248 has matching invoice and manifest quantities. Certificate is attached for review.",
      synthetic: true,
    };
  return { disposition: "ready_for_review", synthetic: true };
}

export function mockRecording(
  mode: "baseline" | "governed",
  scenario: Scenario,
): Recording {
  const task = structuredClone(cargoTask);
  if (scenario === "changed") {
    task.evidence[2].version = "2";
    task.evidence[2].data.coverage_minor = 6500000;
  }
  const runId = `mock-${mode}-${scenario}`;
  const base = Date.parse(observed);
  let offset = 0;
  const events: RunEvent[] = [];
  const emit = (
    event_type: string,
    step_id: string | null,
    payload: Record<string, unknown> = {},
    increment = 0,
  ) => {
    offset += increment;
    events.push({
      schema_version: "1",
      run_id: runId,
      sequence: events.length,
      timestamp: new Date(base + offset).toISOString(),
      event_type,
      step_id,
      payload,
    });
  };
  const changed = new Set([
    "extract_certificate",
    "coverage",
    "review_summary",
    "prepare_packet",
  ]);
  emit("runtime_started", null, { mode, synthetic: true });
  if (scenario === "changed" && mode === "governed")
    emit("evidence_invalidated", null, { step_ids: [...changed] });
  let modelCalls = 0;
  let toolCalls = 0;
  const results: StepResult[] = [];
  for (const item of task.steps) {
    const badDependency = results.some(
      (result) =>
        item.depends_on.includes(result.step_id) &&
        !["completed", "reused"].includes(result.status),
    );
    const reused =
      mode === "governed" &&
      (scenario === "warm" ||
        scenario === "unrelated" ||
        (scenario === "changed" && !changed.has(item.step_id)));
    let status: StepResult["status"] = reused ? "reused" : "completed";
    let reason: string | null = null;
    if (badDependency) {
      status = "blocked";
      reason = "A required dependency did not complete.";
    } else if (
      mode === "governed" &&
      scenario === "approval" &&
      item.step_id === "extract_certificate"
    ) {
      status = "awaiting_approval";
      reason = "This source requires an authorized reviewer.";
    } else if (mode === "governed" && scenario === "refused") {
      status = "blocked";
      reason = "The configured step budget would be exceeded.";
    } else if (
      mode === "governed" &&
      scenario === "failed" &&
      item.step_id === "extract_certificate"
    ) {
      status = "failed";
      reason = "Mock document handler failed. No provider request was made.";
    }
    const usage = unknownUsage();
    usage.tokens_known = true;
    if (status === "completed" || status === "failed") {
      usage.model_calls = Number(item.kind === "model");
      usage.tool_calls = Number(item.kind === "tool");
      modelCalls += usage.model_calls;
      toolCalls += usage.tool_calls;
      emit(
        "step_dispatched",
        item.step_id,
        { handler: item.handler, synthetic: true },
        100,
      );
      emit("budget_settled", item.step_id, { usage }, 330);
    }
    const result: StepResult = {
      step_id: item.step_id,
      status,
      output: ["completed", "reused"].includes(status)
        ? mockOutput(item.step_id, task)
        : null,
      reason,
      cache_key: reused ? `mock-cache-${item.step_id}` : null,
      source_run_id: reused ? "mock-governed-cold" : null,
      usage,
    };
    results.push(result);
    emit("canopy.step.recorded", item.step_id, { result }, reused ? 65 : 0);
    if (status === "awaiting_approval")
      emit("approval_required", item.step_id, {
        approval_id: "mock-approval",
        reason,
      });
    else if (status === "blocked")
      emit(
        scenario === "refused" ? "budget_refused" : "step_blocked",
        item.step_id,
        { reason },
      );
    else
      emit(
        status === "reused"
          ? "step_reused"
          : status === "failed"
            ? "step_failed"
            : "step_completed",
        item.step_id,
        { reason, source_run_id: result.source_run_id },
      );
  }
  const status: RunResult["status"] = results.some((r) => r.status === "failed")
    ? "failed"
    : results.some((r) => r.status === "awaiting_approval")
      ? "awaiting_approval"
      : results.some((r) => r.status === "blocked")
        ? "blocked"
        : "completed";
  const result: RunResult = {
    schema_version: "1",
    run_id: runId,
    task_id: task.task_id,
    tenant_id: task.tenant_id,
    mode,
    status,
    started_at: observed,
    finished_at: new Date(base + offset).toISOString(),
    output: Object.fromEntries(
      results
        .filter((r) => ["completed", "reused"].includes(r.status))
        .map((r) => [r.step_id, r.output]),
    ),
    steps: results,
    usage: {
      ...unknownUsage(),
      tokens_known: true,
      model_calls: modelCalls,
      tool_calls: toolCalls,
    },
    policy_version: "mock-policy-v1",
    evidence_versions: Object.fromEntries(
      task.evidence.map((e) => [e.evidence_id, e.version]),
    ),
    reused_steps: results.filter((r) => r.status === "reused").length,
    invalidated_steps:
      scenario === "changed" && mode === "governed" ? changed.size : 0,
    replay_source_run_id: null,
    synthetic: true,
    reason:
      status === "completed"
        ? null
        : "Illustrative frontend mock; see step details.",
  };
  emit("canopy.run.finished", null, { result });
  return { task, result, events };
}

export const mockBundle: ComparisonBundle = {
  format: "canopy-comparison/v1",
  description:
    "Frontend-only cargo mocks. Animation timing is illustrative; no performance, cost, or quality benchmark.",
  recordings: {
    baseline: mockRecording("baseline", "warm"),
    cold: mockRecording("governed", "cold"),
    warm: mockRecording("governed", "warm"),
    changed: mockRecording("governed", "changed"),
    unrelated: mockRecording("governed", "unrelated"),
  },
};
