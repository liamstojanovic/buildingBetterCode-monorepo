/** A read-only projection of hash-verified retained benchmark evidence. */
export type BenchmarkCategory = "business_decision" | "tool_selection" | "next_step";
export interface BenchmarkLatency { count: number; minMs: number; medianMs: number; maxMs: number }
export interface BenchmarkTrial {
  trial: number; cold: boolean; elapsedMs: number;
  rawChoice: string; acceptedChoice: string | null;
  disposition: "defer" | "accept_selection"; reasons: string[];
  scores: Record<string, number>; selectedEntailment: number; margin: number;
  minimumEntailment: number; minimumMargin: number; thresholdBasis: string;
  ruleBaseline: string | null; remainingFields: string[];
  preboundArguments: Record<string, string> | null;
  actionExecuted: false; rawMatch: boolean; acceptedMatch: boolean;
}
export interface BenchmarkCase {
  id: string; category: BenchmarkCategory; stepId: string; field: string;
  expectedChoice: string; expectationBasis: string; premise: string;
  choices: { name: string; hypothesis: string }[];
  requiresExplanation: boolean; trials: BenchmarkTrial[];
}
export interface BenchmarkEvidence {
  schemaVersion: "canopy.benchmark_evidence/v1";
  publishedAt: string; auditedAt: string; sourceCommit: string; sourceUrl: string;
  dates: { cpu: string; jev: string; comms: string };
  revisions: { cpu: string; gpu: string }; sourceManifestSha256: string;
  advisor: { reports: number; groups: number; modelCalls: number };
  jev: {
    model: string; distinctCases: number; trials: number; rawMatches: number;
    acceptedMatches: number; firstTrialRawMatches: number; firstTrialAcceptedMatches: number;
    coldMs: number; modelLoadMs: number; warm: BenchmarkLatency;
    hosted: {
      calls: number; excludedReused: number; normalizedMatches: number;
      strictMatches: number; latency: BenchmarkLatency;
      wrongChoices: { provider: string; model: string; caseId: string; runLabel: string; choice: string | null; expected: string }[];
    };
    cases: BenchmarkCase[]; limitations: string[];
  };
  comms: {
    messages: number; batches: number; jsonBytes: number; singleCborBytes: number;
    batchCborBytes: number; base64Bytes: number; bytesSaved: number; reductionFraction: number;
    roundtripVerified: boolean; networkCalls: number; timingRepetitions: number;
    timings: { encoding: "json" | "single_cbor" | "cbor" | "auto"; encodeMs: number; decodeMs: number }[];
    mailbox: { delivered: number; acknowledged: number; duplicatesSuppressed: number;
      individualMs: number; batchMs: number; individualTransactions: number;
      batchTransactions: number; scope: string };
    denseFallback: { jsonBytes: number; cborBytes: number; selectedBytes: number; selectedEncoding: "json"; detail: string };
    scope: string; limitations: string[];
  };
  gpu: { name: string; totalMiB: number; allocatorLimitMiB: number; peakAllocatedMiB: number;
    peakReservedMiB: number; teammate12GbTested: false; standardCurrentDockerTested: false;
    compatibilityOverride: string; scope: string };
  spend: { providerUsd: number; budgetUsd: number; remainingUsd: number; historicalHostedUsd: null;
    energySavingsJ: null; costSavingsUsd: null; scope: string };
  downloads: { label: string; url: string; sha256: string }[];
}

