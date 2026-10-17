import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Console, type SourceMode, type Workspace } from "./components/Console";
import { HttpRunClient } from "./api/client";
import { parseBundle, RecordedRunClient } from "./api/recorded";
import { validateTask } from "./api/validation";
import { readAdvisorFile, type AdvisorReport } from "./api/advisor";
import { Optimization } from "./components/Optimization";
import { Benchmarks } from "./components/Benchmarks";
import {
  unknownUsage,
  type ApprovalDetail,
  type ComparisonBundle,
  type Recording,
  type RunClient,
  type RunObserver,
  type StartRunRequest,
  type Task,
} from "./api/types";
import {
  cargoTask,
  mockBundle,
  mockRecording,
  type Scenario,
} from "./fixtures/cargo";
import { initialRun, runReducer, type RunAction } from "./state/run";

export function normalizeTask(input: unknown): Task {
  const task = input as Task;
  if (
    !validateTask(input) ||
    task?.schema_version !== "1" ||
    typeof task.task_id !== "string" ||
    typeof task.tenant_id !== "string" ||
    typeof task.workflow_id !== "string" ||
    !Array.isArray(task.steps) ||
    !task.steps.length ||
    !task.steps.every(
      (step) =>
        typeof step.step_id === "string" &&
        typeof step.handler === "string" &&
        ["model", "tool"].includes(step.kind),
    )
  ) {
    throw new Error(
      "Choose a schema-version 1 Canopy Task JSON, such as core/examples/local_api.py --task-json.",
    );
  }
  return {
    ...task,
    metadata: task.metadata ?? {},
    evidence: (task.evidence ?? []).map((evidence) => ({
      ...evidence,
      data: evidence.data ?? {},
      quality: evidence.quality ?? 1,
      expires_at: evidence.expires_at ?? null,
      half_life_seconds: evidence.half_life_seconds ?? null,
    })),
    steps: task.steps.map((step) => ({
      ...step,
      handler_version: step.handler_version ?? "1",
      inputs: step.inputs ?? {},
      depends_on: step.depends_on ?? [],
      evidence_ids: step.evidence_ids ?? [],
      side_effect: step.side_effect ?? false,
      estimated_usage: { ...unknownUsage(), ...step.estimated_usage },
    })),
  };
}

export function scenarioTask(base: Task, scenario: Scenario): Task {
  const task = structuredClone(base);
  if (scenario === "changed" && task.evidence.length) {
    const evidence =
      task.evidence.find((item) => item.evidence_id === "certificate") ??
      task.evidence[0];
    evidence.version = `${evidence.version}-updated`;
    if (typeof evidence.data.amount_minor === "number")
      evidence.data.amount_minor += 700;
    if (typeof evidence.data.coverage_minor === "number")
      evidence.data.coverage_minor += 500000;
  }
  if (scenario === "unrelated")
    task.evidence.push({
      evidence_id: "web-unrelated",
      tenant_id: task.tenant_id,
      source: "synthetic://unrelated",
      version: "2",
      observed_at: task.evidence[0]?.observed_at ?? new Date().toISOString(),
      expires_at: null,
      half_life_seconds: null,
      quality: 1,
      data: { description: "No step consumes this synthetic evidence." },
    });
  return task;
}

function observer(dispatch: React.Dispatch<RunAction>): RunObserver {
  return {
    onConnection: (connection) => dispatch({ type: "connection", connection }),
    onRun: (run) => dispatch({ type: "run", run }),
    onEvents: (events) => dispatch({ type: "events", events }),
    onResult: (result) => dispatch({ type: "result", result }),
  };
}

function firstEncounter(mode: "baseline" | "governed") {
  const recording = mockRecording(mode, "cold");
  return runReducer(
    runReducer(initialRun(recording.task), {
      type: "events",
      events: recording.events,
    }),
    { type: "result", result: recording.result },
  );
}

