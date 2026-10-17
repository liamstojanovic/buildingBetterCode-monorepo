import demo from "../../public/recordings/advisor-demo.json";
import { parseAdvisorReport } from "../api/advisor";

export const advisorDemo = parseAdvisorReport(demo);

// Explicit frontend challenge fixtures derived from the actual demo. These
// variants exercise rendering only and are not additional analysis results.
export function advisorChallenge(variant: "partial" | "unknown" | "mixed") {
  const report = structuredClone(advisorDemo);
  report.limitations = [
    ...(report.limitations ?? []),
    "Frontend-only challenge fixture derived from the synthetic advisor demo; changed fields are illustrative.",
  ];
  if (variant === "partial") {
    report.analysis.limit_reasons = ["max_runs", "max_samples_per_group"];
    report.analysis.skipped_group_observations = 3;
    report.recommendations.forEach((item) => {
      item.sample_limit_reached = true;
    });
  }
  if (variant === "unknown") {
    report.recommendations.forEach((item) => {
      item.observed.input_tokens = null;
      item.observed.output_tokens = null;
      item.observed.elapsed_ms = null;
      item.observed.unknown_token_observations = item.observed.observations;
      item.observed.unknown_elapsed_observations = item.observed.observations;
    });
  }
  if (variant === "mixed") {
    const item = report.recommendations[0];
    item.observed.synthetic_observations = 1;
    item.samples.forEach((sample, index) => {
      sample.synthetic = index === 0;
    });
    item.finding.partial_substitution = true;
    item.finding.required_changes = [
      ...(item.finding.required_changes ?? []),
      "Retain the separate explanation generator and account for its cost.",
    ];
  }
  return report;
}
