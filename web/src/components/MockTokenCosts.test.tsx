import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockRecording } from "../fixtures/cargo";
import { swarmPrices } from "../fixtures/pricing";
import { initialRun, runReducer } from "../state/run";
import { MockTokenCosts } from "./MockTokenCosts";

function run(
  mode: "baseline" | "governed",
  scenario: "cold" | "warm" | "changed" | "failed",
) {
  const record = mockRecording(mode, scenario);
  return runReducer(initialRun(record.task), {
    type: "result",
    result: record.result,
  });
}
describe("mock cost comparison", () => {
  it("includes modeled JeV in input and total costs and subtracts baseline per category", () => {
    const baseline = run("baseline", "warm");
    render(
      <MockTokenCosts
        state={run("governed", "warm")}
        baseline={baseline}
        variant="canopy"
        price={swarmPrices[0]}
      />,
    );
    expect(screen.getByLabelText("Input cost delta")).toHaveTextContent(
      "−$0.071957",
    );
    expect(screen.getByLabelText("Output cost delta")).toHaveTextContent(
      "−$0.120000",
    );
    expect(screen.getByLabelText("Total cost delta")).toHaveTextContent(
      "−$0.191957",
    );
    expect(screen.getByText("$0.000043 included")).toBeVisible();
    expect(screen.getByText(/JeV 1.13 · 4 modeled decisions/)).toBeVisible();
    expect(screen.getByText(/1,024 input \+ 4 output tokens/)).toBeVisible();
    expect(screen.getByText("$0.000000042")).toBeInTheDocument();
    expect(
      screen.getByText(/Effective Δ per original input token/),
    ).toHaveTextContent("−$0.000009994");
  });
  it("shows the routing overhead as an increase on a cold run", () => {
    const baseline = run("baseline", "cold");
    render(
      <MockTokenCosts
        state={run("governed", "cold")}
        baseline={baseline}
        variant="canopy"
        price={swarmPrices[0]}
      />,
    );
    expect(screen.getByLabelText("Input cost delta")).toHaveTextContent(
      "+$0.000043",
    );
    expect(screen.getByLabelText("Total cost delta")).toHaveTextContent(
      "+$0.000043",
    );
    expect(screen.getByText("$0.192043")).toBeVisible();
  });
  it("keeps JeV exclusive to Canopy and exposes sources for each swarm model", () => {
    const baseline = run("baseline", "cold");
    render(
      <MockTokenCosts
        state={baseline}
        baseline={baseline}
        variant="baseline"
        price={swarmPrices[0]}
      />,
    );
    expect(screen.queryByText(/JeV/i)).not.toBeInTheDocument();
    const table = screen.getByRole("table", { hidden: true });
    expect(within(table).getAllByRole("link", { hidden: true })).toHaveLength(
      3,
    );
    for (const model of swarmPrices)
      expect(within(table).getByText(model.name)).toHaveAttribute(
        "href",
        model.source,
      );
  });
  it("does not present a failed or incomplete pair as cost savings", () => {
    const baseline = run("baseline", "warm");
    const { rerender } = render(
      <MockTokenCosts
        state={run("governed", "failed")}
        baseline={baseline}
        variant="canopy"
        price={swarmPrices[0]}
      />,
    );
    expect(screen.getByLabelText("Total cost delta")).toHaveTextContent(
      "pending",
    );
    rerender(
      <MockTokenCosts
        state={initialRun(baseline.task)}
        baseline={baseline}
        variant="canopy"
        price={swarmPrices[0]}
      />,
    );
    expect(screen.getByLabelText("Total cost delta")).toHaveTextContent(
      "pending",
    );
    expect(screen.queryByText("$0.000000")).not.toBeInTheDocument();
  });
});
