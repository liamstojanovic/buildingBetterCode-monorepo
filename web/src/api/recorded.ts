import { delay } from "./client";
import {
  parseRunEvent,
  type ComparisonBundle,
  type Recording,
  type RunClient,
  type RunObserver,
  type RunResult,
  type StartRunRequest,
} from "./types";
import { validateResult, validateTask } from "./validation";

export class RecordedRunClient implements RunClient {
  constructor(
    private recording: Recording,
    private speed = 1,
    private label: "recorded" | "mock" = "recorded",
  ) {}
  async execute(
    _request: StartRunRequest,
    observer: RunObserver,
    signal: AbortSignal,
  ): Promise<RunResult> {
    const { result } = this.recording;
    observer.onRun({ ...result, status: "running", finished_at: null });
    observer.onConnection(this.label);
    const events = [...this.recording.events].sort(
      (a, b) => a.sequence - b.sequence,
    );
    let previous = events.length ? Date.parse(events[0].timestamp) : 0;
    for (const raw of events) {
      const event = parseRunEvent(raw, result.run_id);
      const time = Date.parse(event.timestamp);
      await delay(
        Math.min(Math.max((time - previous) / this.speed, 0), 5000),
        signal,
      );
      observer.onEvents([event]);
      previous = time;
    }
    observer.onResult(result);
    return result;
  }
}

export function parseBundle(input: unknown): ComparisonBundle {
  if (!input || typeof input !== "object")
    throw new Error("Choose a Canopy comparison JSON file.");
  const bundle = input as ComparisonBundle;
  if (
    bundle.format !== "canopy-comparison/v1" ||
    !bundle.recordings ||
    typeof bundle.recordings !== "object"
  )
    throw new Error(
      "Unsupported recording format. Expected canopy-comparison/v1.",
    );
  for (const recording of Object.values(bundle.recordings)) {
    if (
      !validateTask(recording.task) ||
      !validateResult(recording.result) ||
      recording.task?.schema_version !== "1" ||
      recording.result?.schema_version !== "1" ||
      !Array.isArray(recording.events) ||
      recording.result.task_id !== recording.task.task_id ||
      recording.result.tenant_id !== recording.task.tenant_id ||
      !Array.isArray(recording.result.steps) ||
      !recording.result.usage ||
      !Array.isArray(recording.task.steps)
    )
      throw new Error("Recording has incomplete or incompatible contracts.");
    recording.events.forEach((event) =>
      parseRunEvent(event, recording.result.run_id),
    );
  }
  if (!bundle.recordings.baseline || !bundle.recordings.warm)
    throw new Error("Recording needs baseline and warm runs.");
  return bundle;
}
