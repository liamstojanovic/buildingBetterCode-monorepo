import { describe, expect, it, vi } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import taskSchema from "../../../core/schemas/Task.schema.json";
import eventSchema from "../../../core/schemas/RunEvent.schema.json";
import resultSchema from "../../../core/schemas/RunResult.schema.json";
import coreBundle from "../../public/recordings/core-synthetic.json";
import { mockBundle, mockRecording } from "../fixtures/cargo";
import { parseBundle, RecordedRunClient } from "./recorded";
import type { RunObserver } from "./types";

describe("recordings and contract compatibility", () => {
  it("validates exported actual core recordings and frontend fixtures against committed schemas", () => {
    const ajv = new Ajv2020({ strict: false });
    addFormats(ajv);
    const task = ajv.compile(taskSchema);
    const event = ajv.compile(eventSchema);
    const result = ajv.compile(resultSchema);
    for (const bundle of [parseBundle(coreBundle), mockBundle])
      for (const recording of Object.values(bundle.recordings)) {
        expect(task(recording.task), JSON.stringify(task.errors)).toBe(true);
        expect(result(recording.result), JSON.stringify(result.errors)).toBe(
          true,
        );
        for (const item of recording.events)
          expect(event(item), JSON.stringify(event.errors)).toBe(true);
      }
  });
  it("preserves original measurements when playback speed changes", async () => {
    const recording = mockRecording("governed", "warm");
    recording.result.usage.elapsed_known = true;
    recording.result.usage.elapsed_ms = 2100;
    const obs: RunObserver = {
      onRun: vi.fn(),
      onEvents: vi.fn(),
      onResult: vi.fn(),
      onConnection: vi.fn(),
    };
    const result = await new RecordedRunClient(recording, 10000).execute(
      { task: recording.task },
      obs,
      new AbortController().signal,
    );
    expect(result.usage.elapsed_ms).toBe(2100);
    expect(result.usage.cost_usd).toBeNull();
    expect(obs.onConnection).toHaveBeenCalledWith("recorded");
    expect(obs.onResult).toHaveBeenCalledWith(recording.result);
  });
  it("stops observation without reporting a successful result", async () => {
    const controller = new AbortController();
    controller.abort();
    const obs: RunObserver = {
      onRun: vi.fn(),
      onEvents: vi.fn(),
      onResult: vi.fn(),
      onConnection: vi.fn(),
    };
    const recording = mockRecording("baseline", "cold");
    await expect(
      new RecordedRunClient(recording).execute(
        { task: recording.task },
        obs,
        controller.signal,
      ),
    ).rejects.toThrow();
    expect(obs.onResult).not.toHaveBeenCalled();
  });
  it("rejects incomplete and incompatible recording bundles", () => {
    expect(() => parseBundle({ format: "other" })).toThrow();
    expect(() =>
      parseBundle({ format: "canopy-comparison/v1", recordings: {} }),
    ).toThrow();
  });
  it("includes five paired cases with independently checked successful outputs", () => {
    const bundle = parseBundle(coreBundle);
    expect(bundle.benchmark?.repetitions).toBe(5);
    for (const pair of Object.values(bundle.benchmark!.cases)) {
      expect(pair.baseline).toHaveLength(5);
      expect(pair.governed).toHaveLength(5);
    }
    expect(bundle.evaluations?.every((evaluation) => evaluation.passed)).toBe(
      true,
    );
    expect(bundle.recordings.warm.result.reused_steps).toBe(3);
    expect(bundle.recordings.changed.result.reused_steps).toBe(1);
    expect(bundle.recordings.unrelated.result.reused_steps).toBe(3);
    expect(bundle.recordings.warm.result.synthetic).toBe(true);
    expect(bundle.recordings.cold.result.usage.cost_usd).toBeNull();
    expect(bundle.recordings.warm.result.usage.cost_usd).toBe(0);
  });
});
