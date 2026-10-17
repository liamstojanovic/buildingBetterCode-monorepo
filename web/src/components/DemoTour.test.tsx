import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DemoTour } from "./DemoTour";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function rect(x: number, y: number, width = 200, height = 120) {
  return {
    x,
    y,
    left: x,
    top: y,
    width,
    height,
    right: x + width,
    bottom: y + height,
    toJSON() {},
  } as DOMRect;
}

describe("demo spotlight geometry and keyboard navigation", () => {
  it("cuts out both panels and follows a scrolled or resized layout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      setTimeout(() => callback(0), 0),
    );
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
    let top = 80;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        if (this.dataset.panel === "baseline") return rect(30, top);
        if (this.dataset.panel === "canopy") return rect(260, top);
        return rect(0, 0);
      },
    );
    render(
      <>
        <div data-demo-focus="workflow" data-panel="baseline" />
        <div data-demo-focus="workflow" data-panel="canopy" />
        <DemoTour
          step="workflow"
          onStep={vi.fn()}
          onRun={vi.fn()}
          onClose={vi.fn()}
        />
      </>,
    );
    const outlines = document.querySelectorAll(".demo-tour-outline");
    expect(outlines).toHaveLength(2);
    expect(outlines[0]).toHaveAttribute("x", "24");
    expect(outlines[0]).toHaveAttribute("y", "74");
    expect(outlines[0]).toHaveAttribute("height", "132");
    expect(document.querySelectorAll("mask rect[fill=black]")).toHaveLength(2);
    top = 140;
    await act(async () => {
      fireEvent.scroll(window);
      await vi.runAllTimersAsync();
    });
    expect(outlines[0]).toHaveAttribute("y", "134");
    top = 160;
    await act(async () => {
      fireEvent.resize(window);
      await vi.runAllTimersAsync();
    });
    expect(outlines[1]).toHaveAttribute("y", "154");
  });

  it("includes the highlighted button itself in the focus loop and supports Escape", () => {
    const close = vi.fn();
    const run = vi.fn();
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      () => rect(20, 20, 100, 30),
    );
    render(
      <>
        <button data-demo-focus="repeat">Highlighted run</button>
        <button>Background control</button>
        <DemoTour step="repeat" onStep={vi.fn()} onRun={run} onClose={close} />
      </>,
    );
    expect(screen.getByRole("dialog")).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(
      screen.getByRole("button", { name: "Highlighted run" }),
    ).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Run repeat" }));
    expect(run).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).toHaveBeenCalledTimes(1);
  });
});
