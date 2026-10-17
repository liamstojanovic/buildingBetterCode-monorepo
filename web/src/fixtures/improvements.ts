const hosted =
  "https://github.com/liamstojanovic/buildingBetterCode-monorepo/tree/990f917/core/examples/recordings/advisor-live-2026-10-17";
export const improvementSources = {
  hosted: { label: "Retained hosted comparisons", href: hosted },
  comms: {
    label: "Communication replay evidence",
    href: "/benchmarks/raw/cpu/comms/report.json",
  },
  jev: {
    label: "JEV sample evaluation",
    href: "/benchmarks/raw/jev/comparison.json",
  },
  advisor: {
    label: "Offline discovery evidence",
    href: "/benchmarks/raw/cpu/summary.json",
  },
  governance: {
    label: "Synthetic runtime evidence",
    href: "/benchmarks/raw/cpu/synthetic.json",
  },
  blocking: {
    label: "Policy and drift smoke evidence",
    href: "/benchmarks/raw/cpu/components.json",
  },
} as const;
export interface Improvement {
  id: string;
  label: string;
  measured: string;
  benefit: string;
  tone: "saving" | "efficiency" | "guardrail";
  source: keyof typeof improvementSources;
  additionalSource?: keyof typeof improvementSources;
  context: string;
}
// Presentation summary of retained evidence, not results of the cargo simulation.
export const improvements: readonly Improvement[] = [
  {
    id: "calls",
    label: "AI calls for repeated work",
    measured: "21 → 0 calls",
    benefit: "100% fewer",
    tone: "saving",
    source: "hosted",
    context:
      "Nine retained baseline-repeat versus governed-warm comparisons on invented sample inputs. Reuses existing outputs; does not assess replacement quality.",
  },
  {
    id: "tokens",
    label: "New tokens for repeated work",
    measured: "8,970 → 0 tokens",
    benefit: "100% fewer",
    tone: "saving",
    source: "hosted",
    context:
      "New hosted input/output tokens across the same nine comparisons. Historical tokens and local checking work are separate.",
  },
  {
    id: "time",
    label: "Time spent on repeated work",
    measured: "21.33 → 1.10 seconds",
    benefit: "94.8% less time",
    tone: "saving",
    source: "hosted",
    context:
      "Sum of the nine retained baseline-repeat and warm durations, rounded. A sample replay comparison, not a general speedup guarantee.",
  },
  {
    id: "bytes",
    label: "Data sent between agents",
    measured: "771 → 356 KB",
    benefit: "53.8% smaller",
    tone: "efficiency",
    source: "comms",
    context:
      "771,411 → 356,491 payload bytes including shared context. Local replay of 618 messages; excludes transport headers. Not token or dollar savings.",
  },
  {
    id: "writes",
    label: "Database writes",
    measured: "618 → 36 writes",
    benefit: "94.2% fewer",
    tone: "efficiency",
    source: "comms",
    context:
      "SQLite commit transactions with batching. Message rows are still delivered; this is not a reduction in stored messages.",
  },
  {
    id: "delivery",
    label: "Local message delivery time",
    measured: "1.22 → 0.22 seconds",
    benefit: "82% less time",
    tone: "efficiency",
    source: "comms",
    context:
      "One local sample per path, including validation and commits; excludes frame construction, acknowledgements and network. CBOR serialization itself was slower than JSON.",
  },
  {
    id: "duplicates",
    label: "Duplicate messages",
    measured: "36 attempted; 0 delivered again",
    benefit: "36 duplicates blocked",
    tone: "guardrail",
    source: "comms",
    context: "Duplicate batch redelivery suppressed by the persistent mailbox.",
  },
  {
    id: "business",
    label: "JEV business decisions",
    measured: "2/2 correct; both deferred by the gate",
    benefit: "2 uncertain decisions held back",
    tone: "guardrail",
    source: "jev",
    context:
      "One authored business case, two passes. Correct raw choices failed the fixed margin gate; the explanation remains required.",
  },
  {
    id: "tools",
    label: "JEV tool selection",
    measured: "6/6 correct and accepted",
    benefit: "100% on 3 sample cases",
    tone: "efficiency",
    source: "jev",
    context:
      "Three authored cases, two passes each. Selection only; arguments were prebound and no tool executed.",
  },
  {
    id: "next",
    label: "JEV next-step selection",
    measured: "6/6 correct and accepted",
    benefit: "100% on 3 sample cases",
    tone: "efficiency",
    source: "jev",
    context:
      "Three authored cases, two passes each. Deterministic rules also answer these cases; no transition executed.",
  },
  {
    id: "discovery",
    label: "Optimization discovery",
    measured: "21 candidate groups from 9 reports",
    benefit: "0 additional AI calls",
    tone: "efficiency",
    source: "advisor",
    context:
      "Offline analysis over retained hosted records. Candidate groups are recommendations, not validated replacements or activated routes.",
  },
  {
    id: "source",
    label: "Work after a source changes",
    measured: "2 steps rerun; 1 reused",
    benefit: "1 unnecessary rerun avoided",
    tone: "saving",
    source: "governance",
    context:
      "Three-step synthetic core workload, separate from the seven-step illustrative cargo tutorial.",
  },
  {
    id: "blocking",
    label: "Budget and drift blocking",
    measured: "0 model/tool calls after blocking",
    benefit: "Work stopped before execution",
    tone: "guardrail",
    source: "blocking",
    additionalSource: "governance",
    context:
      "Synthetic budget-refusal and drift-containment probes. Neither is a production policy certification.",
  },
  {
    id: "replay",
    label: "Historical replay",
    measured: "0 new model/tool calls",
    benefit: "Inspect results without rerunning AI",
    tone: "saving",
    source: "governance",
    context:
      "Historical inspection of stored results; distinct from current validity-checked reuse.",
  },
];
