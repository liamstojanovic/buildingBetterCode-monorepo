import { describe, expect, it } from "vitest";
import { cargoTask, mockRecording } from "../fixtures/cargo";
import { currentUsage, initialRun, runReducer } from "./run";

describe("event-driven execution state", () => {
  it("deduplicates events and reconstructs state in sequence order", () => {
    const recording = mockRecording("governed", "changed");
    let state = initialRun(recording.task);
    state = runReducer(state, {
      type: "events",
      events: [...recording.events].reverse(),
    });
    state = runReducer(state, { type: "events", events: recording.events });
    expect(state.events).toHaveLength(recording.events.length);
    expect(state.steps.extract_invoice.status).toBe("reused");
    expect(state.steps.extract_certificate.invalidated).toBe(true);
    expect(state.steps.extract_invoice.invalidated).toBe(false);
    expect(currentUsage(state).model_calls).toBe(2);
  });
  it("uses final reported measurements, retaining unknown tokens and cost", () => {
    const recording = mockRecording("baseline", "cold");
    const result = {
      ...recording.result,
      usage: {
        ...recording.result.usage,
        tokens_known: false,
        elapsed_known: true,
        elapsed_ms: 42,
      },
    };
    const state = runReducer(initialRun(cargoTask), { type: "result", result });
    expect(currentUsage(state).tokens_known).toBe(false);
    expect(currentUsage(state).cost_usd).toBeNull();
    expect(currentUsage(state).elapsed_ms).toBe(42);
    expect(state.status).toBe("completed");
  });
  it("keeps a terminal result authoritative when delayed events arrive", () => {
    const recording = mockRecording("governed", "warm");
    let state = runReducer(initialRun(cargoTask), {
      type: "result",
      result: recording.result,
    });
    state = runReducer(state, {
      type: "events",
      events: recording.events.slice(0, 2),
    });
    expect(state.status).toBe("completed");
    expect(state.steps.extract_invoice.status).toBe("reused");
  });
  it("rejects foreign run events and unsupported schema versions", () => {
    const recording = mockRecording("baseline", "cold");
    const state = runReducer(initialRun(cargoTask), {
      type: "run",
      run: { ...recording.result, status: "running" },
    });
    expect(() =>
      runReducer(state, {
        type: "events",
        events: [{ ...recording.events[0], run_id: "foreign" }],
      }),
    ).toThrow();
    expect(() =>
      runReducer(initialRun(cargoTask), {
        type: "events",
        events: [{ ...recording.events[0], schema_version: "2" as "1" }],
      }),
    ).toThrow();
  });
  it.each(["approval", "refused", "failed"] as const)(
    "preserves %s without claiming completion",
    (scenario) => {
      const recording = mockRecording("governed", scenario);
      const state = runReducer(initialRun(cargoTask), {
        type: "events",
        events: recording.events,
      });
      expect(state.steps.extract_certificate.status).toBe(
        scenario === "approval"
          ? "awaiting_approval"
          : scenario === "refused"
            ? "blocked"
            : "failed",
      );
    },
  );
  it("records unknown event types without inferring a successful step", () => {
    const recording = mockRecording("baseline", "cold");
    const state = runReducer(initialRun(cargoTask), {
      type: "events",
      events: [
        {
          ...recording.events[0],
          step_id: "extract_invoice",
          event_type: "future_feature",
        },
      ],
    });
    expect(state.steps.extract_invoice.status).toBe("pending");
    expect(state.events).toHaveLength(1);
  });
});
