import {
  parseRunEvent,
  unknownUsage,
  type Connection,
  type RunEvent,
  type RunResult,
  type RunStatus,
  type RunSummary,
  type StepResult,
  type Task,
  type Usage,
} from "../api/types";

export type StepStatus = StepResult["status"] | "pending" | "running";
export interface StepView {
  status: StepStatus;
  invalidated: boolean;
  result?: StepResult;
  reason?: string;
  approvalId?: string;
}
export interface RunState {
  task: Task;
  run: RunSummary | null;
  status: RunStatus;
  connection: Connection;
  events: RunEvent[];
  steps: Record<string, StepView>;
  result: RunResult | null;
  error: string | null;
}
export type RunAction =
  | { type: "reset"; task: Task }
  | { type: "connection"; connection: Connection }
  | { type: "run"; run: RunSummary }
  | { type: "events"; events: RunEvent[] }
  | { type: "result"; result: RunResult }
  | { type: "error"; error: string };

export function initialRun(task: Task): RunState {
  return {
    task,
    run: null,
    status: "idle",
    connection: "idle",
    events: [],
    result: null,
    error: null,
    steps: Object.fromEntries(
      task.steps.map((step) => [
        step.step_id,
        { status: "pending", invalidated: false },
      ]),
    ),
  };
}

function applyEvent(steps: RunState["steps"], event: RunEvent) {
  if (event.event_type === "evidence_invalidated") {
    const ids = event.payload.step_ids;
    if (Array.isArray(ids))
      for (const id of ids)
        if (typeof id === "string" && steps[id])
          steps[id] = { ...steps[id], invalidated: true };
  }
  const id = event.step_id;
  if (!id || !steps[id]) return;
  let step = { ...steps[id] };
  const reason = event.payload.reason;
  if (typeof reason === "string") step.reason = reason;
  switch (event.event_type) {
    case "step_dispatched":
      step.status = "running";
      break;
    case "step_reused":
      step.status = "reused";
      break;
    case "step_completed":
      step.status = "completed";
      break;
    case "step_failed":
      step.status = "failed";
      break;
    case "step_blocked":
    case "budget_refused":
      step.status = "blocked";
      break;
    case "approval_required":
      step.status = "awaiting_approval";
      if (typeof event.payload.approval_id === "string")
        step.approvalId = event.payload.approval_id;
      break;
    case "canopy.step.recorded": {
      const result = event.payload.result as StepResult | undefined;
      if (result?.step_id === id)
        step = {
          ...step,
          status: result.status,
          result,
          reason: result.reason || undefined,
        };
      break;
    }
  }
  steps[id] = step;
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "reset":
      return initialRun(action.task);
    case "connection":
      return { ...state, connection: action.connection };
    case "run":
      return { ...state, run: action.run, status: action.run.status };
    case "error":
      return { ...state, error: action.error, connection: "reconnecting" };
    case "result": {
      if (state.run && action.result.run_id !== state.run.run_id) return state;
      const steps = { ...state.steps };
      for (const result of action.result.steps)
        if (steps[result.step_id]) {
          steps[result.step_id] = {
            ...steps[result.step_id],
            result,
            status: result.status,
            reason: result.reason || undefined,
          };
        }
      return {
        ...state,
        result: action.result,
        status: action.result.status,
        steps,
        connection: "complete",
        error: null,
      };
    }
    case "events": {
      const events = new Map(
        state.events.map((event) => [
          `${event.run_id}:${event.sequence}`,
          event,
        ]),
      );
      for (const raw of action.events) {
        const event = parseRunEvent(raw, state.run?.run_id);
        events.set(`${event.run_id}:${event.sequence}`, event);
      }
      const ordered = [...events.values()].sort(
        (a, b) => a.sequence - b.sequence,
      );
      const steps = initialRun(state.task).steps;
      for (const event of ordered) applyEvent(steps, event);
      if (state.result)
        for (const result of state.result.steps)
          if (steps[result.step_id]) {
            steps[result.step_id] = {
              ...steps[result.step_id],
              result,
              status: result.status,
            };
          }
      return {
        ...state,
        events: ordered,
        steps,
        status:
          state.result?.status ?? (ordered.length ? "running" : state.status),
      };
    }
  }
}

export function currentUsage(state: RunState): Usage {
  if (state.result) return state.result.usage;
  const usages = state.events
    .filter((event) => event.event_type === "budget_settled")
    .map((event) => event.payload.usage as Usage)
    .filter(Boolean);
  if (!usages.length) return unknownUsage();
  return usages.reduce<Usage>(
    (total, usage) => ({
      input_tokens: total.input_tokens + usage.input_tokens,
      output_tokens: total.output_tokens + usage.output_tokens,
      provider_cached_input_tokens:
        total.provider_cached_input_tokens + usage.provider_cached_input_tokens,
      model_calls: total.model_calls + usage.model_calls,
      tool_calls: total.tool_calls + usage.tool_calls,
      cost_usd:
        total.cost_usd === null || usage.cost_usd === null
          ? null
          : total.cost_usd + usage.cost_usd,
      energy_j:
        total.energy_j === null || usage.energy_j === null
          ? null
          : total.energy_j + usage.energy_j,
      elapsed_ms: 0,
      elapsed_known: false,
      tokens_known: total.tokens_known && usage.tokens_known,
    }),
    { ...unknownUsage(), tokens_known: true, cost_usd: 0, energy_j: 0 },
  );
}

export function formatDuration(usage: Usage): string {
  if (!usage.elapsed_known) return "—";
  return usage.elapsed_ms < 1000
    ? `${Math.round(usage.elapsed_ms)} ms`
    : `${(usage.elapsed_ms / 1000).toFixed(2)} s`;
}
export function formatCost(value: number | null): string {
  return value === null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 4,
      }).format(value);
}
