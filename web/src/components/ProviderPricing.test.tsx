import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockRecording } from "../fixtures/cargo";
import { providerPrices, type ProviderPrices } from "../fixtures/pricing";
import { initialRun, runReducer, type RunState } from "../state/run";
import { ProviderPricing } from "./ProviderPricing";

// Test-only rates exercise the verified-data path without publishing fictitious prices.
const providers: ProviderPrices[] = [
  {
    id: "openai",
    name: "OpenAI",
    source: "https://example.com/pricing",
    models: [
      {
        id: "older-test",
        name: "Older test model",
        inputPerMillion: 2,
        outputPerMillion: 10,
        source: "https://example.com/older",
        verifiedAt: "2026-10-17",
        flagship: false,
      },
      {
        id: "flagship-test",
        name: "Flagship test model",
        inputPerMillion: 10,
        outputPerMillion: 50,
        source: "https://example.com/flagship",
        verifiedAt: "2026-10-17",
        flagship: true,
      },
    ],
  },
];
function run(
  mode: "baseline" | "governed",
  scenario: "cold" | "warm" | "failed" = "warm",
) {
  const record = mockRecording(mode, scenario);
  return runReducer(initialRun(record.task), {
    type: "result",
    result: record.result,
  });
}
function progressing(tokens: number): RunState {
  const state = run("baseline");
  return {
    ...state,
    status: "running",
    result: null,
    events: [
      {
        ...state.events[0],
        event_type: "budget_settled",
        payload: {
          usage: {
            ...state.result!.usage,
            input_tokens: tokens,
            output_tokens: 0,
          },
        },
      },
    ],
  };
}
afterEach(() => vi.useRealTimers());
describe("provider pricing comparisons", () => {
  it("shows three provider groups and omits all unverified names and rates", () => {
    render(
      <ProviderPricing
        providers={providerPrices.map((provider) => ({
          ...provider,
          models: [],
        }))}
        baseline={run("baseline")}
        canopy={run("governed")}
      />,
    );
    for (const name of ["OpenAI", "Anthropic", "Gemini"]) {
      const card = screen.getByRole("article", { name: `${name} pricing` });
      expect(
        within(card).getByText("Verified rates unavailable"),
      ).toBeVisible();
      fireEvent.click(within(card).getByText("All models"));
      expect(
        within(card).getByText("No verified text pricing is available yet."),
      ).toBeVisible();
      expect(within(card).getByRole("link")).toHaveTextContent(
        `Official ${name} pricing`,
      );
    }
    expect(screen.queryByText(/\$0/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Astra|Fable|Opus|Argon|Sonnet/),
    ).not.toBeInTheDocument();
  });
  it("renders the supplied OpenAI and Anthropic models, including context-tier notes", () => {
    render(
      <ProviderPricing
        baseline={run("baseline", "cold")}
        canopy={run("governed", "cold")}
      />,
    );
    for (const [provider, flagship, count] of [
      ["OpenAI", "GPT-6 Astra", 3],
      ["Anthropic", "Claude Fable 5.1", 4],
    ] as const) {
      const card = screen.getByRole("article", { name: `${provider} pricing` });
      const defaultModel = within(card).getAllByLabelText(
        `${flagship} comparison`,
      )[0];
      expect(defaultModel).toBeVisible();
      expect(within(defaultModel).getAllByText("$0.192000")).toHaveLength(2);
      expect(
        within(defaultModel).getByText(
          "Pricing screenshot supplied 2026-10-17",
        ),
      ).toBeVisible();
      fireEvent.click(within(card).getByText(`All models (${count})`));
    }
    expect(screen.getByLabelText("GPT-6.1 Sol comparison")).toHaveTextContent(
      "$0.038400",
    );
    expect(screen.getByLabelText("GPT-6 Luna comparison")).toHaveTextContent(
      "$0.001920",
    );
    expect(
      screen.getByLabelText("Claude Opus 5.5 comparison"),
    ).toHaveTextContent("$0.076800");
    expect(
      screen.getByLabelText("Claude Sonnet 5.5 comparison"),
    ).toHaveTextContent("$0.038400");
    expect(
      screen.getByLabelText("Claude Haiku 5.5 comparison"),
    ).toHaveTextContent("$0.001920");
    expect(
      screen.getByLabelText("Claude Haiku 5.5 comparison"),
    ).toHaveTextContent("Above 100,000: $0.50 input / $2.50 output");
    expect(
      screen.getAllByLabelText("GPT-6 Astra comparison")[0],
    ).toHaveTextContent("Long context: $20 input / $75 output");
  });
  it("shows all four supplied Gemini models with paid text rates and promotional dates", () => {
    const baseline = run("baseline", "cold"),
      canopy = run("governed", "cold");
    const { rerender } = render(
      <ProviderPricing
        baseline={baseline}
        canopy={canopy}
        asOf={new Date("2026-10-17T12:00:00Z")}
      />,
    );
    const card = screen.getByRole("article", { name: "Gemini pricing" });
    const argon = within(card).getAllByLabelText(
      "Gemini 4 Argon comparison",
    )[0];
    expect(argon).toBeVisible();
    expect(within(argon).getAllByText("$0.038400")).toHaveLength(2);
    expect(argon).toHaveTextContent("Pricing supplied 2026-10-17");
    fireEvent.click(within(card).getByText("All models (4)"));
    for (const name of ["Gemini 3.8 Flash", "Gemini 3.6 Flash"]) {
      const row = within(card).getByLabelText(`${name} comparison`);
      expect(row).toBeVisible();
      expect(row).toHaveTextContent("$0.014400");
      expect(row).toHaveTextContent("from January 1, 2027");
    }
    const live = within(card).getByLabelText("Gemini 3.8 Live comparison");
    expect(live).toHaveTextContent("$0.016200");
    expect(live).toHaveTextContent("Input $0.75 · Output $4.50");
    expect(live).not.toHaveTextContent(/audio|video|image|\/min/i);
    rerender(
      <ProviderPricing
        baseline={baseline}
        canopy={canopy}
        asOf={new Date("2027-01-01T00:00:00Z")}
      />,
    );
    expect(argon).toHaveTextContent("Input $4.00 · Output $20.00");
    expect(argon).toHaveTextContent("$0.076800");
    expect(
      within(card).getByLabelText("Gemini 3.8 Flash comparison"),
    ).toHaveTextContent("$0.028800");
    expect(live).toHaveTextContent("$0.016200");
    expect(within(card).queryByText(/Omni/)).not.toBeInTheDocument();
  });
  it("keeps rates fixed for a simulation and applies the new date at the next start", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-30T23:59:59Z"));
    const baseline = run("baseline", "cold"),
      canopy = run("governed", "cold");
    const { rerender } = render(
      <ProviderPricing baseline={baseline} canopy={canopy} />,
    );
    const argon = screen.getAllByLabelText("Gemini 4 Argon comparison")[0];
    expect(argon).toHaveTextContent("Input $2.00 · Output $10.00");
    vi.setSystemTime(new Date("2026-12-01T00:00:01Z"));
    rerender(<ProviderPricing baseline={baseline} canopy={canopy} />);
    expect(argon).toHaveTextContent("Input $2.00 · Output $10.00");
    rerender(<ProviderPricing baseline={progressing(100)} canopy={canopy} />);
    expect(argon).toHaveTextContent("Input $4.00 · Output $20.00");
  });
  it("defaults to the flagship and expands every model with paired costs", () => {
    render(
      <ProviderPricing
        providers={providers}
        baseline={run("baseline")}
        canopy={run("governed")}
      />,
    );
    expect(
      screen.getAllByLabelText("Flagship test model comparison")[0],
    ).toBeVisible();
    expect(
      screen.getByLabelText("Older test model comparison"),
    ).not.toBeVisible();
    fireEvent.click(screen.getByText("All models (2)"));
    expect(screen.getByLabelText("Older test model comparison")).toBeVisible();
    const flagship = screen.getAllByLabelText(
      "Flagship test model comparison",
    )[0];
    expect(within(flagship).getByText("$0.192000")).toBeVisible();
    expect(within(flagship).getByText("$0.000000")).toBeVisible();
    expect(within(flagship).getByText("Higher LLM spend")).toBeVisible();
    expect(
      screen.getByText(
        /Total Canopy cost and net dollar savings remain unmeasured/,
      ),
    ).toBeVisible();
  });
  it("updates mid-run, emphasizes faster accumulation, and resets without stale amounts", () => {
    vi.useFakeTimers();
    const canopy = run("governed");
    const { rerender } = render(
      <ProviderPricing
        providers={providers}
        baseline={progressing(100)}
        canopy={canopy}
      />,
    );
    rerender(
      <ProviderPricing
        providers={providers}
        baseline={progressing(200)}
        canopy={canopy}
      />,
    );
    const flagship = screen.getAllByLabelText(
      "Flagship test model comparison",
    )[0];
    expect(within(flagship).getByText("$0.002000")).toBeVisible();
    expect(within(flagship).getByText("Accumulating faster")).toBeVisible();
    act(() => vi.advanceTimersByTime(900));
    expect(
      within(flagship).queryByText("Accumulating faster"),
    ).not.toBeInTheDocument();
    rerender(
      <ProviderPricing
        providers={providers}
        baseline={initialRun(canopy.task)}
        canopy={initialRun(canopy.task)}
      />,
    );
    expect(within(flagship).getAllByText("—")).toHaveLength(2);
    expect(within(flagship).queryByText("$0.002000")).not.toBeInTheDocument();
  });
  it("does not label a failed pair as savings or a cold pair as a reuse benefit", () => {
    const { rerender } = render(
      <ProviderPricing
        providers={providers}
        baseline={run("baseline")}
        canopy={run("governed", "failed")}
      />,
    );
    expect(screen.queryByText("Higher LLM spend")).not.toBeInTheDocument();
    rerender(
      <ProviderPricing
        providers={providers}
        baseline={run("baseline", "cold")}
        canopy={run("governed", "cold")}
      />,
    );
    expect(screen.queryByText("Higher LLM spend")).not.toBeInTheDocument();
  });
});
