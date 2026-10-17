import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import evidence from "../../public/benchmarks/latest.json";
import { parseBenchmarkEvidence } from "../api/benchmarks";
import { Benchmarks } from "./Benchmarks";

afterEach(() => vi.unstubAllGlobals());

function response(value: unknown = evidence) {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}

function mockEvidence() {
  const fetch = vi.fn().mockImplementation(async () => response());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

async function show() {
  mockEvidence();
  const view = render(<Benchmarks />);
  await screen.findByRole("navigation", { name: "Benchmark categories" });
  return view;
}

function decision() {
  return within(
    screen.getByRole("region", { name: "Measured decision detail" }),
  );
}

describe("measured benchmark workspace", () => {
  it("shows actual results and keeps the correct business choice deferred", async () => {
    await show();
    expect(screen.getByText("14/14")).toBeInTheDocument();
    expect(screen.getByText("12/14")).toBeInTheDocument();
    expect(screen.getByText("53.79%")).toBeInTheDocument();
    const detail = decision();
    expect(
      detail.getByText("Raw JEV choice").nextElementSibling,
    ).toHaveTextContent("high");
    expect(
      detail.getByText("After acceptance gate").nextElementSibling,
    ).toHaveTextContent("Defer");
    expect(
      detail.getByText(/gap of 0.115 is below the 0.15 minimum/),
    ).toBeInTheDocument();
    expect(
      detail.getByText(/field still needs explanation generation/),
    ).toBeInTheDocument();
    expect(
      detail.getByText("Action executed").nextElementSibling,
    ).toHaveTextContent("No");
    expect(
      screen.getByText(/7 distinct cases are a small demonstration/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/physical 12 GB GPU has not been tested/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Historical hosted cost, replacement cost savings and energy savings are unknown/,
      ),
    ).toBeInTheDocument();
  });

  it("separates the cold load from the second business trial", async () => {
    await show();
    const detail = decision();
    expect(
      detail.getByText("End-to-end trial").nextElementSibling,
    ).toHaveTextContent("6,469.67 ms");
    fireEvent.click(
      detail.getByRole("button", { name: "Pass 2 · warm worker" }),
    );
    expect(
      detail.getByText("End-to-end trial").nextElementSibling,
    ).toHaveTextContent("164.2 ms");
    expect(
      detail.getByText("After acceptance gate").nextElementSibling,
    ).toHaveTextContent("Defer");
    expect(screen.getByText("301.87 ms")).toBeInTheDocument();
    expect(
      screen.getByText(/do not establish a controlled speedup/),
    ).toBeInTheDocument();
  });

  it("lets the presenter inspect all three tool selections with prebound arguments", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: /02 Tool selection/ }));
    const cases = within(
      screen.getByRole("navigation", { name: "Benchmark cases" }),
    );
    expect(cases.getAllByRole("button")).toHaveLength(3);
    for (const name of [
      "knowledge_search",
      "incident_lookup",
      "ticket_lookup",
    ]) {
      fireEvent.click(
        cases.getByRole("button", {
          name: new RegExp(name.replaceAll("_", " ")),
        }),
      );
      const detail = decision();
      expect(
        detail.getByText("After acceptance gate").nextElementSibling,
      ).toHaveTextContent(name);
      expect(
        detail.getByText(/Tool arguments were prebound/),
      ).toBeInTheDocument();
      expect(
        detail.getByText("Action executed").nextElementSibling,
      ).toHaveTextContent("No");
    }
  });

  it("shows all next-step outcomes alongside the model-free rule baseline", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: /03 Next steps/ }));
    const cases = within(
      screen.getByRole("navigation", { name: "Benchmark cases" }),
    );
    expect(cases.getAllByRole("button")).toHaveLength(3);
    for (const name of ["retrieve", "clarify", "stop"]) {
      fireEvent.click(cases.getByRole("button", { name: new RegExp(name) }));
      const detail = decision();
      expect(
        detail.getByText("After acceptance gate").nextElementSibling,
      ).toHaveTextContent(name);
      expect(detail.getByText(/Deterministic baseline:/)).toHaveTextContent(
        name,
      );
      expect(detail.getByText(/without a model call/)).toBeInTheDocument();
    }
  });

  it("pairs CBOR payload savings with serialization cost, JSON fallback and scoped mailbox evidence", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: /CBOR-LD-EX savings/ }));
    const chart = within(
      screen.getByRole("region", { name: "CBOR byte comparison" }),
    );
    expect(chart.getByText("771,411 bytes")).toBeInTheDocument();
    expect(chart.getByText("356,491 bytes")).toBeInTheDocument();
    expect(chart.getByText("53.79%")).toBeInTheDocument();
    expect(chart.getByText("Lossless round trip verified")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Shared-context CBOR encode time was higher than compact JSON, and decode time was higher than compact JSON/,
      ),
    ).toBeInTheDocument();
    const timings = within(
      screen.getByRole("table", { name: "Local serialization times" }),
    );
    expect(timings.getByText("32.05 ms")).toBeInTheDocument();
    expect(timings.getByText("72.39 ms")).toBeInTheDocument();
    expect(
      screen.getByText("Selected format").nextElementSibling,
    ).toHaveTextContent("JSON");
    expect(
      screen.getByText("Duplicate messages suppressed").nextElementSibling,
    ).toHaveTextContent("36");
    expect(screen.getByText(/One local sample per path/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /Byte savings are not token, dollar, network-latency or energy savings/,
      ),
    ).toBeInTheDocument();
  });

  it("derives narration from a refreshed valid report with changed outcomes and measurements", async () => {
    const changed = parseBenchmarkEvidence(evidence);
    // A smaller refreshed sample, with three business trials and mixed outcomes.
    changed.jev.cases = changed.jev.cases.filter(
      (item) => item.expectedChoice !== "knowledge_search",
    );
    const business = changed.jev.cases.find(
      (item) => item.category === "business_decision",
    )!;
    const first = business.trials[0];
    Object.assign(first, {
      rawChoice: "normal",
      scores: { high: 0.3, normal: 0.6 },
      selectedEntailment: 0.6,
      margin: 0.3,
      rawMatch: false,
      reasons: ["selected_entailment_below_threshold"],
    });
    const correct = structuredClone(business.trials[1]);
    Object.assign(correct, {
      trial: 3,
      elapsedMs: 200,
      scores: { high: 0.99, normal: 0.1 },
      selectedEntailment: 0.99,
      margin: 0.89,
      acceptedChoice: "high",
      disposition: "accept_selection",
      acceptedMatch: true,
      reasons: [],
    });
    business.trials.push(correct);
    Object.assign(business.trials[1], {
      rawChoice: "normal",
      scores: { high: 0.1, normal: 0.99 },
      selectedEntailment: 0.99,
      margin: 0.89,
      acceptedChoice: "normal",
      disposition: "accept_selection",
      rawMatch: false,
      acceptedMatch: false,
      reasons: [],
    });
    const trials = changed.jev.cases.flatMap((item) => item.trials);
    changed.jev.distinctCases = changed.jev.cases.length;
    changed.jev.trials = trials.length;
    changed.jev.rawMatches = trials.filter((trial) => trial.rawMatch).length;
    changed.jev.acceptedMatches = trials.filter(
      (trial) => trial.acceptedMatch,
    ).length;
    changed.jev.firstTrialRawMatches = changed.jev.cases.filter(
      (item) => item.trials[0].rawMatch,
    ).length;
    changed.jev.firstTrialAcceptedMatches = changed.jev.cases.filter(
      (item) => item.trials[0].acceptedMatch,
    ).length;
    const warmTimes = trials
      .filter((trial) => !trial.cold)
      .map((trial) => trial.elapsedMs)
      .sort((a, b) => a - b);
    const midpoint = Math.floor(warmTimes.length / 2);
    changed.jev.warm = {
      count: warmTimes.length,
      minMs: warmTimes[0],
      maxMs: warmTimes.at(-1)!,
      medianMs:
        warmTimes.length % 2
          ? warmTimes[midpoint]
          : (warmTimes[midpoint - 1] + warmTimes[midpoint]) / 2,
    };
    changed.spend.providerUsd = 3;
    changed.spend.remainingUsd = 2;
    Object.assign(
      changed.comms.timings.find((timing) => timing.encoding === "cbor")!,
      { encodeMs: 1, decodeMs: 1 },
    );
    // Verify this exercises a supported report, rather than bypassing the reader.
    const valid = parseBenchmarkEvidence(changed);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => response(valid)),
    );
    render(<Benchmarks />);
    await screen.findByRole("navigation", { name: "Benchmark categories" });
    expect(screen.getByText("6 distinct cases · 13 calls")).toBeInTheDocument();
    expect(
      screen.getByText("1 distinct case · 3 recorded calls"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("1/3 business trials deferred"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/6 distinct cases are a small demonstration/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/The \$3 incremental provider spend applies/),
    ).toBeInTheDocument();
    expect(
      decision().getByText(
        /raw choice does not match expected and stays deferred/,
      ),
    ).toBeInTheDocument();
    expect(
      decision().getByText(/Selected entailment 0.6 is below the 0.8 minimum/),
    ).toBeInTheDocument();
    expect(
      decision().getByText(/Deferred in 1 of 3 recorded trials/),
    ).toBeInTheDocument();
    expect(decision().queryByText(/The gap of/)).not.toBeInTheDocument();
    expect(screen.queryByText(/both passes/)).not.toBeInTheDocument();
    fireEvent.click(
      decision().getByRole("button", { name: "Pass 2 · warm worker" }),
    );
    expect(
      decision().getByText("Accepted · expected mismatch"),
    ).toBeInTheDocument();
    expect(
      decision().getByText(/Acceptance is not proof of correctness/),
    ).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("navigation", { name: "Benchmark cases" }),
      ).getByText(/1\/3 accepted matches/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /CBOR-LD-EX savings/ }));
    expect(
      screen.getByText(
        /Shared-context CBOR encode time was lower than compact JSON, and decode time was lower than compact JSON/,
      ),
    ).toBeInTheDocument();
  });

  it("does not fetch before activation and preserves loaded state across workspace switches", async () => {
    const fetch = mockEvidence();
    const view = render(<Benchmarks active={false} />);
    expect(fetch).not.toHaveBeenCalled();
    view.rerender(<Benchmarks active />);
    await screen.findByRole("navigation", { name: "Benchmark categories" });
    fireEvent.click(screen.getByRole("button", { name: /CBOR-LD-EX savings/ }));
    view.rerender(<Benchmarks active={false} />);
    view.rerender(<Benchmarks active />);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: /CBOR-LD-EX savings/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it.each([
    ["missing", () => new Response("Not found", { status: 404 })],
    [
      "invalid",
      () => response({ schemaVersion: "canopy.benchmark_evidence/v1" }),
    ],
  ])(
    "shows no invented results when evidence is %s and supports retry",
    async (_name, initialResponse) => {
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(initialResponse())
        .mockImplementation(async () => response());
      vi.stubGlobal("fetch", fetch);
      render(<Benchmarks />);
      await screen.findByRole("alert");
      expect(screen.queryByText("14/14")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("navigation", { name: "Benchmark categories" }),
      ).not.toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Retry loading evidence" }),
      );
      await screen.findByText("14/14");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(fetch).toHaveBeenCalledTimes(2);
    },
  );

  it("ignores an obsolete fetch result after a workspace change", async () => {
    let finishFirst!: (value: Response) => void;
    const fetch = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockImplementation(async () => response());
    vi.stubGlobal("fetch", fetch);
    const view = render(<Benchmarks />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const firstSignal = fetch.mock.calls[0][1].signal as AbortSignal;
    view.rerender(<Benchmarks active={false} />);
    expect(firstSignal.aborted).toBe(true);
    view.rerender(<Benchmarks active />);
    await screen.findByText("14/14");
    finishFirst(new Response("old failure", { status: 500 }));
    await waitFor(() =>
      expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("14/14")).toBeInTheDocument();
  });
});
