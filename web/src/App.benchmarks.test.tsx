import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import evidence from "../public/benchmarks/latest.json";

vi.mock("./components/ExecutionGraph", () => ({
  ExecutionGraph: ({ state, onSelect }: any) => (
    <button onClick={() => onSelect(state.task.steps[0].step_id)}>
      Inspect first step
    </button>
  ),
  statusLabels: {
    pending: "Queued", running: "Executing", completed: "Completed", reused: "Reused",
    blocked: "Refused", awaiting_approval: "Approval required", failed: "Failed",
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

function installEvidence() {
  const fetcher = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify(evidence)));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function benchmarks() {
  fireEvent.click(screen.getByRole("button", { name: "Benchmarks workspace" }));
}

describe("benchmarks integrated with the guided execution demo", () => {
  it("loads only on entry and does not show a timed mock tour over measured results", async () => {
    vi.useFakeTimers();
    const fetcher = installEvidence();
    render(<App />);
    expect(fetcher).not.toHaveBeenCalled();
    await act(async () => benchmarks());
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByRole("main", { name: "Benchmarks workspace content" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Benchmarks workspace" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("14/14")).toBeVisible();
    expect(screen.getByText("12/14")).toBeVisible();
    expect(screen.queryByRole("region", { name: "canopy token costs" })).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/benchmarks/latest.json");
  });

  it("preserves execution inspection and benchmark category when switching workspaces", async () => {
    sessionStorage.setItem("canopy-demo-tour", "seen");
    const fetcher = installEvidence();
    render(<App />);
    fireEvent.click(within(screen.getByRole("region", { name: "Canopy execution" })).getByRole("button", { name: "Inspect first step" }));
    const inspected = screen.getByRole("complementary", { name: "Step inspector" }).textContent;
    benchmarks();
    await screen.findByRole("navigation", { name: "Benchmark categories" });
    fireEvent.click(screen.getByRole("button", { name: /CBOR-LD-EX savings/ }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "Step inspector" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Execution workspace" }));
    expect(screen.getByRole("complementary", { name: "Step inspector" }).textContent).toBe(inspected);
    expect(screen.getByRole("combobox", { name: "Data source" })).toHaveValue("mock");
    benchmarks();
    expect(screen.getByRole("button", { name: /CBOR-LD-EX savings/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("356,491 bytes")).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("suspends focus styling for benchmarks while preserving the completed guided repeat", async () => {
    vi.useFakeTimers();
    sessionStorage.setItem("canopy-demo-tour", "seen");
    installEvidence();
    const { container } = render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Run again" }));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(container.querySelector(".app-shell")).toHaveClass("presentation");
    await act(async () => benchmarks());
    expect(container.querySelector(".app-shell")).not.toHaveClass("presentation");
    expect(screen.getByText("RECORDED BENCHMARKS")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Execution workspace" }));
    expect(container.querySelector(".app-shell")).toHaveClass("presentation");
    expect(within(screen.getByRole("region", { name: "Canopy execution" })).getByText("7/7 steps")).toBeVisible();
    expect(screen.getByRole("meter", { name: "canopy Tokens" })).toHaveAttribute("aria-valuenow", "1028");
  });
});
