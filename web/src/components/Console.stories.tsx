import { useReducer } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { fn } from "storybook/test";
import { Console, type ConsoleProps } from "./Console";
import { initialRun, runReducer } from "../state/run";
import {
  cargoTask,
  mockBundle,
  mockRecording,
  type Scenario,
} from "../fixtures/cargo";
import { HttpRunClient } from "../api/client";
import type {
  Recording,
  RunDetail,
  RunObserver,
  StartRunRequest,
} from "../api/types";

function state(mode: "baseline" | "governed", scenario: Scenario) {
  const recording = mockRecording(mode, scenario);
  let result = initialRun(recording.task);
  result = runReducer(result, { type: "events", events: recording.events });
  return runReducer(result, { type: "result", result: recording.result });
}
const defaults: ConsoleProps = {
  baseline: state("baseline", "warm"),
  canopy: state("governed", "warm"),
  scenario: "warm",
  sourceMode: "mock",
  busy: false,
  notice: mockBundle.description,
  bundle: mockBundle,
  evaluations: [],
  selected: null,
  onSelect: fn(),
  onCloseInspector: fn(),
  onScenario: fn(),
  onSourceMode: fn(),
  onRun: fn(),
  onReset: fn(),
  onStop: fn(),
  onImport: fn(),
  onImportTask: fn(),
  onExport: fn(),
  onLoadCore: fn(),
  onLoadLiveTask: fn(),
  approval: null,
  approvalBusy: false,
  approvalError: null,
  onApproval: fn(),
  onLoadApproval: fn(),
};
const meta = {
  title: "Canopy/Execution console",
  component: Console,
  args: defaults,
  parameters: {
    docs: {
      description: {
        component:
          "Presentational console with explicitly synthetic fixtures. Live, mock, and recorded transports share the same event reducer.",
      },
    },
  },
} satisfies Meta<typeof Console>;
export default meta;
type Story = StoryObj<typeof meta>;
export const WarmReuse: Story = {};
export const Empty: Story = {
  args: { baseline: initialRun(cargoTask), canopy: initialRun(cargoTask) },
};
export const SourceChanged: Story = {
  args: { scenario: "changed", canopy: state("governed", "changed") },
};
export const UnrelatedChange: Story = {
  args: { scenario: "unrelated", canopy: state("governed", "unrelated") },
};
export const ApprovalRequired: Story = {
  args: {
    scenario: "approval",
    canopy: state("governed", "approval"),
    selected: { lane: "canopy", stepId: "extract_certificate" },
  },
};
export const BudgetRefusal: Story = {
  args: { scenario: "refused", canopy: state("governed", "refused") },
};
export const HandlerFailure: Story = {
  args: { scenario: "failed", canopy: state("governed", "failed") },
};
export const Reconnecting: Story = {
  args: {
    canopy: {
      ...initialRun(cargoTask),
      connection: "reconnecting",
      error: "Connection interrupted; execution status is unknown.",
    },
  },
};
export const Running: Story = {
  args: {
    busy: true,
    canopy: runReducer(initialRun(cargoTask), {
      type: "events",
      events: mockRecording("baseline", "cold").events.slice(0, 3),
    }),
  },
};
export const RecordedPlayback: Story = {
  args: {
    sourceMode: "recorded",
    notice:
      "Recorded synthetic fixture. Playback timing is independent of measured execution time.",
  },
};

const runs = new Map<string, Recording>();
const handlers = [
  http.post("/api/runs/async", () =>
    HttpResponse.json({ detail: "Unsupported" }, { status: 404 }),
  ),
  http.post("/api/runs", async ({ request }) => {
    const body = (await request.json()) as StartRunRequest;
    const recording = mockRecording(
      body.mode === "baseline" ? "baseline" : "governed",
      "warm",
    );
    const runId = crypto.randomUUID();
    recording.task = body.task;
    recording.result = {
      ...recording.result,
      run_id: runId,
      task_id: body.task.task_id,
    };
    recording.events = recording.events.map((event) => ({
      ...event,
      run_id: runId,
    }));
    runs.set(runId, recording);
    await delay(500);
    return HttpResponse.json(recording.result);
  }),
  http.get("/api/runs", () =>
    HttpResponse.json(
      [...runs.values()].map(({ result }) => ({
        ...result,
        status: "running",
      })),
    ),
  ),
  http.get("/api/runs/:id/events", ({ params, request }) => {
    const after = Number(
      new URL(request.url).searchParams.get("after_sequence") ?? -1,
    );
    return HttpResponse.json(
      runs
        .get(String(params.id))
        ?.events.filter((event) => event.sequence > after) ?? [],
    );
  }),
  http.get("/api/runs/:id", ({ params }) => {
    const recording = runs.get(String(params.id));
    return recording
      ? HttpResponse.json({
          ...recording.result,
          task: recording.task,
          steps: recording.result.steps,
          result: recording.result,
        } satisfies RunDetail)
      : HttpResponse.json({}, { status: 404 });
  }),
];
function MockHttpConsole() {
  const [baseline, dispatchBaseline] = useReducer(
    runReducer,
    cargoTask,
    initialRun,
  );
  const [canopy, dispatchCanopy] = useReducer(
    runReducer,
    cargoTask,
    initialRun,
  );
  const run = () => {
    runs.clear();
    const execute = (
      mode: "baseline" | "governed",
      dispatch: typeof dispatchBaseline,
    ) => {
      dispatch({ type: "reset", task: cargoTask });
      const observer: RunObserver = {
        onConnection: (connection) =>
          dispatch({ type: "connection", connection }),
        onRun: (value) => dispatch({ type: "run", run: value }),
        onEvents: (events) => dispatch({ type: "events", events }),
        onResult: (result) => dispatch({ type: "result", result }),
      };
      return new HttpRunClient().execute(
        { task: { ...cargoTask, task_id: crypto.randomUUID() }, mode },
        observer,
        new AbortController().signal,
      );
    };
    void Promise.all([
      execute("baseline", dispatchBaseline),
      execute("governed", dispatchCanopy),
    ]);
  };
  return (
    <Console
      {...defaults}
      baseline={baseline}
      canopy={canopy}
      onRun={run}
      notice="MSW mocked HTTP integration · No runtime or provider execution. Click Run comparison to exercise the real HTTP adapter."
    />
  );
}
export const MockedHttpIntegration: Story = {
  render: () => <MockHttpConsole />,
  parameters: { msw: { handlers } },
};
