import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../public/benchmarks/latest.json";
import { fetchBenchmarkEvidence, parseBenchmarkEvidence } from "./benchmarks";

afterEach(() => vi.unstubAllGlobals());

describe("retained benchmark evidence", () => {
  it("preserves the real trial denominators, abstentions, rules and unknown savings", () => {
    const data = parseBenchmarkEvidence(fixture);
    expect(data).not.toBe(fixture);
    expect(data.jev.distinctCases).toBe(7);
    expect(data.jev.trials).toBe(14);
    expect(data.jev.rawMatches).toBe(14);
    expect(data.jev.acceptedMatches).toBe(12);
    const business = data.jev.cases.find(c => c.category === "business_decision")!;
    expect(business.trials.map(t => t.acceptedChoice)).toEqual([null, null]);
    expect(business.trials[0].remainingFields).toEqual(["reason"]);
    expect(business.trials[0].margin).toBeLessThan(business.trials[0].minimumMargin);
    expect(data.jev.cases.filter(c => c.category === "next_step").every(c => c.trials.every(t => t.ruleBaseline === c.expectedChoice))).toBe(true);
    expect(data.spend.costSavingsUsd).toBeNull();
    expect(data.spend.energySavingsJ).toBeNull();
    expect(data.spend.historicalHostedUsd).toBeNull();
    expect(data.jev.hosted.wrongChoices[0]).toMatchObject({ provider: "openai", choice: "respond", expected: "retrieve" });
    expect(data.comms.messages).toBe(618);
    expect(data.comms.bytesSaved).toBe(414920);
    expect(data.gpu.teammate12GbTested).toBe(false);
  });

  it.each([null, [], {}, { ...fixture, schemaVersion: "canopy.benchmark_evidence/v2" }])("rejects unsupported/missing reports", value => {
    expect(() => parseBenchmarkEvidence(value)).toThrow(/Invalid benchmark evidence/);
  });

  it.each([NaN, Infinity, -1, null, "14"])("rejects invalid numeric measurements %s", value => {
    const data = structuredClone(fixture);
    (data.jev as Record<string, unknown>).trials = value;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/jev.trials/);
  });

  it("rejects missing required data without defaulting it to zero", () => {
    const data = structuredClone(fixture);
    delete (data.comms as Record<string, unknown>).batchCborBytes;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/batchCborBytes.*missing/);
  });

  it.each([
    ["javascript:alert(1)", "source"],
    ["https://evil.example/report", "source"],
    ["//evil.example/report.json", "download"],
    ["/benchmarks/raw/../../private.json", "download"],
    ["/benchmarks/raw/%2e%2e/private.json", "download"],
  ])("rejects unsafe links %s", (url, kind) => {
    const data = structuredClone(fixture);
    if (kind === "source") data.sourceUrl = url;
    else data.downloads[0].url = url;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/source URL|same-origin/);
  });

  it("rejects a summary that silently counts deferred choices as accepted", () => {
    const data = structuredClone(fixture);
    data.jev.acceptedMatches = 14;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/acceptedMatches/);
  });

  it("rejects a fabricated acceptance that bypasses the margin gate", () => {
    const data = structuredClone(fixture);
    const trial = data.jev.cases[0].trials[0];
    trial.acceptedChoice = "high";
    trial.disposition = "accept_selection";
    trial.acceptedMatch = true;
    data.jev.acceptedMatches += 1;
    data.jev.firstTrialAcceptedMatches += 1;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/confidence gates/);
  });

  it("rejects missing categories and repeated case identities", () => {
    const data = structuredClone(fixture);
    data.jev.cases[0].category = "next_step";
    expect(() => parseBenchmarkEvidence(data)).toThrow(/missing category/);
    data.jev.cases[0] = structuredClone(data.jev.cases[1]);
    expect(() => parseBenchmarkEvidence(data)).toThrow(/duplicate case/);
  });

  it("rejects missing scores, negative scores and lost abstention reasons", () => {
    const data = structuredClone(fixture);
    const scores = data.jev.cases[0].trials[0].scores as Record<string, number>;
    delete scores.normal;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/every hypothesis/);
    scores.normal = -0.2;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/nonnegative/);
    scores.normal = (fixture.jev.cases[0].trials[0].scores as Record<string, number>).normal;
    data.jev.cases[0].trials[0].reasons = [];
    expect(() => parseBenchmarkEvidence(data)).toThrow(/abstention needs a reason/);
  });

  it("rejects incorrect byte savings and ledger denominators", () => {
    const data = structuredClone(fixture);
    data.comms.reductionFraction = 0.9;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/reductionFraction/);
    data.comms.reductionFraction = fixture.comms.reductionFraction;
    data.spend.remainingUsd = 6;
    expect(() => parseBenchmarkEvidence(data)).toThrow(/spend/);
  });

  it("fetches the public projection with cancellation and no stale HTTP cache", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture)));
    vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();
    expect((await fetchBenchmarkEvidence(controller.signal)).jev.acceptedMatches).toBe(12);
    expect(fetch).toHaveBeenCalledWith("/benchmarks/latest.json", { signal: controller.signal, cache: "no-store" });
  });

  it("reports HTTP and malformed JSON failures clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("missing", { status: 404 })));
    await expect(fetchBenchmarkEvidence()).rejects.toThrow(/HTTP 404/);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>app fallback</html>")));
    await expect(fetchBenchmarkEvidence()).rejects.toThrow(/not valid JSON/);
  });
});
