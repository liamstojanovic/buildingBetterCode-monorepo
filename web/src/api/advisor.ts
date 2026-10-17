import type { AdvisorReport } from "./generated/AdvisorReport";
import { validateAdvisor } from "./validation";

export type {
  AdvisorReport,
  AdvisorRecommendation,
} from "./generated/AdvisorReport";
export const MAX_ADVISOR_BYTES = 20 * 1024 * 1024;

export function parseAdvisorReport(input: unknown): AdvisorReport {
  if (!input || typeof input !== "object" || Array.isArray(input))
    throw new Error(
      "Choose an offline AdvisorReport JSON, not a task or recording bundle.",
    );
  const header = input as Record<string, unknown>;
  if (header.report_type !== "canopy.offline_advisor")
    throw new Error(
      "Expected report_type canopy.offline_advisor. Tasks, demo manifests, and comparison bundles use different formats.",
    );
  if (
    header.schema_version !== "1" ||
    header.analyzer_version !== "offline-advisor-v1"
  )
    throw new Error(
      "Unsupported advisor version. Expected schema 1 and analyzer offline-advisor-v1.",
    );
  const report = structuredClone(input);
  if (!validateAdvisor(report)) {
    const error = validateAdvisor.errors?.[0];
    throw new Error(
      `Invalid advisor report: ${error?.instancePath || "/"} ${error?.message || "does not match the schema"}.`,
    );
  }
  return report;
}

export async function readAdvisorFile(
  file: Pick<File, "size" | "text">,
): Promise<AdvisorReport> {
  if (file.size > MAX_ADVISOR_BYTES)
    throw new Error("Use an advisor report no larger than 20 MiB.");
  let data: unknown;
  try {
    data = JSON.parse(await file.text());
  } catch {
    throw new Error(
      "Could not read advisor JSON. Choose a valid UTF-8 JSON report.",
    );
  }
  return parseAdvisorReport(data);
}

export const categoryLabels = {
  business_decision: "Business decision",
  tool_selection: "Tool selection",
  next_step: "Next step",
  unknown: "Unknown category",
} as const;
export const statusLabels = {
  candidate: "Candidate",
  needs_mapping: "Needs mapping",
  unsupported: "Unsupported",
} as const;
export const strategyLabels = {
  evaluate_openjev: "Evaluate OpenJEV",
  deterministic_function: "Deterministic function",
  retain_llm: "Retain LLM",
  retain_backend: "Retain backend",
} as const;
export const compatibilityLabels = {
  native_request_shape: "Native request shape",
  requires_text_adapter: "Requires text adapter",
  requires_mapping: "Requires mapping",
  not_applicable: "Not applicable",
} as const;
