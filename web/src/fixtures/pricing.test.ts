import { describe, expect, it } from "vitest";
import { unknownUsage } from "../api/types";
import { initialRun, runReducer } from "../state/run";
import { mockRecording } from "./cargo";
import {
  estimateTokenCost,
  formatUnitDollars,
  modeledJevUsage,
  providerPrices,
  unitRateDelta,
} from "./pricing";

// Arithmetic-only test rates; these are not published provider prices.
const rate = {
  id: "test-rate",
  name: "Test rate",
  inputPerMillion: 10,
  outputPerMillion: 50,
  source: "https://example.com",
};
const usage = {
  ...unknownUsage(),
  tokens_known: true,
  input_tokens: 7200,
  output_tokens: 2400,
};
describe("illustrative token estimates", () => {
  it("prices input and output separately", () => {
    expect(estimateTokenCost(usage, rate)).toEqual({
      input: 0.072,
      output: 0.12,
      total: 0.192,
    });
  });
  it("keeps unmeasured costs unknown and known zero spend at zero", () => {
    expect(estimateTokenCost(unknownUsage(), rate)).toBeNull();
    expect(
      estimateTokenCost({ ...usage, input_tokens: 0, output_tokens: 0 }, rate),
    ).toEqual({ input: 0, output: 0, total: 0 });
  });
  it("models a decision for every observed model task, including reuse, without altering runtime usage", () => {
    for (const scenario of ["cold", "warm", "changed"] as const) {
      const record = mockRecording("governed", scenario);
      const state = runReducer(initialRun(record.task), {
        type: "result",
        result: record.result,
      });
      expect(modeledJevUsage(initialRun(record.task)).decisions).toBe(0);
      expect(modeledJevUsage(state)).toEqual({
        decisions: 4,
        input_tokens: 1024,
        output_tokens: 4,
      });
      expect(state.result?.usage).toEqual(record.result.usage);
    }
  });
  it("uses all eleven supplied paid text rates and omits unsupplied models", () => {
    expect(providerPrices.map((provider) => provider.name)).toEqual([
      "OpenAI",
      "Anthropic",
      "Gemini",
    ]);
    expect(
      providerPrices.map((provider) =>
        provider.models.map((model) => [
          model.id,
          model.inputPerMillion,
          model.outputPerMillion,
        ]),
      ),
    ).toEqual([
      [
        ["gpt-6-astra", 10, 50],
        ["gpt-6.1-sol", 2, 10],
        ["gpt-6-luna", 0.1, 0.5],
      ],
      [
        ["claude-fable-5.1", 10, 50],
        ["claude-opus-5.5", 4, 20],
        ["claude-sonnet-5.5", 2, 10],
        ["claude-haiku-5.5", 0.1, 0.5],
      ],
      [
        ["gemini-4-argon", 2, 10],
        ["gemini-3.8-flash", 0.75, 3.75],
        ["gemini-3.6-flash", 0.75, 3.75],
        ["gemini-3.8-live", 0.75, 4.5],
      ],
    ]);
    for (const model of providerPrices.flatMap((provider) => provider.models)) {
      expect(model.evidence).toBe(
        model.id.startsWith("gemini") ? "user-provided" : "user-screenshot",
      );
      expect(model.verifiedAt).toBe("2026-10-17");
    }
  });
  it.each([
    ["gemini-4-argon", "2026-11-30T23:59:59.999Z", 0.0384],
    ["gemini-4-argon", "2026-12-01T00:00:00.000Z", 0.0768],
    ["gemini-3.8-flash", "2026-12-31T23:59:59.999Z", 0.0144],
    ["gemini-3.8-flash", "2027-01-01T00:00:00.000Z", 0.0288],
    ["gemini-3.6-flash", "2026-12-31T23:59:59.999Z", 0.0144],
    ["gemini-3.6-flash", "2027-01-01T00:00:00.000Z", 0.0288],
    ["gemini-3.8-live", "2026-10-17T00:00:00.000Z", 0.0162],
    ["gemini-3.8-live", "2027-01-01T00:00:00.000Z", 0.0162],
  ])("prices %s at its applicable paid text rate on %s", (id, date, total) => {
    const price = providerPrices
      .find((provider) => provider.id === "gemini")!
      .models.find((model) => model.id === id)!;
    expect(estimateTokenCost(usage, price, new Date(date))!.total).toBeCloseTo(
      total,
      10,
    );
  });
  it("keeps sub-microdollar unit prices visible", () => {
    expect(formatUnitDollars(0.042 / 1_000_000)).toBe("$0.000000042");
    expect(unitRateDelta(10, 0.042)).toBeCloseTo(-0.000009958, 12);
  });
});
