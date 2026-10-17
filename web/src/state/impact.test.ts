import { describe, expect, it } from "vitest";
import { projectImpact } from "./impact";
import { mockBundle } from "../fixtures/cargo";
import type { BenchmarkTrial, ComparisonBundle } from "../api/types";

function measured(): ComparisonBundle {
  const bundle = structuredClone(mockBundle);
  const trials = (name: string, governed: number): BenchmarkTrial[] =>
    Array.from({ length: 5 }, (_, index) => ({
      baseline_run_id: `${name}-base-${index}`,
      governed_run_id: `${name}-canopy-${index}`,
      baseline_cost_usd: 1,
      governed_cost_usd: governed,
      completed: true,
      synthetic: false,
    }));
  const cold = trials("cold", 1.2);
  const warm = trials("warm", 0.2);
  bundle.benchmark = {
    repetitions: 5,
    cases: {
      cold: { baseline: [], governed: [], trials: cold },
      warm: { baseline: [], governed: [], trials: warm },
    },
  };
  bundle.evaluations = [...cold, ...warm].flatMap((trial) =>
    [trial.baseline_run_id, trial.governed_run_id].map((run_id) => ({
      run_id,
      passed: true,
      checked_fields: 1,
      correct_fields: 1,
      method: "Test-only measurement fixture",
    })),
  );
  return bundle;
}
describe("evidence-gated enterprise projection", () => {
  it("includes cold overhead and warm savings using paired medians", () => {
    expect(projectImpact(measured(), 100000, 50)).toBeCloseTo(30000);
    expect(projectImpact(measured(), 100000, 0)).toBeCloseTo(-20000);
  });
  it("does not substitute the final run for all measured trials", () => {
    const bundle = measured();
    bundle.benchmark!.cases.warm.trials![4].governed_cost_usd = 100;
    expect(projectImpact(bundle, 100000, 50)).toBeCloseTo(30000);
  });
  it.each([
    "synthetic",
    "unknown-cost",
    "incomplete",
    "unscored",
    "few-trials",
  ] as const)("hides projection for %s evidence", (condition) => {
    const bundle = measured();
    const trial = bundle.benchmark!.cases.cold.trials![0];
    if (condition === "synthetic") trial.synthetic = true;
    if (condition === "unknown-cost") trial.baseline_cost_usd = null;
    if (condition === "incomplete") trial.completed = false;
    if (condition === "unscored") bundle.evaluations = [];
    if (condition === "few-trials") bundle.benchmark!.cases.warm.trials!.pop();
    expect(projectImpact(bundle, 100000, 50)).toBeNull();
  });
});
