import type { Usage } from "../api/types";
import type { RunState } from "../state/run";

export interface TokenPrice {
  id: string;
  name: string;
  inputPerMillion: number;
  outputPerMillion: number;
  source: string;
  note?: string;
}

// Standard paid text pricing, verified against the linked sources on this date.
// These price illustrative tokens only; they are never recorded runtime spend.
export const pricingAsOf = "2026-10-17";
export const swarmPrices = [
  {
    id: "gpt-6-astra",
    name: "OpenAI · GPT-6 Astra",
    inputPerMillion: 10,
    outputPerMillion: 50,
    source: "https://developers.openai.com/api/docs/pricing",
  },
  {
    id: "gemini-3.8-flash",
    name: "Google · Gemini 3.8 Flash",
    inputPerMillion: 0.75,
    outputPerMillion: 3.75,
    source: "https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash",
    note: "Promotional rates through December 31, 2026; then $1.50 input / $7.50 output per 1M.",
  },
  {
    id: "claude-sonnet-5.5",
    name: "Anthropic · Claude Sonnet 5.5",
    inputPerMillion: 2,
    outputPerMillion: 10,
    source: "https://platform.claude.com/docs/en/about-claude/pricing",
  },
] as const satisfies readonly TokenPrice[];
export const jevPrice: TokenPrice = {
  id: "typesafe/jev-1.13",
  name: "JeV 1.13",
  inputPerMillion: 0.042,
  outputPerMillion: 0,
  source: "https://openrouter.ai/typesafe/jev-1.13",
};

export function estimateTokenCost(usage: Usage, price: TokenPrice) {
  if (!usage.tokens_known) return null;
  const input = (usage.input_tokens * price.inputPerMillion) / 1_000_000;
  const output = (usage.output_tokens * price.outputPerMillion) / 1_000_000;
  return { input, output, total: input + output };
}

export function formatTokenDollars(value: number | null) {
  if (value === null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
  }).format(value);
}

export function formatRate(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  }).format(value);
}

export function unitRateDelta(baselineRate: number, alternativeRate: number) {
  return (alternativeRate - baselineRate) / 1_000_000;
}

// Demo-only routing assumption: one typed JeV decision per observed model task.
export const modeledJevTokensPerDecision = { input: 256, output: 1 } as const;
export function modeledJevUsage(state: RunState) {
  const decisions = state.task.steps.filter(
    (step) =>
      step.kind === "model" &&
      ["running", "completed", "reused", "failed"].includes(
        state.steps[step.step_id].status,
      ),
  ).length;
  return {
    decisions,
    input_tokens: decisions * modeledJevTokensPerDecision.input,
    output_tokens: decisions * modeledJevTokensPerDecision.output,
  };
}

export function formatUnitDollars(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 9,
    maximumFractionDigits: 9,
  }).format(value);
}
