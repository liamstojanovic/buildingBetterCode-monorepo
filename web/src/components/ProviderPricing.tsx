import { useEffect, useRef, useState } from "react";
import { ChevronDown, ArrowUpRight } from "lucide-react";
import { currentUsage, type RunState } from "../state/run";
import {
  estimateTokenCost,
  formatRate,
  formatTokenDollars,
  providerPrices,
  resolveTokenPrice,
  type ProviderPrices,
  type VerifiedTokenPrice,
} from "../fixtures/pricing";
import "./comparison-enhancements.css";

function CostRow({
  model,
  baseline,
  canopy,
  asOf,
}: {
  model: VerifiedTokenPrice;
  baseline: RunState;
  canopy: RunState;
  asOf: Date;
}) {
  const activeRate = resolveTokenPrice(model, asOf);
  const left =
    estimateTokenCost(currentUsage(baseline), model, asOf)?.total ?? null;
  const right =
    estimateTokenCost(currentUsage(canopy), model, asOf)?.total ?? null;
  const previous = useRef({ left, right });
  const [risingFaster, setRisingFaster] = useState(false);
  useEffect(() => {
    const before = previous.current;
    previous.current = { left, right };
    const faster =
      baseline.status === "running" &&
      left !== null &&
      right !== null &&
      before.left !== null &&
      before.right !== null &&
      left > before.left &&
      left - before.left > Math.max(0, right - before.right);
    setRisingFaster(faster);
    if (!faster) return;
    const timer = window.setTimeout(() => setRisingFaster(false), 900);
    return () => window.clearTimeout(timer);
  }, [left, right, baseline.status]);
  const finished =
    baseline.status === "completed" && canopy.status === "completed";
  const higher = finished && left !== null && right !== null && left > right;
  return (
    <div className="provider-model" aria-label={`${model.name} comparison`}>
      <div className="provider-model-heading">
        <a href={model.source} target="_blank" rel="noreferrer">
          {model.name}
        </a>
        <span>Text · USD / 1M tokens</span>
      </div>
      <p className="provider-rates">
        Input {formatRate(activeRate.inputPerMillion)} · Output{" "}
        {formatRate(activeRate.outputPerMillion)}
      </p>
      <div className="provider-cost-pair">
        <div className={risingFaster || higher ? "baseline-higher" : ""}>
          <span>Baseline</span>
          <strong key={left} className="cost-tick">
            {formatTokenDollars(left)}
          </strong>
          <small>
            {risingFaster ? (
              <>
                <ArrowUpRight size={12} /> Accumulating faster
              </>
            ) : higher ? (
              "Higher LLM spend"
            ) : (
              "LLM estimate"
            )}
          </small>
        </div>
        <div className="canopy-cost">
          <span>With Canopy</span>
          <strong key={right} className="cost-tick">
            {formatTokenDollars(right)}
          </strong>
          <small>LLM estimate</small>
        </div>
      </div>
      {model.note && <p className="provider-note">{model.note}</p>}
      <small className="provider-verification">
        {model.evidence === "user-screenshot"
          ? "Pricing screenshot supplied"
          : model.evidence === "user-provided"
            ? "Pricing supplied"
            : "Rates checked"}{" "}
        {model.verifiedAt}
      </small>
    </div>
  );
}

export function ProviderPricing({
  baseline,
  canopy,
  providers = providerPrices,
  asOf,
}: {
  baseline: RunState;
  canopy: RunState;
  providers?: readonly ProviderPrices[];
  asOf?: Date;
}) {
  const [pricingDate, setPricingDate] = useState(() => new Date());
  // Keep both lanes on the same rate schedule throughout a simulation.
  useEffect(() => {
    if (baseline.status === "running") setPricingDate(new Date());
  }, [baseline.status]);
  const effectiveDate = asOf ?? pricingDate;
  return (
    <section
      className="provider-pricing"
      aria-labelledby="provider-pricing-title"
      data-demo-focus="pricing"
    >
      <div className="benefits-heading">
        <div>
          <span className="eyebrow">ILLUSTRATIVE · TEXT TOKENS</span>
          <h3 id="provider-pricing-title">The same work. A different bill.</h3>
        </div>
        <span className="pricing-status">
          {baseline.status === "running" || canopy.status === "running"
            ? "Updating with the simulation"
            : "Simulation estimate"}
        </span>
      </div>
      <p className="comparison-context">
        Compare the same simulated token workload at each model’s standard,
        uncached short-context text rates. Estimates update as work settles;
        model quality and speed are not compared.
      </p>
      <div className="provider-grid">
        {providers.map((provider) => {
          const model =
            provider.models.find((item) => item.flagship) ?? provider.models[0];
          return (
            <article
              className="provider-card"
              key={provider.id}
              aria-label={`${provider.name} pricing`}
            >
              <h4>{provider.name}</h4>
              {model ? (
                <CostRow
                  key={model.id}
                  model={model}
                  baseline={baseline}
                  canopy={canopy}
                  asOf={effectiveDate}
                />
              ) : (
                <div className="pricing-unavailable">
                  <strong>Verified rates unavailable</strong>
                  <p>Unverified models and prices are omitted.</p>
                </div>
              )}
              <details className="provider-expansion">
                <summary>
                  All models
                  {provider.models.length > 0 && ` (${provider.models.length})`}
                  <ChevronDown size={15} aria-hidden="true" />
                </summary>
                <div className="provider-model-list">
                  {provider.models.length ? (
                    provider.models.map((item) => (
                      <CostRow
                        key={item.id}
                        model={item}
                        baseline={baseline}
                        canopy={canopy}
                        asOf={effectiveDate}
                      />
                    ))
                  ) : (
                    <p>No verified text pricing is available yet.</p>
                  )}
                  <a
                    className="provider-source"
                    href={provider.source}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Official {provider.name} pricing <ArrowUpRight size={12} />
                  </a>
                </div>
              </details>
            </article>
          );
        })}
      </div>
      <p className="comparison-context">
        LLM estimates exclude unverified JeV charges, tool fees, cache storage
        and infrastructure. Total Canopy cost and net dollar savings remain
        unmeasured. Rates are fixed at simulation start; scheduled changes use
        UTC dates.
      </p>
    </section>
  );
}