export default function App() {
  const [workspace, setWorkspace] = useState<Workspace>(
    "execution",
  );
  const [advisorReport, setAdvisorReport] = useState<AdvisorReport | null>(
    null,
  );
  const [advisorOrigin, setAdvisorOrigin] = useState<"demo" | "import" | null>(
    null,
  );
  const [advisorBusy, setAdvisorBusy] = useState(false);
  const [advisorError, setAdvisorError] = useState<string | null>(null);
  const advisorEpoch = useRef(0);
  const advisorController = useRef<AbortController | null>(null);
  const [baseline, dispatchBaseline] = useReducer(runReducer, cargoTask, () =>
    firstEncounter("baseline"),
  );
  const [canopy, dispatchCanopy] = useReducer(runReducer, cargoTask, () =>
    firstEncounter("governed"),
  );
  const [sourceMode, setSourceMode] = useState<SourceMode>("mock");
  const [scenario, setScenario] = useState<Scenario>("cold");
  const [bundle, setBundle] = useState<ComparisonBundle | null>(mockBundle);
  const [liveTask, setLiveTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(
    "Frontend mock · Synthetic cargo packet. Animation timing is illustrative; no measured savings.",
  );
  const [selected, setSelected] = useState<{
    lane: "baseline" | "canopy";
    stepId: string;
  } | null>(null);
  const [approval, setApproval] = useState<ApprovalDetail | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const client = useRef(
    new HttpRunClient({
      baseUrl: import.meta.env.VITE_API_BASE_URL || "/api",
      asyncStartPath: import.meta.env.VITE_ASYNC_START_PATH || "/runs/async",
    }),
  );
  const controller = useRef<AbortController | null>(null);
  const requests = useRef<{
    baseline?: StartRunRequest;
    canopy?: StartRunRequest;
  }>({});
  const primed = useRef(false);
  const seed = useRef<Recording | null>(null);
  const requestEpoch = useRef(0);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(
    () => () => {
      advisorEpoch.current += 1;
      advisorController.current?.abort();
    },
    [],
  );
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape" && workspace === "execution")
        setSelected(null);
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [workspace]);

  const reset = useCallback((task: Task) => {
    dispatchBaseline({ type: "reset", task });
    dispatchCanopy({ type: "reset", task });
    setSelected(null);
    setApproval(null);
    setApprovalError(null);
  }, []);

  const loadBundle = useCallback(
    (data: unknown) => {
      const parsed = parseBundle(data);
      setBundle(parsed);
      setSourceMode("recorded");
      setScenario("warm");
      reset(parsed.recordings.warm.task);
      setNotice(`Recorded runtime events · ${parsed.description}`);
    },
    [reset],
  );

  const loadCore = useCallback(async () => {
    const epoch = ++requestEpoch.current;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    try {
      const response = await fetch("/recordings/core-synthetic.json", {
        signal: abort.signal,
      });
      if (!response.ok)
        throw new Error(
          "Core recordings are missing. Generate them with the fixtures:core script.",
        );
      const data = await response.json();
      if (epoch === requestEpoch.current) loadBundle(data);
    } catch (error) {
      if (!abort.signal.aborted) setNotice(String(error));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }, [loadBundle]);

  const loadLiveTask = useCallback(async () => {
    const epoch = ++requestEpoch.current;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    try {
      const response = await fetch("/recordings/core-task.json", {
        cache: "no-store",
        signal: abort.signal,
      });
      if (!response.ok)
        throw new Error(
          "Generate a fresh core-task.json or import a task from the runtime owner.",
        );
      const task = normalizeTask(await response.json());
      if (epoch !== requestEpoch.current) return;
      // A local demo namespace isolates this session from earlier cache runs.
      task.workflow_id = `${task.workflow_id}:web-${crypto.randomUUID().slice(0, 8)}`;
      setLiveTask(task);
      primed.current = false;
      seed.current = null;
      setSourceMode("live");
      setScenario("warm");
      reset(task);
      setNotice(
        "Live API · Local synthetic workload. Cold preparation runs before the warm comparison. Costs remain unknown.",
      );
    } catch (error) {
      if (!abort.signal.aborted) setNotice(String(error));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }, [reset]);

  const changeMode = (mode: SourceMode) => {
    if (mode === "recorded") {
      void loadCore();
      return;
    }
    if (mode === "live") {
      void loadLiveTask();
      return;
    }
    setSourceMode("mock");
    setBundle(mockBundle);
    setScenario("warm");
    reset(cargoTask);
    setNotice(
      "Frontend mock · Synthetic cargo packet. Animation timing is illustrative; no measured savings.",
    );
  };

  const changeScenario = (value: Scenario) => {
    setScenario(value);
    if (sourceMode === "mock") reset(mockRecording("governed", value).task);
    else if (sourceMode === "recorded") {
      const recording = bundle?.recordings[value];
      if (recording) reset(recording.task);
      else setNotice(`The imported bundle has no ${value} recording.`);
    } else if (liveTask) reset(scenarioTask(liveTask, value));
  };

  const runPair = async (nextScenario: Scenario = scenario) => {
    setScenario(nextScenario);
    const epoch = ++requestEpoch.current;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setSelected(null);
    setApprovalError(null);
    try {
      let left: RunClient;
      let right: RunClient;
      let leftTask: Task;
      let rightTask: Task;
      if (sourceMode === "mock") {
        const baselineRecording = mockRecording("baseline", nextScenario);
        const canopyRecording = mockRecording("governed", nextScenario);
        leftTask = baselineRecording.task;
        rightTask = canopyRecording.task;
        left = new RecordedRunClient(baselineRecording, 1, "mock");
        right = new RecordedRunClient(canopyRecording, 1, "mock");
      } else if (sourceMode === "recorded") {
        const leftRecording =
          bundle?.recordings[
            nextScenario === "warm" ? "baseline" : `baseline_${nextScenario}`
          ];
        const rightRecording = bundle?.recordings[nextScenario];
        if (!leftRecording || !rightRecording)
          throw new Error(
            "This scenario needs a matching baseline and governed recording. Import the complete comparison bundle.",
          );
        leftTask = leftRecording.task;
        rightTask = rightRecording.task;
        left = new RecordedRunClient(leftRecording);
        right = new RecordedRunClient(rightRecording);
      } else {
        if (!liveTask)
          throw new Error("Load or import the runtime’s task first.");
        if (nextScenario !== "cold" && !primed.current) {
          setNotice(
            "Live API · Preparing the cache with a real cold governed execution before comparison…",
          );
          const seedEvents: Recording["events"] = [];
          const task = {
            ...structuredClone(liveTask),
            task_id: crypto.randomUUID(),
          };
          const seedRequest: StartRunRequest = { task, mode: "governed" };
          requests.current.canopy = seedRequest;
          dispatchCanopy({ type: "reset", task });
          const seedObserver = observer(dispatchCanopy);
          const result = await client.current.execute(
            seedRequest,
            {
              ...seedObserver,
              onEvents(events) {
                seedEvents.push(...events);
                seedObserver.onEvents(events);
              },
            },
            abort.signal,
          );
          if (result.status !== "completed") {
            setNotice(
              `Cache preparation ${result.status}. Inspect the affected step before comparing successful runs.`,
            );
            return;
          }
          seed.current = {
            task,
            result,
            events: [
              ...new Map(
                seedEvents.map((event) => [event.sequence, event]),
              ).values(),
            ],
          };
          primed.current = true;
        }
        const task = scenarioTask(liveTask, nextScenario);
        if (nextScenario === "cold")
          task.workflow_id = `${liveTask.workflow_id}:cold-${crypto.randomUUID()}`;
        leftTask = { ...structuredClone(task), task_id: crypto.randomUUID() };
        rightTask = { ...structuredClone(task), task_id: crypto.randomUUID() };
        left = client.current;
        right = client.current;
        setNotice(
          "Live API · Events and metrics come from the server. SSE is preferred; ordered polling is the fallback.",
        );
      }
      dispatchBaseline({ type: "reset", task: leftTask });
      dispatchCanopy({ type: "reset", task: rightTask });
      const leftRequest: StartRunRequest = {
        task: leftTask,
        mode: "baseline",
        cache_enabled: false,
      };
      const rightRequest: StartRunRequest = {
        task: rightTask,
        mode: "governed",
        cache_enabled: true,
      };
      requests.current = { baseline: leftRequest, canopy: rightRequest };
      const execute = async (
        adapter: RunClient,
        request: StartRunRequest,
        dispatch: React.Dispatch<RunAction>,
      ) => {
        try {
          await adapter.execute(request, observer(dispatch), abort.signal);
        } catch (error) {
          if (!abort.signal.aborted && epoch === requestEpoch.current)
            dispatch({
              type: "error",
              error: error instanceof Error ? error.message : String(error),
            });
        }
      };
      await Promise.all([
        execute(left, leftRequest, dispatchBaseline),
        execute(right, rightRequest, dispatchCanopy),
      ]);
    } catch (error) {
      if (!abort.signal.aborted) {
        const message = error instanceof Error ? error.message : String(error);
        setNotice(message);
        dispatchCanopy({ type: "error", error: message });
      }
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  };

  const stop = () => {
    controller.current?.abort(
      new DOMException("Stopped watching", "AbortError"),
    );
    requestEpoch.current += 1;
    setBusy(false);
    setNotice(
      "Observation stopped. A live server execution may continue; no cancellation or automatic retry was sent.",
    );
  };

  const importRecording = async (file: File) => {
    const epoch = ++requestEpoch.current;
    setBusy(true);
    try {
      if (file.size > 20 * 1024 * 1024)
        throw new Error("Use a recording smaller than 20 MB.");
      const data = JSON.parse(await file.text());
      if (epoch === requestEpoch.current) loadBundle(data);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  };
  const importTask = async (file: File) => {
    const epoch = ++requestEpoch.current;
    setBusy(true);
    try {
      if (file.size > 20 * 1024 * 1024)
        throw new Error("Use a task smaller than 20 MB.");
      const raw = JSON.parse(await file.text());
      if (epoch !== requestEpoch.current) return;
      const task = normalizeTask(raw.task ?? raw);
      setLiveTask(task);
      primed.current = false;
      seed.current = null;
      setSourceMode("live");
      reset(task);
      setNotice(
        "Live API · Imported task snapshot preserved. Only server-registered handlers can execute.",
      );
    } catch (error) {
      setNotice(String(error));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  };
  const exportSession = () => {
    if (!baseline.result || !canopy.result) return;
    const recordings: ComparisonBundle["recordings"] = {
      baseline: {
        task: baseline.task,
        result: baseline.result,
        events: baseline.events,
      },
      warm: { task: canopy.task, result: canopy.result, events: canopy.events },
    };
    if (seed.current) recordings.seed = seed.current;
    const data: ComparisonBundle = {
      format: "canopy-comparison/v1",
      description:
        sourceMode === "mock"
          ? "Frontend mock. No measured performance or savings."
          : `Exported ${sourceMode} session (${scenario}). Preserve synthetic flags and unknown measurements.`,
      recordings,
      evaluations: bundle?.evaluations?.filter((evaluation) =>
        [baseline.result!.run_id, canopy.result!.run_id].includes(
          evaluation.run_id,
        ),
      ),
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `canopy-${sourceMode}-${scenario}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };
  const loadApproval = async (id: string) => {
    if (sourceMode !== "live") {
      setApprovalError(
        "Mock and recorded approvals are read-only. Connect the live API to decide an approval.",
      );
      return;
    }
    setApprovalBusy(true);
    setApprovalError(null);
    try {
      setApproval(await client.current.getApproval(id));
    } catch (error) {
      setApprovalError(String(error));
    } finally {
      setApprovalBusy(false);
    }
  };
  const decideApproval = async (approved: boolean) => {
    if (!approval || !selected || sourceMode !== "live") return;
    setApprovalBusy(true);
    setApprovalError(null);
    try {
      const decided = await client.current.decideApproval(
        approval.approval_id,
        approved,
        "Decision made through the local demo console.",
      );
      setApproval(decided);
      if (approved) {
        const lane = selected.lane;
        const request = requests.current[lane];
        if (!request)
          throw new Error(
            "Original request is unavailable. Approval cannot be applied to a regenerated task.",
          );
        const next = {
          ...request,
          approval_ids: {
            ...request.approval_ids,
            [selected.stepId]: approval.approval_id,
          },
        };
        requests.current[lane] = next;
        const dispatch =
          lane === "baseline" ? dispatchBaseline : dispatchCanopy;
        const abort = new AbortController();
        controller.current = abort;
        setBusy(true);
        dispatch({ type: "reset", task: next.task });
        const retryEvents: Recording["events"] = [];
        const retryObserver = observer(dispatch);
        const result = await client.current.execute(
          next,
          {
            ...retryObserver,
            onEvents(events) {
              retryEvents.push(...events);
              retryObserver.onEvents(events);
            },
          },
          abort.signal,
        );
        if (lane === "canopy" && result.status === "completed") {
          primed.current = true;
          seed.current = {
            task: next.task,
            result,
            events: [
              ...new Map(
                retryEvents.map((event) => [event.sequence, event]),
              ).values(),
            ],
          };
        }
      }
    } catch (error) {
      setApprovalError(String(error));
    } finally {
      setApprovalBusy(false);
      setBusy(false);
    }
  };

  const importAdvisor = async (file: File) => {
    const epoch = ++advisorEpoch.current;
    advisorController.current?.abort();
    setAdvisorBusy(true);
    setAdvisorError(null);
    try {
      const report = await readAdvisorFile(file);
      if (epoch !== advisorEpoch.current) return;
      setAdvisorReport(report);
      setAdvisorOrigin("import");
    } catch (error) {
      if (epoch === advisorEpoch.current)
        setAdvisorError(error instanceof Error ? error.message : String(error));
    } finally {
      if (epoch === advisorEpoch.current) setAdvisorBusy(false);
    }
  };

  const loadAdvisorDemo = async () => {
    const epoch = ++advisorEpoch.current;
    advisorController.current?.abort();
    const abort = new AbortController();
    advisorController.current = abort;
    setAdvisorBusy(true);
    setAdvisorError(null);
    try {
      const response = await fetch("/recordings/advisor-demo.json", {
        signal: abort.signal,
      });
      if (!response.ok)
        throw new Error(
          "The bundled advisor demo could not be loaded. Import an advisor report instead.",
        );
      const report = await readAdvisorFile(await response.blob());
      if (epoch !== advisorEpoch.current) return;
      setAdvisorReport(report);
      setAdvisorOrigin("demo");
    } catch (error) {
      if (!abort.signal.aborted && epoch === advisorEpoch.current)
        setAdvisorError(error instanceof Error ? error.message : String(error));
    } finally {
      if (epoch === advisorEpoch.current) setAdvisorBusy(false);
    }
  };

  return (
    <Console
      activeWorkspace={workspace}
      onWorkspace={setWorkspace}
      benchmarks={<Benchmarks active={workspace === "benchmarks"} />}
      advisorSourceLabel={
        advisorOrigin === "demo"
          ? "SYNTHETIC ADVISOR DEMO"
          : advisorOrigin === "import"
            ? "IMPORTED ADVISOR REPORT"
            : "NO ADVISOR REPORT"
      }
      optimization={
        <Optimization
          report={advisorReport}
          origin={advisorOrigin}
          busy={advisorBusy}
          error={advisorError}
          onImport={(file) => void importAdvisor(file)}
          onLoadDemo={() => void loadAdvisorDemo()}
        />
      }
      baseline={baseline}
      canopy={canopy}
      scenario={scenario}
      sourceMode={sourceMode}
      busy={busy}
      notice={notice}
      bundle={bundle}
      evaluations={sourceMode === "recorded" ? (bundle?.evaluations ?? []) : []}
      selected={selected}
      onSelect={(lane, stepId) => {
        setSelected({ lane, stepId });
        setApproval(null);
        setApprovalError(null);
      }}
      onCloseInspector={() => setSelected(null)}
      onScenario={changeScenario}
      onSourceMode={changeMode}
      onRun={(next) => void runPair(next)}
      onReset={() => reset(canopy.task)}
      onStop={stop}
      onImport={(file) => void importRecording(file)}
      onImportTask={(file) => void importTask(file)}
      onExport={exportSession}
      onLoadCore={() => void loadCore()}
      onLoadLiveTask={() => void loadLiveTask()}
      approval={approval}
      approvalBusy={approvalBusy}
      approvalError={approvalError}
      onApproval={(value) => void decideApproval(value)}
      onLoadApproval={(id) => void loadApproval(id)}
    />
  );
}
