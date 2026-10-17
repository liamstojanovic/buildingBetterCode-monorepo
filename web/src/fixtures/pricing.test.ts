import { describe, expect, it } from "vitest";
import { unknownUsage } from "../api/types";
import { initialRun, runReducer } from "../state/run";
import { mockRecording } from "./cargo";
import {
  estimateTokenCost,
  formatUnitDollars,
  jevPrice,
  modeledJevUsage,
  swarmPrices,
  unitRateDelta,
} from "./pricing";

const usage = {
  ...unknownUsage(),
  tokens_known: true,
  input_tokens: 7200,
  output_tokens: 2400,
};
describe("mock token price estimates", () => {
  it.each([
    [0, 0.072, 0.12, 0.192],
    [1, 0.0054, 0.009, 0.0144],
    [2, 0.0144, 0.024, 0.0384],
  ])(
    "prices provider %i using separate input/output rates",
    (index, input, output, total) => {
      const cost = estimateTokenCost(usage, swarmPrices[index])!;
      expect(cost.input).toBeCloseTo(input, 10);
      expect(cost.output).toBeCloseTo(output, 10);
      expect(cost.total).toBeCloseTo(total, 10);
    },
  );
  it("keeps unmeasured token costs unknown and known zero spend at zero", () => {
    expect(estimateTokenCost(unknownUsage(), swarmPrices[0])).toBeNull();
    expect(
      estimateTokenCost(
        { ...usage, input_tokens: 0, output_tokens: 0 },
        swarmPrices[0],
      ),
    ).toEqual({ input: 0, output: 0, total: 0 });
  });
  it("models a decision for each observed model task, including cache hits", () => {
    for (const scenario of ["cold", "warm", "changed"] as const) {
      const record = mockRecording("governed", scenario);
      const state = runReducer(initialRun(record.task), {
        type: "result",
        result: record.result,
      });
      expect(modeledJevUsage(initialRun(record.task)).decisions).toBe(0);
      const modeled = modeledJevUsage(state);
      expect(modeled).toEqual({
        decisions: 4,
        input_tokens: 1024,
        output_tokens: 4,
      });
      const charge = estimateTokenCost({ ...usage, ...modeled }, jevPrice)!;
      expect(charge.input).toBeCloseTo(0.000043008, 12);
      expect(charge.output).toBe(0);
      expect(charge.total).toBeCloseTo(0.000043008, 12);
      expect(state.result?.usage).toEqual(record.result.usage);
    }
  });
  it("keeps sub-microdollar unit prices visible", () => {
    expect(formatUnitDollars(jevPrice.inputPerMillion / 1_000_000)).toBe(
      "$0.000000042",
    );
    expect(unitRateDelta(10, 0.042)).toBeCloseTo(-0.000009958, 12);
  });
});
