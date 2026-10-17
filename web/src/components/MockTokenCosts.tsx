import type { RunState } from "../state/run";
import { currentUsage } from "../state/run";
import {
  estimateTokenCost,
  formatRate,
  formatTokenDollars,
  formatUnitDollars,
  modeledJevUsage,
  modeledJevTokensPerDecision,
  jevPrice,
  pricingAsOf,
  swarmPrices,
  unitRateDelta,
  type TokenPrice,
} from "../fixtures/pricing";

function difference(value: number | null, unit = false) {
  return value === null
    ? "—"
    : `${value > 0 ? "+" : value < 0 ? "−" : ""}${(unit ? formatUnitDollars : formatTokenDollars)(Math.abs(value))}`;
}

export function MockTokenCosts({
  state,
  baseline,
  variant,
  price,
}: {
  state: RunState;
  baseline: RunState;
  variant: "baseline" | "canopy";
  price: TokenPrice;
}) {
  const usage = currentUsage(state);
  const llmCost = estimateTokenCost(usage, price);
  const routing = modeledJevUsage(state);
  const jevCost = estimateTokenCost(
    {
      ...usage,
      input_tokens: routing.input_tokens,
      output_tokens: routing.output_tokens,
    },
    jevPrice,
  );
  const cost =
    llmCost && (variant === "baseline" || jevCost)
      ? {
          input: llmCost.input + (variant === "canopy" ? jevCost!.input : 0),
          output: llmCost.output + (variant === "canopy" ? jevCost!.output : 0),
          total: llmCost.total + (variant === "canopy" ? jevCost!.total : 0),
        }
      : null;
  const baseUsage = currentUsage(baseline);
  const baseCost = estimateTokenCost(baseUsage, price);
  const compare =
    variant === "canopy" &&
    baseline.status === "completed" &&
    state.status === "completed";
  const rows = variant === "canopy" ? [...swarmPrices, jevPrice] : swarmPrices;
  return (
    <section className="mock-token-costs" aria-label={`${variant} token costs`}>
      <div className="token-cost-heading">
        <span>ESTIMATED TOKEN COST</span>
        <a href={price.source} target="_blank" rel="noreferrer">
          {price.name}
        </a>
      </div>
      <div className="token-cost-grid">
        {(
          [
            ["input", "Input"],
            ["output", "Output"],
            ["total", "Total"],
          ] as const
        ).map(([key, label]) => {
          const delta =
            compare && cost && baseCost ? cost[key] - baseCost[key] : null;
          return (
            <div key={key}>
              <span>{label}</span>
              <strong>{formatTokenDollars(cost?.[key] ?? null)}</strong>
              {variant === "canopy" && (
                <small
                  className={
                    delta !== null && delta < 0 ? "cost-reduction" : ""
                  }
                  aria-label={`${label} cost delta`}
                >
                  {delta === null ? "Δ pending" : `Δ ${difference(delta)}`}
                </small>
              )}
            </div>
          );
        })}
      </div>
      <details className="token-rate-details">
        <summary>
          Input {formatRate(price.inputPerMillion)} / output{" "}
          {formatRate(price.outputPerMillion)} per 1M tokens{" "}
          <span>Rates & per-token deltas</span>
        </summary>
        <div className="token-rate-scroll">
          <table>
            <caption>Published rates · USD · as of {pricingAsOf}</caption>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">Input / token</th>
                <th scope="col">Output / token</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((model) => (
                <tr
                  key={model.id}
                  className={model.id === price.id ? "selected-rate" : ""}
                >
                  <th scope="row">
                    <a href={model.source} target="_blank" rel="noreferrer">
                      {model.name}
                    </a>
                  </th>
                  <td>
                    {formatUnitDollars(model.inputPerMillion / 1_000_000)}
                  </td>
                  <td>
                    {formatUnitDollars(model.outputPerMillion / 1_000_000)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          Standard paid text rates, uncached short-context input. Run estimates
          use mock token counts; tool fees, cache storage and infrastructure are
          excluded.
        </p>
        {compare && cost && baseCost && (
          <p className="effective-token-delta">
            Effective Δ per original input token:{" "}
            {baseUsage.input_tokens
              ? difference(
                  (cost.input - baseCost.input) / baseUsage.input_tokens,
                  true,
                )
              : "—"}
            ; per original output token:{" "}
            {baseUsage.output_tokens
              ? difference(
                  (cost.output - baseCost.output) / baseUsage.output_tokens,
                  true,
                )
              : "—"}
            . Includes modeled JeV overhead.
          </p>
        )}
        {variant === "canopy" && (
          <div className="jev-rate-delta">
            <strong>JeV vs {price.name} · per-token rate delta</strong>
            <span>
              Input{" "}
              {difference(
                unitRateDelta(price.inputPerMillion, jevPrice.inputPerMillion),
                true,
              )}{" "}
              / token · output{" "}
              {difference(
                unitRateDelta(
                  price.outputPerMillion,
                  jevPrice.outputPerMillion,
                ),
                true,
              )}{" "}
              / token
            </span>
            <p>
              JeV models typed decisions; the selected LLM handles novel
              generation. The modeled JeV charge is included in this lane’s
              input, output and total cost.
            </p>
          </div>
        )}
      </details>
      {price.note && <p className="pricing-promotion">{price.note}</p>}
      {variant === "canopy" && (
        <div className="jev-price-reference">
          <a href={jevPrice.source} target="_blank" rel="noreferrer">
            JeV 1.13 · {routing.decisions} modeled decisions
          </a>
          <strong>{formatTokenDollars(jevCost?.total ?? null)} included</strong>
          <span>
            {routing.input_tokens.toLocaleString()} input +{" "}
            {routing.output_tokens.toLocaleString()} output tokens ·{" "}
            {formatRate(jevPrice.inputPerMillion)} / free per 1M
          </span>
          <small>
            Assumes {modeledJevTokensPerDecision.input} input +{" "}
            {modeledJevTokensPerDecision.output} output tokens per model-task
            decision.
            {" "}This pricing scenario is separate from the recorded local
            OpenJEV results in Benchmarks; their cost savings remain unknown.
          </small>
        </div>
      )}
      {variant === "canopy" && (
        <p className="cost-delta-note">
          Δ = Canopy − baseline, including modeled JeV overhead.
        </p>
      )}
    </section>
  );
}
