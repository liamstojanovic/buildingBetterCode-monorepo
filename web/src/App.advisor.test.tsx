import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { HttpRunClient } from "./api/client";
import type { RunObserver, RunResult } from "./api/types";
import { parseBundle } from "./api/recorded";
import demo from "../public/recordings/advisor-demo.json";
import coreBundle from "../public/recordings/core-synthetic.json";
const warmResult = parseBundle(coreBundle).recordings.warm.result;

vi.mock("./components/ExecutionGraph", () => ({
  ExecutionGraph: ({ state, onSelect }: any) => (
    <button onClick={() => onSelect(state.task.steps[0].step_id)}>
      Inspect first step
    </button>
  ),
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

function openAdvisor() {
  fireEvent.click(
    screen.getByRole("button", { name: "Optimization workspace" }),
  );
}
function openExecution() {
  fireEvent.click(screen.getByRole("button", { name: "Execution workspace" }));
}
async function importReport(value: unknown, size?: number) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  const file = new File([text], "advisor.json", { type: "application/json" });
  Object.defineProperty(file, "text", { value: async () => text });
  if (size !== undefined) Object.defineProperty(file, "size", { value: size });
  await act(async () =>
    fireEvent.change(screen.getByLabelText("Import advisor report"), {
      target: { files: [file] },
    }),
  );
}
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("advisor workspace integration", () => {
  it("starts empty with advisor-specific provenance and makes local imports without network requests", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    render(<App />);
    openAdvisor();
    expect(screen.getByText("Bring an advisor report")).toBeVisible();
    expect(screen.getByText("NO ADVISOR REPORT")).toBeVisible();
    await importReport(demo);
    expect(screen.getByText("IMPORTED ADVISOR REPORT")).toBeVisible();
    expect(screen.getByText("advisor-demo")).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
    openExecution();
    expect(screen.getByText("MOCK DATA")).toBeVisible();
    openAdvisor();
    expect(screen.getByText("advisor-demo")).toBeVisible();
  });

  it("keeps the prior report after invalid, unsupported, or oversized imports", async () => {
    render(<App />);
    openAdvisor();
    await importReport(demo);
    for (const invalid of [
      "{",
      { format: "canopy-comparison/v1" },
      { ...demo, analyzer_version: "future" },
    ]) {
      await importReport(invalid);
      expect(screen.getByRole("alert")).toBeVisible();
      expect(screen.getByText("advisor-demo")).toBeVisible();
    }
    await importReport(demo, 20 * 1024 * 1024 + 1);
    expect(screen.getByRole("alert")).toHaveTextContent("20 MiB");
    expect(screen.getByText("advisor-demo")).toBeVisible();
    await importReport(demo);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("loads the bundled report and preserves it on a failed demo reload", async () => {
    // jsdom's Blob lacks the browser Blob.text() method used by this import.
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        blob: async () => ({
          size: JSON.stringify(demo).length,
          text: async () => JSON.stringify(demo),
        }),
      })
      .mockResolvedValueOnce(new Response("Missing", { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    render(<App />);
    openAdvisor();
    fireEvent.click(screen.getByRole("button", { name: "Load advisor demo" }));
    await vi.waitFor(() => {
      const error = screen.queryByRole("alert");
      if (error) throw new Error(error.textContent ?? "Demo import failed");
      expect(screen.getByText("advisor-demo")).toBeVisible();
    });
    expect(screen.getByText("SYNTHETIC ADVISOR DEMO")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Load advisor demo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be loaded",
    );
    expect(screen.getByText("advisor-demo")).toBeVisible();
    expect(
      fetcher.mock.calls.every(
        ([url]) => url === "/recordings/advisor-demo.json",
      ),
    ).toBe(true);
  });

  it("retains recorded results, selected steps, and presentation state across imports", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(coreBundle))),
    );
    vi.stubGlobal("scrollTo", vi.fn());
    render(<App />);
    await act(async () =>
      fireEvent.change(screen.getByRole("combobox", { name: "Data source" }), {
        target: { value: "recorded" },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Run comparison" }));
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    fireEvent.click(screen.getByRole("button", { name: "Exit focus view" }));
    const lane = screen.getByRole("region", { name: "Canopy execution" });
    expect(within(lane).getByText("3/3 steps")).toBeVisible();
    fireEvent.click(
      within(lane).getByRole("button", { name: "Inspect first step" }),
    );
    const inspected = screen.getByRole("complementary", {
      name: "Step inspector",
    }).textContent;
    openAdvisor();
    await importReport(demo);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.queryByRole("complementary", { name: "Step inspector" }),
    ).not.toBeInTheDocument();
    openExecution();
    expect(screen.getByRole("combobox", { name: "Data source" })).toHaveValue(
      "recorded",
    );
    expect(
      within(
        screen.getByRole("region", { name: "Canopy execution" }),
      ).getByText("3/3 steps"),
    ).toBeVisible();
    expect(
      screen.getByRole("complementary", { name: "Step inspector" }).textContent,
    ).toBe(inspected);
  });

  it("keeps live observation active and accepts incoming results while the advisor is open", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify(coreBundle.recordings.warm.task)),
      ),
    );
    vi.stubGlobal("scrollTo", vi.fn());
    let observer!: RunObserver;
    let signal!: AbortSignal;
    const execute = vi
      .spyOn(HttpRunClient.prototype, "execute")
      .mockImplementation((_request, incoming, abort) => {
        observer = incoming;
        signal = abort;
        incoming.onRun({
          ...warmResult,
          status: "running",
          finished_at: null,
        });
        return new Promise(() => {});
      });
    render(<App />);
    await act(async () =>
      fireEvent.change(screen.getByRole("combobox", { name: "Data source" }), {
        target: { value: "live" },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Run comparison" }));
    fireEvent.click(screen.getByRole("button", { name: "Exit focus view" }));
    expect(execute).toHaveBeenCalledTimes(1);
    openAdvisor();
    await importReport(demo);
    expect(signal.aborted).toBe(false);
    act(() => observer.onResult(warmResult));
    openExecution();
    expect(screen.getByRole("combobox", { name: "Data source" })).toHaveValue(
      "live",
    );
    expect(
      within(
        screen.getByRole("region", { name: "Canopy execution" }),
      ).getByText("3/3 steps"),
    ).toBeVisible();
    expect(signal.aborted).toBe(false);
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it("preserves a loaded approval and its pending decision across advisor imports", async () => {
    const fetcher = vi.fn(
      async (url: string | URL | Request) =>
        new Response(
          JSON.stringify(
            String(url).includes("/approvals/")
              ? approvalSnapshot
              : coreBundle.recordings.warm.task,
          ),
        ),
    );
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("scrollTo", vi.fn());
    const approvalId = "advisor-navigation-approval";
    vi.spyOn(HttpRunClient.prototype, "execute").mockImplementation(
      async (_request, incoming) => {
        const result: RunResult = {
          ...warmResult,
          status: "awaiting_approval",
          output: {},
          steps: warmResult.steps.map((step) => ({
            ...step,
            status:
              step.step_id === "read_catalog" ? "awaiting_approval" : "blocked",
          })),
        };
        incoming.onRun({ ...result, status: "running", finished_at: null });
        incoming.onEvents([
          {
            schema_version: "1",
            run_id: result.run_id,
            sequence: 0,
            timestamp: result.started_at,
            event_type: "approval_required",
            step_id: "read_catalog",
            payload: { approval_id: approvalId, reason: "Review required" },
          },
        ]);
        incoming.onResult(result);
        return result;
      },
    );
    const approvalSnapshot = {
      approval_id: approvalId,
      binding: { fixture: "Exact approval snapshot" },
      binding_digest: "fixture-binding-digest",
      status: "pending",
      requested_at: warmResult.started_at,
      expires_at: warmResult.finished_at,
      reviewer_id: null,
      decided_at: null,
      reason: null,
      consumed_at: null,
    };
    render(<App />);
    await act(async () =>
      fireEvent.change(screen.getByRole("combobox", { name: "Data source" }), {
        target: { value: "live" },
      }),
    );
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Run comparison" })),
    );
    fireEvent.click(screen.getByRole("button", { name: "Exit focus view" }));
    fireEvent.click(
      within(
        screen.getByRole("region", { name: "Canopy execution" }),
      ).getByRole("button", { name: "Inspect first step" }),
    );
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Inspect approval" })),
    );
    expect(screen.getByText("Status: pending")).toBeVisible();
    openAdvisor();
    await importReport(demo);
    openExecution();
    expect(screen.getByText("Status: pending")).toBeVisible();
    expect(screen.getByText(/Exact approval snapshot/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Approve & retry" }),
    ).toBeEnabled();
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).includes("/approvals/")),
    ).toHaveLength(1);
  });
});