type Check = (value: unknown, path: string) => void;
function invalid(path: string, reason: string): never {
  throw new Error(`Invalid benchmark evidence at ${path}: ${reason}.`);
}
const text: Check = (v, p) => {
  if (typeof v !== "string" || !v.trim()) invalid(p, "expected a nonempty string");
};
const number: Check = (v, p) => {
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0)
    invalid(p, "expected a finite nonnegative number");
};
const count: Check = (v, p) => {
  number(v, p);
  if (!Number.isSafeInteger(v)) invalid(p, "expected a safe integer count");
};
const probability: Check = (v, p) => {
  number(v, p);
  if ((v as number) > 1) invalid(p, "expected a value from 0 to 1");
};
const boolean: Check = (v, p) => {
  if (typeof v !== "boolean") invalid(p, "expected a boolean");
};
const literal = (...allowed: unknown[]): Check => (v, p) => {
  if (!allowed.includes(v)) invalid(p, `expected ${allowed.map(String).join(" or ")}`);
};
const nullable = (check: Check): Check => (v, p) => { if (v !== null) check(v, p); };
const array = (check: Check, minimum = 0): Check => (v, p) => {
  if (!Array.isArray(v) || v.length < minimum) invalid(p, `expected an array with at least ${minimum} items`);
  v.forEach((item, i) => check(item, `${p}[${i}]`));
};
const object = (fields: Record<string, Check>): Check => (v, p) => {
  if (!v || typeof v !== "object" || Array.isArray(v)) invalid(p, "expected an object");
  const data = v as Record<string, unknown>;
  for (const key of Object.keys(data)) if (!Object.hasOwn(fields, key)) invalid(`${p}.${key}`, "unsupported field");
  for (const [key, check] of Object.entries(fields)) {
    if (!Object.hasOwn(data, key)) invalid(`${p}.${key}`, "required field is missing");
    check(data[key], `${p}.${key}`);
  }
};
const record = (check: Check): Check => (v, p) => {
  if (!v || typeof v !== "object" || Array.isArray(v)) invalid(p, "expected an object");
  for (const [key, item] of Object.entries(v)) {
    if (["__proto__", "constructor", "prototype"].includes(key)) invalid(p, "unsupported record key");
    check(item, `${p}.${key}`);
  }
};
const pattern = (regex: RegExp, description: string): Check => (v, p) => {
  text(v, p);
  if (!regex.test(v as string)) invalid(p, description);
};
const sha = pattern(/^[a-f0-9]{64}$/, "expected a SHA256 digest");
const revision = pattern(/^[a-f0-9]{40}$/, "expected a full Git revision");
const date: Check = (v, p) => {
  text(v, p);
  if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d\d:\d\d)$/.test(v as string) || !Number.isFinite(Date.parse(v as string)))
    invalid(p, "expected a timestamp with a timezone");
};
const strings = array(text);
const latency = object({ count, minMs: number, medianMs: number, maxMs: number });
const trial = object({
  trial: count, cold: boolean, elapsedMs: number, rawChoice: text, acceptedChoice: nullable(text),
  disposition: literal("defer", "accept_selection"), reasons: strings, scores: record(probability),
  selectedEntailment: probability, margin: probability, minimumEntailment: probability, minimumMargin: probability,
  thresholdBasis: text, ruleBaseline: nullable(text), remainingFields: strings,
  preboundArguments: nullable(record(text)), actionExecuted: literal(false), rawMatch: boolean, acceptedMatch: boolean,
});
const evidenceSchema = object({
  schemaVersion: literal("canopy.benchmark_evidence/v1"), publishedAt: date, auditedAt: date,
  sourceCommit: revision, sourceUrl: pattern(/^https:\/\/github\.com\/liamstojanovic\/buildingBetterCode-monorepo\/tree\/[a-f0-9]{40}\/core\/examples\/recordings\/container-benchmarks-\d{4}-\d{2}-\d{2}$/, "expected the trusted benchmark source URL"),
  dates: object({ cpu: date, jev: date, comms: date }), revisions: object({ cpu: revision, gpu: revision }),
  sourceManifestSha256: sha, advisor: object({ reports: count, groups: count, modelCalls: literal(0) }),
  jev: object({
    model: text, distinctCases: count, trials: count, rawMatches: count, acceptedMatches: count,
    firstTrialRawMatches: count, firstTrialAcceptedMatches: count,
    coldMs: number, modelLoadMs: number, warm: latency,
    hosted: object({ calls: count, excludedReused: count, normalizedMatches: count, strictMatches: count, latency,
      wrongChoices: array(object({ provider: text, model: text, caseId: text, runLabel: text, choice: nullable(text), expected: text })) }),
    cases: array(object({ id: text, category: literal("business_decision", "tool_selection", "next_step"),
      stepId: text, field: text, expectedChoice: text, expectationBasis: text, premise: text,
      choices: array(object({ name: text, hypothesis: text }), 2), requiresExplanation: boolean, trials: array(trial, 1) }), 3),
    limitations: array(text, 1),
  }),
  comms: object({
    messages: count, batches: count, jsonBytes: count, singleCborBytes: count, batchCborBytes: count,
    base64Bytes: count, bytesSaved: count, reductionFraction: probability, roundtripVerified: literal(true),
    networkCalls: literal(0), timingRepetitions: count,
    timings: array(object({ encoding: literal("json", "single_cbor", "cbor", "auto"), encodeMs: number, decodeMs: number }), 4),
    mailbox: object({ delivered: count, acknowledged: count, duplicatesSuppressed: count,
      individualMs: number, batchMs: number, individualTransactions: count, batchTransactions: count, scope: text }),
    denseFallback: object({ jsonBytes: count, cborBytes: count, selectedBytes: count, selectedEncoding: literal("json"), detail: text }),
    scope: text, limitations: array(text, 1),
  }),
  gpu: object({ name: text, totalMiB: number, allocatorLimitMiB: number, peakAllocatedMiB: number,
    peakReservedMiB: number, teammate12GbTested: literal(false), standardCurrentDockerTested: literal(false),
    compatibilityOverride: text, scope: text }),
  spend: object({ providerUsd: number, budgetUsd: number, remainingUsd: number, historicalHostedUsd: literal(null),
    energySavingsJ: literal(null), costSavingsUsd: literal(null), scope: text }),
  downloads: array(object({ label: text,
    url: pattern(/^\/benchmarks\/raw\/(?:[a-zA-Z0-9_-]+\/)*[a-zA-Z0-9_.-]+\.(?:json|jsonl)$/, "expected a same-origin benchmark JSON path"), sha256: sha }), 1),
});

