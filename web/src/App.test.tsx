import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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

describe("workspace controls and task snapshots", () => {
  it("starts in visibly mocked mode with unknown savings", () => {
    render(<App />);
    expect(screen.getByText("MOCK DATA")).toBeInTheDocument();
    expect(screen.getAllByText("Measurement pending").length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getByRole("button", { name: /run comparison/i }),
    ).toBeEnabled();
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
        screen.queryByRole("button", { name: "Run comparison" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("combobox", { name: "Data source" }),
      ).toBeDisabled();
      await act(async () => {
        resolve(new Response(JSON.stringify(coreBundle), { status: 200 }));
      });
      expect(screen.getByText("RECORDED")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Run comparison" }),
      ).toBeEnabled();
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
