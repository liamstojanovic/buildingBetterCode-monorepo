import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { normalizeTask, scenarioTask } from "./App";
import { cargoTask } from "./fixtures/cargo";
import coreBundle from "../public/recordings/core-synthetic.json";

vi.mock("./components/ExecutionGraph", () => ({
  ExecutionGraph: () => <div>Execution graph</div>,
  statusLabels: {
    pending: "Queued",
    running: "Executing",
    completed: "Completed",
    reused: "Reused",
    blocked: "Refused",
    awaiting_approval: "Approval required",
    failed: "Failed",
  },
}));

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("scrollTo", vi.fn());
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("guided repeat demo", () => {
  it("starts with equal cold results and repeats automatically without a scenario selection", async () => {
    vi.useFakeTimers();
    render(<App />);
    expect(
      screen.getByRole("combobox", { name: "Workload scenario" }),
    ).toHaveValue("cold");
    expect(
      screen.getByRole("meter", { name: "baseline Tokens" }),
    ).toHaveAttribute("aria-valuenow", "9600");
    expect(
      screen.getByRole("meter", { name: "canopy Tokens" }),
    ).toHaveAttribute("aria-valuenow", "10628");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500);
    });
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Follow the work through the swarm.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Same task. A different cost.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Run again" }));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Workload scenario" }),
    ).toHaveValue("warm");
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    expect(
      screen.getByRole("meter", { name: "canopy Tokens" }),
    ).toHaveAttribute("aria-valuenow", "1028");
    expect(
      screen.getByRole("meter", { name: "canopy Tokens" }),
    ).toHaveAttribute("aria-valuemax", "9600");
    expect(screen.getByRole("meter", { name: "canopy Time" })).toHaveAttribute(
      "aria-valuenow",
      "455",
    );
    expect(screen.getByRole("meter", { name: "canopy Time" })).toHaveAttribute(
      "aria-valuemax",
      "3010",
    );
    expect(
      screen.getByRole("meter", { name: "canopy Tool calls" }),
    ).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText(/85% less time/)).toBeVisible();
    expect(screen.getByRole("dialog")).toHaveTextContent("3 OF 4");
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Read the cost of the repeat.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Inspect the execution tree.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Tree view" }));
    fireEvent.click(screen.getByRole("button", { name: "Finish tour" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const lane = screen.getByRole("region", { name: "Canopy execution" });
    expect(within(lane).getAllByText("CACHE HIT")).toHaveLength(7);
    expect(
      within(lane).getByText(/also depends on extract_manifest/),
    ).toBeVisible();
    expect(
      within(lane).getByRole("meter", { name: "canopy Tokens" }),
    ).toHaveAttribute("aria-valuenow", "1028");
    fireEvent.click(
      within(lane).getByRole("button", { name: /extract_invoice/ }),
    );
    expect(
      screen.getByRole("complementary", { name: "Step inspector" }),
    ).toHaveTextContent("Read invoice");
  });

  it("reprices both lanes when the mock model changes and hides estimates for recordings", async () => {
    render(<App />);
    fireEvent.change(
      screen.getByRole("combobox", { name: "Mock swarm model" }),
      { target: { value: "gemini-3.8-flash" } },
    );
    const baseline = screen.getByRole("region", {
      name: "baseline token costs",
    });
    const canopy = screen.getByRole("region", { name: "canopy token costs" });
    expect(within(baseline).getByText("$0.014400")).toBeVisible();
    expect(within(canopy).getByText("$0.014443")).toBeVisible();
    expect(
      within(canopy).getByText(/Promotional rates through December 31, 2026/),
    ).toBeVisible();
    fireEvent.change(
      screen.getByRole("combobox", { name: "Mock swarm model" }),
      { target: { value: "claude-sonnet-5.5" } },
    );
    expect(within(baseline).getByText("$0.038400")).toBeVisible();
    expect(within(canopy).getByText("$0.038443")).toBeVisible();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(coreBundle))),
    );
    await act(async () =>
      fireEvent.change(screen.getByRole("combobox", { name: "Data source" }), {
        target: { value: "recorded" },
      }),
    );
    expect(
      screen.queryByRole("region", { name: "canopy token costs" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Mock swarm model" }),
    ).not.toBeInTheDocument();
  });

  it("reopens a dismissed guide from Take the tour and supports Back and Skip", () => {
    sessionStorage.setItem("canopy-demo-tour", "seen");
    render(<App />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Take the tour" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("1 OF 4");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("2 OF 4");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("1 OF 4");
    fireEvent.click(screen.getByRole("button", { name: "Skip tour" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("canopy-demo-tour")).toBe("seen");
  });

  it("lets keyboard users dismiss the spotlight and respects the session dismissal", async () => {
    vi.useFakeTimers();
    const { unmount } = render(<App />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500);
    });
    expect(screen.getByRole("dialog")).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    unmount();
    render(<App />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});

describe("workspace controls and task snapshots", () => {
  it("starts in visibly mocked mode with unknown savings", () => {
    render(<App />);
    expect(screen.getByText("Demo Mode")).toBeInTheDocument();
    expect(screen.getAllByText("Measurement pending").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByRole("button", { name: /run again/i })).toBeEnabled();
  });
  it("renders the methodology and closes it", () => {
    render(<App />);
    fireEvent.click(
      screen.getByRole("button", { name: "Methodology and demo guide" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Five paired repetitions",
    );
    fireEvent.click(screen.getByRole("button", { name: "Close methodology" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("preserves unchanged evidence including observation timestamps", () => {
    const task = scenarioTask(cargoTask, "warm");
    expect(task.evidence).toEqual(cargoTask.evidence);
    expect(task).not.toBe(cargoTask);
    const changed = scenarioTask(cargoTask, "changed");
    expect(changed.evidence[0]).toEqual(cargoTask.evidence[0]);
    expect(changed.evidence[1]).toEqual(cargoTask.evidence[1]);
    expect(changed.evidence[2].version).not.toBe(cargoTask.evidence[2].version);
    expect(changed.evidence[2].observed_at).toBe(
      cargoTask.evidence[2].observed_at,
    );
  });
  it("rejects unsupported task schemas before submission", () => {
    expect(() =>
      normalizeTask({ ...cargoTask, schema_version: "2" }),
    ).toThrow();
  });
  it("prevents running the previous workload while a new data source loads", async () => {
    let resolve!: (value: Response) => void;
    const fetcher = vi.fn(
      () =>
        new Promise<Response>((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    try {
      render(<App />);
      fireEvent.change(screen.getByRole("combobox", { name: "Data source" }), {
        target: { value: "recorded" },
      });
      expect(
        screen.queryByRole("button", { name: "Run again" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("combobox", { name: "Data source" }),
      ).toBeDisabled();
      await act(async () => {
        resolve(new Response(JSON.stringify(coreBundle), { status: 200 }));
      });
      expect(screen.getByText("RECORDED")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
