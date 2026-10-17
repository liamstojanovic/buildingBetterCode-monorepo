import type { Usage } from "../api/types";
import type { RunState } from "../state/run";

export interface TokenPrice {
  id: string;
  name: string;
  inputPerMillion: number;
  outputPerMillion: number;
  source: string;
  note?: string;
  rateChanges?: readonly {
    effectiveFrom: string;
    inputPerMillion: number;
    outputPerMillion: number;
  }[];
}

export interface VerifiedTokenPrice extends TokenPrice {
  verifiedAt: string;
  flagship: boolean;
  evidence?: "user-screenshot" | "user-provided";
}
export interface ProviderPrices {
  id: "openai" | "anthropic" | "gemini";
  name: string;
  source: string;
  models: readonly VerifiedTokenPrice[];
}

// User-supplied official-pricing screenshots and Gemini text rates, dated below.
// Standard paid, uncached text rates price the mock workload. Other context
// tiers are preserved in notes. Scheduled changes take effect at 00:00 UTC.
const suppliedPricing = {
  verifiedAt: "2026-10-17",
  evidence: "user-screenshot",
} as const;
const openaiSource = "https://developers.openai.com/api/docs/pricing";
const anthropicSource =
  "https://platform.claude.com/docs/en/about-claude/pricing";
const geminiSource = "https://ai.google.dev/gemini-api/docs/pricing";
const suppliedGemini = {
  verifiedAt: "2026-10-17",
  evidence: "user-provided",
} as const;
export const providerPrices: readonly ProviderPrices[] = [
  {
    id: "openai",
    name: "OpenAI",
    source: openaiSource,
    models: [
      {
        ...suppliedPricing,
        id: "gpt-6-astra",
        name: "GPT-6 Astra",
        inputPerMillion: 10,
        outputPerMillion: 50,
        source: openaiSource,
        flagship: true,
        note: "Standard short context. Long context: $20 input / $75 output per 1M tokens.",
      },
      {
        ...suppliedPricing,
        id: "gpt-6.1-sol",
        name: "GPT-6.1 Sol",
        inputPerMillion: 2,
        outputPerMillion: 10,
        source: openaiSource,
        flagship: false,
        note: "Standard short context. Long context: $4 input / $15 output per 1M tokens.",
      },
      {
        ...suppliedPricing,
        id: "gpt-6-luna",
        name: "GPT-6 Luna",
        inputPerMillion: 0.1,
        outputPerMillion: 0.5,
        source: openaiSource,
        flagship: false,
        note: "Standard short context. Long context: $0.20 input / $0.75 output per 1M tokens.",
      },
    ],
  },
  {
    id: "anthropic",
    name: "Anthropic",
    source: anthropicSource,
    models: [
      {
        ...suppliedPricing,
        id: "claude-fable-5.1",
        name: "Claude Fable 5.1",
        inputPerMillion: 10,
        outputPerMillion: 50,
        source: anthropicSource,
        flagship: true,
      },
      {
        ...suppliedPricing,
        id: "claude-opus-5.5",
        name: "Claude Opus 5.5",
        inputPerMillion: 4,
        outputPerMillion: 20,
        source: anthropicSource,
        flagship: false,
      },
      {
        ...suppliedPricing,
        id: "claude-sonnet-5.5",
        name: "Claude Sonnet 5.5",
        inputPerMillion: 2,
        outputPerMillion: 10,
        source: anthropicSource,
        flagship: false,
      },
      {
        ...suppliedPricing,
        id: "claude-haiku-5.5",
        name: "Claude Haiku 5.5",
        inputPerMillion: 0.1,
        outputPerMillion: 0.5,
        source: anthropicSource,
        flagship: false,
        note: "Prompts up to 100,000 tokens. Above 100,000: $0.50 input / $2.50 output per 1M tokens. This simulation uses the lower tier.",
      },
    ],
  },
  {
    id: "gemini",
    name: "Gemini",
    source: geminiSource,
    models: [
      {
        ...suppliedGemini,
        id: "gemini-4-argon",
        name: "Gemini 4 Argon",
        inputPerMillion: 2,
        outputPerMillion: 10,
        source: geminiSource,
        flagship: true,
        rateChanges: [
          {
            effectiveFrom: "2026-12-01",
            inputPerMillion: 4,
            outputPerMillion: 20,
          },
        ],
        note: "$2 input / $10 output per 1M through November 30, 2026; $4 / $20 from December 1, 2026. Output includes thinking tokens.",
      },
      {
        ...suppliedGemini,
        id: "gemini-3.8-flash",
        name: "Gemini 3.8 Flash",
        inputPerMillion: 0.75,
        outputPerMillion: 3.75,
        source: geminiSource,
        flagship: false,
        rateChanges: [
          {
            effectiveFrom: "2027-01-01",
            inputPerMillion: 1.5,
            outputPerMillion: 7.5,
          },
        ],
        note: "$0.75 input / $3.75 output per 1M through December 31, 2026; $1.50 / $7.50 from January 1, 2027. Output includes thinking tokens.",
      },
      {
        ...suppliedGemini,
        id: "gemini-3.6-flash",
        name: "Gemini 3.6 Flash",
        inputPerMillion: 0.75,
        outputPerMillion: 3.75,
        source: geminiSource,
        flagship: false,
        rateChanges: [
          {
            effectiveFrom: "2027-01-01",
            inputPerMillion: 1.5,
            outputPerMillion: 7.5,
          },
        ],
        note: "$0.75 input / $3.75 output per 1M through December 31, 2026; $1.50 / $7.50 from January 1, 2027. Output includes thinking tokens.",
      },
      {
        ...suppliedGemini,
        id: "gemini-3.8-live",
        name: "Gemini 3.8 Live",
        inputPerMillion: 0.75,
        outputPerMillion: 4.5,
        source: geminiSource,
        flagship: false,
        note: "Paid text input and text output only; output includes thinking tokens.",
      },
    ],
  },
];

export function resolveTokenPrice<T extends TokenPrice>(
  price: T,
  asOf = new Date(),
): T {
  const day = asOf.toISOString().slice(0, 10);
  let active = price;
  for (const change of price.rateChanges ?? []) {
    if (day >= change.effectiveFrom)
      active = {
        ...active,
        inputPerMillion: change.inputPerMillion,
        outputPerMillion: change.outputPerMillion,
      };
  }
  return active;
}

export function estimateTokenCost(
  usage: Usage,
  price: TokenPrice,
  asOf = new Date(),
) {
  if (!usage.tokens_known) return null;
  const active = resolveTokenPrice(price, asOf);
  const input = (usage.input_tokens * active.inputPerMillion) / 1_000_000;
  const output = (usage.output_tokens * active.outputPerMillion) / 1_000_000;
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
