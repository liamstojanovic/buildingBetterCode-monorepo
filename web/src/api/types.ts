import type { Task as WireTask, Step as WireStep } from "./generated/Task";
import type { RunEvent as WireEvent } from "./generated/RunEvent";
import type {
  RunResult as WireResult,
  StepResult as WireStepResult,
  Usage as WireUsage,
} from "./generated/RunResult";

export type Usage = Required<WireUsage>;
export type Evidence = Required<NonNullable<WireTask["evidence"]>[number]>;
export type Step = Omit<Required<WireStep>, "estimated_usage"> & {
  estimated_usage: Usage;
};
export type Task = Omit<Required<WireTask>, "steps" | "evidence"> & {
  steps: Step[];
  evidence: Evidence[];
};
export type RunEvent = Required<WireEvent>;
export type StepResult = Omit<Required<WireStepResult>, "usage"> & {
  usage: Usage;
};
export type RunResult = Omit<Required<WireResult>, "steps" | "usage"> & {
  steps: StepResult[];
  usage: Usage;
};
export type Mode = RunResult["mode"];
export type RunStatus = RunResult["status"] | "idle" | "running";
export type Connection =
  | "idle"
  | "connecting"
  | "sse"
  | "polling"
  | "reconnecting"
  | "recorded"
  | "mock"
  | "complete";

export interface RunSummary {
  run_id: string;
  task_id: string;
  tenant_id: string;
  mode: Mode;
  status: Exclude<RunStatus, "idle">;
  started_at: string;
  finished_at: string | null;
}
export interface RunDetail extends RunSummary {
  task: Task;
  result: RunResult | null;
  steps: StepResult[];
}
export interface StartRunRequest {
  task: Task;
  mode?: Mode;
  cache_enabled?: boolean;
  replay_run_id?: string;
  approval_ids?: Record<string, string>;
}
export interface ApprovalDetail {
  approval_id: string;
  binding: Record<string, unknown>;
  binding_digest: string;
  status: "pending" | "approved" | "denied";
  requested_at: string;
  expires_at: string;
  reviewer_id: string | null;
  decided_at: string | null;
  reason: string | null;
  consumed_at: string | null;
}
export interface RunObserver {
  onConnection: (connection: Connection) => void;
  onRun: (run: RunSummary) => void;
  onEvents: (events: RunEvent[]) => void;
  onResult: (result: RunResult) => void;
}
export interface RunClient {
  execute: (
    request: StartRunRequest,
    observer: RunObserver,
    signal: AbortSignal,
  ) => Promise<RunResult>;
  getApproval?: (id: string) => Promise<ApprovalDetail>;
  decideApproval?: (
    id: string,
    approved: boolean,
    reason?: string,
  ) => Promise<ApprovalDetail>;
}
export interface Recording {
  task: Task;
  result: RunResult;
  events: RunEvent[];
}
export interface Evaluation {
  run_id: string;
  passed: boolean;
  checked_fields: number;
  correct_fields: number;
  method: string;
}
export interface ComparisonBundle {
  format: "canopy-comparison/v1";
  description: string;
  recordings: Record<string, Recording>;
  evaluations?: Evaluation[];
  benchmark?: {
    repetitions: number;
    cases: Record<
      string,
      { baseline: number[]; governed: number[]; trials?: BenchmarkTrial[] }
    >;
  };
}

export interface BenchmarkTrial {
  baseline_run_id: string;
  governed_run_id: string;
  baseline_cost_usd: number | null;
  governed_cost_usd: number | null;
  completed: boolean;
  synthetic: boolean;
}

export function unknownUsage(): Usage {
  return {
    input_tokens: 0,
    output_tokens: 0,
    provider_cached_input_tokens: 0,
    model_calls: 0,
    tool_calls: 0,
    cost_usd: null,
    energy_j: null,
    elapsed_ms: 0,
    tokens_known: false,
    elapsed_known: false,
  };
}

export function parseRunEvent(value: unknown, runId?: string): RunEvent {
  if (!value || typeof value !== "object") throw new Error("Invalid run event");
  const event = value as Partial<RunEvent>;
  if (
    event.schema_version !== "1" ||
    typeof event.run_id !== "string" ||
    !Number.isInteger(event.sequence) ||
    (event.sequence ?? -1) < 0 ||
    typeof event.event_type !== "string" ||
    typeof event.timestamp !== "string" ||
    !Number.isFinite(Date.parse(event.timestamp)) ||
    (runId && event.run_id !== runId) ||
    !event.payload ||
    typeof event.payload !== "object" ||
    Array.isArray(event.payload)
  ) {
    throw new Error("Unsupported or malformed run event");
  }
  return event as RunEvent;
}

export function isTerminalEvent(event: RunEvent): boolean {
  return event.event_type === "canopy.run.finished";
}
