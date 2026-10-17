import type { ComparisonBundle } from "../api/types";

function median(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  const center = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[center]
    : (sorted[center - 1] + sorted[center]) / 2;
}

export function projectImpact(
  bundle: ComparisonBundle | null,
  volume: number,
  repeatShare: number,
): number | null {
  if (!bundle?.benchmark || bundle.benchmark.repetitions < 5) return null;
  const differences: number[] = [];
  for (const name of ["cold", "warm"]) {
    const trials = bundle.benchmark.cases[name]?.trials;
    if (!trials || trials.length < 5) return null;
    if (
      trials.some(
        (trial) =>
          !trial.completed ||
          trial.synthetic ||
          trial.baseline_cost_usd === null ||
          trial.governed_cost_usd === null ||
          !Number.isFinite(trial.baseline_cost_usd) ||
          !Number.isFinite(trial.governed_cost_usd) ||
          trial.baseline_cost_usd < 0 ||
          trial.governed_cost_usd < 0 ||
          ![trial.baseline_run_id, trial.governed_run_id].every((id) =>
            bundle.evaluations?.some(
              (evaluation) => evaluation.run_id === id && evaluation.passed,
            ),
          ),
      )
    )
      return null;
    differences.push(
      median(
        trials.map(
          (trial) => trial.baseline_cost_usd! - trial.governed_cost_usd!,
        ),
      ),
    );
  }
  if (!Number.isFinite(volume) || !Number.isFinite(repeatShare)) return null;
  const share = Math.max(0, Math.min(100, repeatShare)) / 100;
  return (
    Math.max(0, volume) *
    ((1 - share) * differences[0] + share * differences[1])
  );
}