function same(actual: number, expected: number, path: string) {
  if (Math.abs(actual - expected) > 1e-9) invalid(path, "does not agree with the underlying evidence counts or measurements");
}
function checkLatency(value: BenchmarkLatency, path: string) {
  if (value.count === 0 || value.minMs > value.medianMs || value.medianMs > value.maxMs)
    invalid(path, "expected a nonempty, ordered latency distribution");
}
/** Reject incomplete, unsupported, inconsistent, or unsafe evidence before rendering. */
export function parseBenchmarkEvidence(input: unknown): BenchmarkEvidence {
  evidenceSchema(input, "benchmark");
  const data = structuredClone(input) as BenchmarkEvidence;
  const { jev, comms, gpu, spend } = data;
  if (!data.sourceUrl.includes(`/tree/${data.sourceCommit}/`)) invalid("sourceUrl", "source revision differs");
  if (new Set(jev.cases.map(c => c.id)).size !== jev.cases.length) invalid("jev.cases", "duplicate case IDs");
  for (const category of ["business_decision", "tool_selection", "next_step"])
    if (!jev.cases.some(c => c.category === category)) invalid("jev.cases", `missing category ${category}`);
  same(jev.distinctCases, jev.cases.length, "jev.distinctCases");
  const trials = jev.cases.flatMap(c => c.trials);
  same(jev.trials, trials.length, "jev.trials");
  same(jev.rawMatches, trials.filter(t => t.rawMatch).length, "jev.rawMatches");
  same(jev.acceptedMatches, trials.filter(t => t.acceptedMatch).length, "jev.acceptedMatches");
  same(jev.firstTrialRawMatches, jev.cases.filter(c => c.trials[0].rawMatch).length, "jev.firstTrialRawMatches");
  same(jev.firstTrialAcceptedMatches, jev.cases.filter(c => c.trials[0].acceptedMatch).length, "jev.firstTrialAcceptedMatches");
  for (const c of jev.cases) {
    const names = c.choices.map(choice => choice.name);
    if (new Set(names).size !== names.length || !names.includes(c.expectedChoice)) invalid(c.id, "invalid choice inventory");
    c.trials.forEach((t, i) => {
      same(t.trial, i + 1, `${c.id}.trial`);
      if (Object.keys(t.scores).length !== names.length || names.some(name => !Object.hasOwn(t.scores, name)))
        invalid(c.id, "scores must cover every hypothesis exactly once");
      if (!names.includes(t.rawChoice) || (t.acceptedChoice !== null && t.acceptedChoice !== t.rawChoice))
        invalid(c.id, "invalid raw or accepted choice");
      if ((t.disposition === "defer") !== (t.acceptedChoice === null)) invalid(c.id, "abstention and accepted choice disagree");
      if (t.rawMatch !== (t.rawChoice === c.expectedChoice) || t.acceptedMatch !== (t.acceptedChoice === c.expectedChoice))
        invalid(c.id, "match flags disagree with expected choice");
      same(t.selectedEntailment, t.scores[t.rawChoice], `${c.id}.selectedEntailment`);
      const sorted = Object.values(t.scores).sort((a, b) => b - a);
      same(t.selectedEntailment, sorted[0], `${c.id}.rawChoice`);
      same(t.margin, sorted[0] - sorted[1], `${c.id}.margin`);
      if (t.disposition === "accept_selection" && (t.selectedEntailment < t.minimumEntailment || t.margin < t.minimumMargin))
        invalid(c.id, "accepted choice does not pass confidence gates");
      if (t.disposition === "defer" && !t.reasons.length) invalid(c.id, "abstention needs a reason");
      if (c.requiresExplanation && !t.remainingFields.includes("reason")) invalid(c.id, "required explanation was lost");
      if (c.category === "next_step" && (t.ruleBaseline === null || !names.includes(t.ruleBaseline)))
        invalid(c.id, "next-step rule baseline is missing");
    });
  }
  same(trials.filter(t => t.cold).length, 1, "jev.cold");
  same(jev.coldMs, trials.find(t => t.cold)!.elapsedMs, "jev.coldMs");
  same(jev.warm.count, trials.filter(t => !t.cold).length, "jev.warm.count");
  checkLatency(jev.warm, "jev.warm");
  const warmTimes = trials.filter(t => !t.cold).map(t => t.elapsedMs).sort((a, b) => a - b);
  const midpoint = Math.floor(warmTimes.length / 2);
  const median = warmTimes.length % 2 ? warmTimes[midpoint] : (warmTimes[midpoint - 1] + warmTimes[midpoint]) / 2;
  same(jev.warm.minMs, warmTimes[0], "jev.warm.minMs");
  same(jev.warm.medianMs, median, "jev.warm.medianMs");
  same(jev.warm.maxMs, warmTimes[warmTimes.length - 1], "jev.warm.maxMs");
  if (jev.modelLoadMs > jev.coldMs) invalid("jev.modelLoadMs", "exceeds cold end-to-end time");
  const hosted = jev.hosted;
  same(hosted.latency.count, hosted.calls, "jev.hosted.calls");
  checkLatency(hosted.latency, "jev.hosted.latency");
  same(hosted.calls - hosted.normalizedMatches, hosted.wrongChoices.length, "jev.hosted.normalizedMatches");
  if (hosted.strictMatches > hosted.normalizedMatches) invalid("jev.hosted.strictMatches", "exceeds normalized matches");
  for (const wrong of hosted.wrongChoices) {
    const c = jev.cases.find(item => item.id === wrong.caseId);
    if (!c || wrong.expected !== c.expectedChoice || wrong.choice === wrong.expected)
      invalid("jev.hosted.wrongChoices", "invalid mismatch evidence");
  }
  if (!comms.messages || !comms.batches || !comms.jsonBytes || !comms.timingRepetitions)
    invalid("comms", "message, batch, byte and timing denominators must be positive");
  same(comms.bytesSaved, comms.jsonBytes - comms.batchCborBytes, "comms.bytesSaved");
  same(comms.reductionFraction, comms.bytesSaved / comms.jsonBytes, "comms.reductionFraction");
  if (new Set(comms.timings.map(t => t.encoding)).size !== 4 || comms.timings.length !== 4)
    invalid("comms.timings", "expected all four distinct encodings");
  same(comms.mailbox.delivered, comms.messages, "comms.mailbox.delivered");
  same(comms.mailbox.acknowledged, comms.messages, "comms.mailbox.acknowledged");
  same(comms.mailbox.individualTransactions, comms.messages, "comms.mailbox.individualTransactions");
  same(comms.mailbox.batchTransactions, comms.batches, "comms.mailbox.batchTransactions");
  same(comms.mailbox.duplicatesSuppressed, comms.batches, "comms.mailbox.duplicatesSuppressed");
  same(comms.denseFallback.selectedBytes, comms.denseFallback.jsonBytes, "comms.denseFallback.selectedBytes");
  if (comms.denseFallback.jsonBytes >= comms.denseFallback.cborBytes) invalid("comms.denseFallback", "JSON fallback must save bytes");
  if (gpu.peakAllocatedMiB > gpu.peakReservedMiB || gpu.peakReservedMiB > gpu.allocatorLimitMiB || gpu.allocatorLimitMiB > gpu.totalMiB)
    invalid("gpu", "inconsistent allocator memory measurements");
  same(spend.providerUsd + spend.remainingUsd, spend.budgetUsd, "spend");
  return data;
}

export async function fetchBenchmarkEvidence(signal?: AbortSignal): Promise<BenchmarkEvidence> {
  const response = await fetch("/benchmarks/latest.json", { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`Benchmark evidence could not be loaded (HTTP ${response.status}).`);
  let input: unknown;
  try { input = await response.json(); }
  catch (error) {
    if (signal?.aborted) throw error;
    throw new Error("Benchmark evidence is not valid JSON. Rebuild or refresh the published frontend assets.");
  }
  return parseBenchmarkEvidence(input);
}
