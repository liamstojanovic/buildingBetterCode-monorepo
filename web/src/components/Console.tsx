import { projectImpact } from "../state/impact";
import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Database,
  Download,
  FileCheck2,
  FileText,
  Fingerprint,
  FlaskConical,
  GitBranch,
  Layers3,
  Leaf,
  Loader2,
  LockKeyhole,
  Play,
  Radio,
  RotateCcw,
  ShieldCheck,
  Ship,
  Sparkles,
  Square,
  Upload,
  X,
  Zap,
  Maximize2,
  Minimize2,
} from "lucide-react";
import type {
  ApprovalDetail,
  ComparisonBundle,
  Connection,
  Evaluation,
  Step,
  Task,
} from "../api/types";
import {
  currentUsage,
  formatCost,
  formatDuration,
  type RunState,
} from "../state/run";
import {
  labelFor,
  scenarioLabel,
  scenarioLabels,
  type Scenario,
} from "../fixtures/cargo";
import { DemoTour, type DemoTourStep } from "./DemoTour";
import { MockTokenCosts } from "./MockTokenCosts";
import {
  swarmPrices,
  modeledJevUsage,
  type TokenPrice,
} from "../fixtures/pricing";
import { ExecutionTree } from "./ExecutionTree";
import { ExecutionGraph, statusLabels } from "./ExecutionGraph";

export type SourceMode = "mock" | "recorded" | "live";
export interface ConsoleProps {
  activeWorkspace?: "execution" | "optimization";
  onWorkspace?: (workspace: "execution" | "optimization") => void;
  optimization?: ReactNode;
  advisorSourceLabel?: string;
  baseline: RunState;
  canopy: RunState;
  scenario: Scenario;
  sourceMode: SourceMode;
  busy: boolean;
  notice: string;
  bundle: ComparisonBundle | null;
  evaluations: Evaluation[];
  selected: { lane: "baseline" | "canopy"; stepId: string } | null;
  onSelect: (lane: "baseline" | "canopy", stepId: string) => void;
  onCloseInspector: () => void;
  onScenario: (value: Scenario) => void;
  onSourceMode: (value: SourceMode) => void;
  onRun: (scenario?: Scenario) => void;
  onReset: () => void;
  onStop: () => void;
  onImport: (file: File) => void;
  onImportTask: (file: File) => void;
  onExport: () => void;
  onLoadCore: () => void;
  onLoadLiveTask: () => void;
  approval: ApprovalDetail | null;
  approvalBusy: boolean;
  approvalError: string | null;
  onApproval: (approved: boolean) => void;
  onLoadApproval: (id: string) => void;
}

function connectionLabel(value: Connection) {
  return {
    idle: "Ready",
    connecting: "Connecting",
    sse: "SSE connected",
    polling: "Polling · 300 ms",
    reconnecting: "Reconnecting",
    recorded: "Recorded playback",
    mock: "Mock playback",
    complete: "Finished",
  }[value];
}
function Metric({
  label,
  value,
  detail,
  accent = false,
}: {
  label: string;
  value: string;
  detail: string;
  accent?: boolean;
}) {
  return (
    <div className={`metric${accent ? " metric-accent" : ""}`}>
      <span className="eyebrow">{label}</span>
      <strong>{value}</strong>
      <span>{detail}</span>
    </div>
  );
}
function Quality({
  state,
  evaluations,
}: {
  state: RunState;
  evaluations: Evaluation[];
}) {
  const evaluation = evaluations.find(
    (item) => item.run_id === state.result?.run_id,
  );
  return (
    <span
      className={`quality ${evaluation?.passed ? "quality-pass" : ""}`}
      title={evaluation?.method}
    >
      <ShieldCheck size={13} />
      {evaluation
        ? evaluation.passed
          ? `${evaluation.correct_fields}/${evaluation.checked_fields} checks passed`
          : "Quality check failed"
        : "Quality not scored"}
    </span>
  );
}

function RunLane({
  state,
  variant,
  selected,
  onSelect,
  evaluations,
  reference,
  view,
  illustrative,
  price,
}: {
  price: TokenPrice;
  reference: RunState;
  view: "workflow" | "tree";
  illustrative: boolean;
  state: RunState;
  variant: "baseline" | "canopy";
  selected?: string;
  onSelect: (id: string) => void;
  evaluations: Evaluation[];
}) {
  const rawUsage = currentUsage(state);
  const rawReference = currentUsage(reference);
  const routing = modeledJevUsage(state);
  const referenceRouting = modeledJevUsage(reference);
  const usage =
    illustrative && variant === "canopy"
      ? {
          ...rawUsage,
          input_tokens: rawUsage.input_tokens + routing.input_tokens,
          output_tokens: rawUsage.output_tokens + routing.output_tokens,
        }
      : rawUsage;
  const referenceUsage =
    illustrative && variant === "baseline"
      ? {
          ...rawReference,
          input_tokens:
            rawReference.input_tokens + referenceRouting.input_tokens,
          output_tokens:
            rawReference.output_tokens + referenceRouting.output_tokens,
        }
      : rawReference;
  const done = Object.values(state.steps).filter((step) =>
    ["completed", "reused"].includes(step.status),
  ).length;
  const reused = Object.values(state.steps).filter(
    (step) => step.status === "reused",
  ).length;
  return (
    <section
      className={`run-lane lane-${variant}`}
      aria-label={`${variant === "canopy" ? "Canopy" : "Baseline"} execution`}
    >
      <div className="lane-heading">
        <div className={`lane-symbol ${variant}`}>
          {variant === "canopy" ? <Leaf size={17} /> : <GitBranch size={17} />}
        </div>
        <div>
          <h3>{variant === "canopy" ? "With Canopy" : "Baseline"}</h3>
          <p>
            {variant === "canopy"
              ? "Validity-aware reuse"
              : "Normal agent execution"}
          </p>
        </div>
        <span className={`run-status status-${state.status}`}>
          <i />
          {state.status.replaceAll("_", " ")}
        </span>
      </div>
      <div
        data-demo-focus="metrics"
        className="lane-measurements"
        aria-label={`${variant} usage${illustrative ? " (illustrative)" : ""}`}
      >
        <div className="lane-totals">
          <strong>
            {formatDuration(usage)}
            <small>completion time</small>
          </strong>
          <strong>
            {usage.tokens_known
              ? (usage.input_tokens + usage.output_tokens).toLocaleString()
              : "—"}
            <small>
              {illustrative && variant === "canopy"
                ? "tokens incl. modeled JeV"
                : "tokens used"}
            </small>
          </strong>
          <span>{illustrative ? "ILLUSTRATIVE" : "REPORTED"}</span>
        </div>
        {(
          [
            [
              "Time",
              usage.elapsed_known ? usage.elapsed_ms : null,
              referenceUsage.elapsed_known ? referenceUsage.elapsed_ms : null,
              formatDuration(usage),
            ],
            [
              "Tokens",
              usage.tokens_known
                ? usage.input_tokens + usage.output_tokens
                : null,
              referenceUsage.tokens_known
                ? referenceUsage.input_tokens + referenceUsage.output_tokens
                : null,
              usage.tokens_known
                ? (usage.input_tokens + usage.output_tokens).toLocaleString()
                : "Unknown",
            ],
            [
              "Tool calls",
              state.status === "idle" ? null : usage.tool_calls,
              reference.status === "idle" ? null : referenceUsage.tool_calls,
              state.status === "idle" ? "—" : String(usage.tool_calls),
            ],
          ] as const
        ).map(([label, value, base, formatted]) => {
          const maximum = Math.max(base ?? 0, value ?? 0);
          return (
            <div className="comparison-measure" key={label}>
              <span>{label}</span>
              <div
                className="measure-track"
                role="meter"
                aria-label={`${variant} ${label}`}
                aria-valuemin={0}
                aria-valuemax={maximum || 1}
                aria-valuenow={value ?? undefined}
                aria-valuetext={value === null ? "Unknown" : formatted}
              >
                <i
                  style={{
                    width: `${value === null || !maximum ? 0 : (value / maximum) * 100}%`,
                  }}
                />
              </div>
              <b>{formatted}</b>
            </div>
          );
        })}
        <div className="lane-dispatches">
          {usage.model_calls} model dispatches <span>·</span> {reused} steps
          reused
        </div>
        {illustrative && (
          <MockTokenCosts
            state={state}
            baseline={variant === "baseline" ? state : reference}
            variant={variant}
            price={price}
          />
        )}
      </div>
      {view === "tree" ? (
        <ExecutionTree
          state={state}
          selectedStep={selected}
          onSelect={onSelect}
        />
      ) : (
        <ExecutionGraph
          state={state}
          selectedStep={selected}
          onSelect={onSelect}
        />
      )}
      <div className="lane-footer">
        <div className="completion-track">
          <div
            style={{ width: `${(done / state.task.steps.length) * 100}%` }}
          />
        </div>
        <span>
          {done}/{state.task.steps.length} steps
        </span>
        <Quality state={state} evaluations={evaluations} />
      </div>
    </section>
  );
}

function EvidenceTray({
  task,
  scenario,
  sourceMode,
}: {
  task: Task;
  scenario: Scenario;
  sourceMode: SourceMode;
}) {
  return (
    <section
      className="evidence-tray"
      id="evidence"
      aria-label="Source documents"
    >
      <div className="section-label">
        <Layers3 size={15} />
        <strong>Source packet</strong>
        <span>{task.evidence.length} evidence sources</span>
      </div>
      <div className="documents">
        {task.evidence.map((evidence, index) => {
          const titles = {
            invoice: "Commercial invoice",
            manifest: "Bill of lading",
            certificate: "Insurance certificate",
          };
          const title =
            titles[evidence.evidence_id as keyof typeof titles] ??
            evidence.evidence_id;
          const changed =
            scenario === "changed" &&
            (evidence.evidence_id === "certificate" ||
              (index === 0 && sourceMode !== "mock"));
          return (
            <div
              key={evidence.evidence_id}
              className={`document ${changed ? "document-changed" : ""}`}
            >
              <div className="paper-icon">
                <FileText size={20} />
                <i />
                <i />
              </div>
              <div>
                <strong>{title}</strong>
                <span>
                  {sourceMode === "mock"
                    ? "Synthetic document"
                    : evidence.source}
                </span>
              </div>
              <span className="document-version">
                {changed ? (
                  <>
                    <RotateCcw size={11} />{" "}
                  </>
                ) : null}
                v{evidence.version}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Inspector({
  state,
  step,
  onClose,
  approval,
  approvalBusy,
  approvalError,
  onApproval,
  onLoadApproval,
}: {
  state: RunState;
  step: Step;
  onClose: () => void;
  approval: ApprovalDetail | null;
  approvalBusy: boolean;
  approvalError: string | null;
  onApproval: (approved: boolean) => void;
  onLoadApproval: (id: string) => void;
}) {
  const view = state.steps[step.step_id];
  const related = state.events.filter(
    (event) => event.step_id === step.step_id,
  );
  const reused = related.find((event) => event.event_type === "step_reused");
  const sourceRun = view.result?.source_run_id ?? reused?.payload.source_run_id;
  const label = labelFor(step);
  return (
    <aside className="inspector" aria-label="Step inspector">
      <div className="inspector-heading">
        <span className="eyebrow">STEP INSPECTOR</span>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="Close inspector"
        >
          <X size={18} />
        </button>
      </div>
      <div className="inspector-icon">
        <Fingerprint size={25} />
      </div>
      <h2>{label.title}</h2>
      <p>
        {label.agent} · {step.kind}
      </p>
      <span className={`inspection-status node-${view.status}`}>
        {statusLabels[view.status]}
        {view.invalidated ? " · source updated" : ""}
      </span>
      <div className="inspector-section">
        <h4>Decision evidence</h4>
        <p>
          {view.reason ||
            (view.status === "reused"
              ? "A recorded computation result was reused. Inspect its original run and the current evidence and policy checks below."
              : "Execution state is derived from the ordered run events.")}
        </p>
      </div>
      <div className="inspector-section">
        <h4>Execution binding</h4>
        <dl>
          <dt>Handler</dt>
          <dd>{step.handler}</dd>
          <dt>Version</dt>
          <dd>{step.handler_version}</dd>
          <dt>Policy</dt>
          <dd>{state.result?.policy_version ?? "See policy_checked events"}</dd>
          <dt>Side effects</dt>
          <dd>{step.side_effect ? "Yes · never reusable" : "No"}</dd>
          <dt>Dependencies</dt>
          <dd>{step.depends_on.join(", ") || "Independent step"}</dd>
        </dl>
      </div>
      {typeof sourceRun === "string" && (
        <div className="inspector-section provenance">
          <h4>
            <Database size={13} />
            Original execution
          </h4>
          <code>{sourceRun}</code>
          <p>Historical usage is separate from this run’s new spend.</p>
        </div>
      )}
      <div className="inspector-section">
        <h4>Declared sources</h4>
        {step.evidence_ids.length ? (
          step.evidence_ids.map((id) => {
            const evidence = state.task.evidence.find(
              (item) => item.evidence_id === id,
            );
            return (
              <div className="evidence-reference" key={id}>
                <FileCheck2 size={15} />
                <div>
                  <strong>{id}</strong>
                  <small>
                    Version {evidence?.version} · {evidence?.source}
                  </small>
                </div>
              </div>
            );
          })
        ) : (
          <p>Evidence is inherited from the dependencies listed above.</p>
        )}
      </div>
      {view.approvalId && (
        <div className="inspector-section approval-panel">
          <h4>
            <LockKeyhole size={14} /> Exact-action approval
          </h4>
          <p>{view.approvalId}</p>
          <button
            className="secondary-button"
            onClick={() => onLoadApproval(view.approvalId!)}
            disabled={approvalBusy}
          >
            Inspect approval
          </button>
          {approval?.approval_id === view.approvalId && (
            <>
              <pre>{JSON.stringify(approval.binding, null, 2)}</pre>
              <p>Status: {approval.status}</p>
              <div className="button-row">
                <button
                  className="primary-button"
                  onClick={() => onApproval(true)}
                  disabled={approvalBusy || approval.status !== "pending"}
                >
                  Approve & retry
                </button>
                <button
                  className="secondary-button"
                  onClick={() => onApproval(false)}
                  disabled={approvalBusy || approval.status !== "pending"}
                >
                  Deny
                </button>
              </div>
            </>
          )}
          {approvalError && (
            <p role="alert" className="error-text">
              {approvalError}
            </p>
          )}
        </div>
      )}
      <div className="inspector-section">
        <h4>Input snapshot</h4>
        <pre>{JSON.stringify(step.inputs, null, 2)}</pre>
      </div>
      {view.result && (
        <div className="inspector-section">
          <h4>Recorded output</h4>
          <pre>{JSON.stringify(view.result.output, null, 2)}</pre>
        </div>
      )}
      <div className="inspector-section">
        <h4>
          Event trail <span>{related.length}</span>
        </h4>
        {related.map((event) => (
          <details className="event-detail" key={event.sequence}>
            <summary>
              <span>#{event.sequence}</span>
              {event.event_type}
            </summary>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
          </details>
        ))}
      </div>
    </aside>
  );
}

function Impact({
  baseline,
  canopy,
  bundle,
  sourceMode,
}: Pick<ConsoleProps, "baseline" | "canopy" | "bundle" | "sourceMode">) {
  const [volume, setVolume] = useState(100000);
  const [repeat, setRepeat] = useState(50);
  const projected =
    sourceMode === "recorded" ? projectImpact(bundle, volume, repeat) : null;
  const measured = projected !== null;
  const counts = [baseline, canopy].map((run) => currentUsage(run).model_calls);
  return (
    <section className="impact-section" id="impact">
      <div>
        <div className="eyebrow">FROM ONE RUN TO YOUR ORGANIZATION</div>
        <h2>Small repetitions. Large implications.</h2>
        <p>
          {measured
            ? "Explore projected gross provider savings using measured cold and warm runs."
            : "The mechanism is visible. Dollar savings need measured, correctly completed provider runs."}
        </p>
      </div>
      <div className="impact-controls">
        <label>
          Tasks per month
          <input
            type="number"
            min="0"
            max="1000000000"
            value={volume}
            onChange={(e) =>
              setVolume(Math.max(0, Math.min(1e9, Number(e.target.value))))
            }
          />
        </label>
        <label>
          Unchanged repeats <span>{repeat}%</span>
          <input
            type="range"
            min="0"
            max="100"
            value={repeat}
            onChange={(e) => setRepeat(Number(e.target.value))}
          />
        </label>
      </div>
      <div className="impact-result">
        <strong>
          {projected === null ? "Measurement pending" : formatCost(projected)}
        </strong>
        <span>
          {projected === null
            ? `Observed dispatches: ${counts[0]} baseline / ${counts[1]} Canopy`
            : "Projected gross provider savings / month"}
        </span>
        <small>
          Illustrative volume. Includes cold-run cost difference. Infrastructure
          costs excluded.
        </small>
      </div>
    </section>
  );
}

export function Console(props: ConsoleProps) {
  const { baseline, canopy, sourceMode, scenario, busy, selected } = props;
  const isOptimization = props.activeWorkspace === "optimization";
  const [methodology, setMethodology] = useState(false);
  const [presentation, setPresentation] = useState(false);
  const [view, setView] = useState<"workflow" | "tree">("workflow");
  const [modelId, setModelId] = useState<string>(swarmPrices[0].id);
  const mockPrice =
    swarmPrices.find((price) => price.id === modelId) ?? swarmPrices[0];
  const [tourDismissed, setTourDismissed] = useState(() => {
    try {
      return sessionStorage.getItem("canopy-demo-tour") === "seen";
    } catch {
      return false;
    }
  });
  const [tourStep, setTourStep] = useState<DemoTourStep | null>(null);
  const dismissTour = () => {
    setTourStep(null);
    setTourDismissed(true);
    try {
      sessionStorage.setItem("canopy-demo-tour", "seen");
    } catch {
      /* Storage is optional. */
    }
  };
  const guideEligible =
    sourceMode === "mock" &&
    scenario === "cold" &&
    !busy &&
    !isOptimization &&
    !presentation &&
    !methodology;
  useEffect(() => {
    if (!guideEligible || tourDismissed || tourStep !== null) return;
    const timer = window.setTimeout(() => setTourStep("workflow"), 4500);
    return () => window.clearTimeout(timer);
  }, [guideEligible, tourDismissed, tourStep]);
  useEffect(() => {
    if (sourceMode !== "mock") setTourStep(null);
  }, [sourceMode]);
  const runAgain = () => {
    if (tourStep) setTourStep("savings");
    else dismissTour();
    setPresentation(true);
    window.scrollTo({ top: 0 });
    props.onRun(
      sourceMode === "mock" &&
        scenario === "cold" &&
        baseline.status === "completed" &&
        canopy.status === "completed"
        ? "warm"
        : scenario,
    );
  };
  const usage = currentUsage(canopy);
  const baselineUsage = currentUsage(baseline);
  const modeledRouting = modeledJevUsage(canopy);
  const complete =
    baseline.status === "completed" && canopy.status === "completed";
  const reused = Object.values(canopy.steps).filter(
    (step) => step.status === "reused",
  ).length;
  const inspectedState = selected?.lane === "baseline" ? baseline : canopy;
  const inspectedStep = inspectedState.task.steps.find(
    (step) => step.step_id === selected?.stepId,
  );
  const qualityPassed = [baseline, canopy].every((state) =>
    props.evaluations.some(
      (evaluation) =>
        evaluation.run_id === state.result?.run_id && evaluation.passed,
    ),
  );
  const costSavings =
    complete &&
    qualityPassed &&
    sourceMode !== "mock" &&
    !baseline.result?.synthetic &&
    !canopy.result?.synthetic &&
    baselineUsage.cost_usd !== null &&
    usage.cost_usd !== null
      ? baselineUsage.cost_usd - usage.cost_usd
      : null;
  return (
    <div
      className={`app-shell demo-mode${sourceMode === "mock" ? " mock-priced" : ""}${presentation && !isOptimization ? " presentation" : ""}${isOptimization ? " optimization-active" : ""}`}
    >
      <aside className="rail">
        <a className="brand-mark" href="#" aria-label="Canopy home">
          <Leaf size={23} />
        </a>
        <nav className="rail-links" aria-label="Workspace navigation">
          <button
            className={`rail-link${!isOptimization ? " active" : ""}`}
            aria-current={!isOptimization ? "page" : undefined}
            onClick={() => props.onWorkspace?.("execution")}
            aria-label="Execution workspace"
            title="Execution"
          >
            <GitBranch size={20} />
          </button>
          <button
            className={`rail-link${isOptimization ? " active" : ""}`}
            aria-current={isOptimization ? "page" : undefined}
            onClick={() => props.onWorkspace?.("optimization")}
            aria-label="Optimization workspace"
            title="Optimization"
          >
            <Sparkles size={20} />
          </button>
          <a
            className="rail-link"
            href="#evidence"
            aria-label="Source evidence"
            onClick={() => props.onWorkspace?.("execution")}
          >
            <Layers3 size={20} />
          </a>
          <a
            className="rail-link"
            href="#impact"
            aria-label="Enterprise impact"
            onClick={() => props.onWorkspace?.("execution")}
          >
            <Activity size={20} />
          </a>
        </nav>
        <button
          className="rail-link rail-help"
          onClick={() => {
            props.onWorkspace?.("execution");
            setMethodology(true);
          }}
          aria-label="Methodology and demo guide"
        >
          <Code2 size={19} />
        </button>
        <span className="rail-avatar" title="Local demo workspace">
          C
        </span>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="wordmark">
            canopy<span>/</span>
            <span className="workspace-name">
              {isOptimization ? "Optimization" : "Execution studio"}
            </span>
          </div>
          <div className="topbar-right">
            <span className="event-tag">eMerge AI · Miami 2026</span>
            <span
              className={`source-badge source-${isOptimization ? "advisor" : sourceMode}`}
            >
              <i />
              {isOptimization
                ? (props.advisorSourceLabel ?? "NO ADVISOR REPORT")
                : sourceMode === "mock"
                  ? "Demo Mode"
                  : sourceMode === "recorded"
                    ? "RECORDED"
                    : "LIVE API"}
            </span>
          </div>
        </header>
        <main hidden={isOptimization} aria-label="Execution workspace content">
          <div className="hero">
            <div>
              <div className="eyebrow">
                <span className="tiny-line" /> VERIFIED WORK. REUSED.
              </div>
              <h1>
                New task. Same work.
                <br />
                <span>Why pay twice?</span>
              </h1>
              <p className="demo-preface">
                Agent swarms split a job into smaller tasks. But they often
                repeat similar—or identical—work, spending more tokens and more
                time.
              </p>
              <div className="demo-chapter">
                <span>
                  {scenario === "cold"
                    ? "01 / FIRST ENCOUNTER"
                    : "02 / THE REPEAT"}
                </span>
                <p>
                  {scenario === "cold"
                    ? complete
                      ? "Novel agent tasks execute normally, using capable but expensive large language models. This first mock run is complete: both paths did the same work."
                      : "Novel agent tasks execute normally, using capable but expensive large language models. Follow each step as both paths execute the work."
                    : scenario === "warm" || scenario === "unrelated"
                      ? "The same work returns. The baseline executes every step again. Canopy checks validity and reuses eligible results, avoiding repeat model and tool calls."
                      : "When inputs or conditions change, Canopy checks which results are still valid. Inspect the steps to see what executes and what can be reused."}
                </p>
              </div>
            </div>
            <div className="shipment-card">
              <div className="shipment-eyebrow">
                <Ship size={15} />
                {sourceMode === "mock"
                  ? "ILLUSTRATIVE CARGO WORKFLOW"
                  : "CONNECTED WORKLOAD"}
              </div>
              <div className="shipment-id">
                {sourceMode === "mock" ? "MIA–0248" : canopy.task.workflow_id}
              </div>
              <div className="shipment-route">
                <span>
                  {sourceMode === "mock" ? "Cartagena" : "Declared inputs"}
                </span>
                <span className="route-line">
                  <i />
                  <span />
                  <i />
                </span>
                <span>
                  {sourceMode === "mock" ? "PortMiami" : "Reviewed output"}
                </span>
              </div>
              <div className="shipment-foot">
                <FileText size={13} />
                {canopy.task.evidence.length} evidence sources<span>·</span>
                {canopy.task.steps.length} execution steps
                <ArrowUpRight size={14} />
              </div>
            </div>
          </div>
          <section className="control-bar" aria-label="Run controls">
            <div className="scenario-control">
              <span className="control-icon">
                <GitBranch size={18} />
              </span>
              <label>
                <span>WORKLOAD SCENARIO</span>
                <select
                  aria-label="Workload scenario"
                  value={scenario}
                  onChange={(e) => props.onScenario(e.target.value as Scenario)}
                  disabled={busy}
                >
                  {Object.entries(scenarioLabels)
                    .filter(
                      ([id]) =>
                        sourceMode === "mock" ||
                        ["cold", "warm", "changed", "unrelated"].includes(id),
                    )
                    .map(([id, label]) => (
                      <option key={id} value={id}>
                        {scenarioLabel(id as Scenario, canopy.task)}
                      </option>
                    ))}
                </select>
              </label>
              <ChevronDown size={14} />
            </div>
            <div className="control-actions">
              <select
                className="mode-select"
                aria-label="Data source"
                value={sourceMode}
                onChange={(e) =>
                  props.onSourceMode(e.target.value as SourceMode)
                }
                disabled={busy}
              >
                <option value="mock">Mock workspace</option>
                <option value="recorded">Recorded runs</option>
                <option value="live">Live API</option>
              </select>
              {sourceMode === "mock" && (
                <button
                  className="secondary-button restart-demo"
                  disabled={busy}
                  onClick={() => {
                    setTourDismissed(false);
                    setTourStep(null);
                    props.onRun("cold");
                  }}
                >
                  Restart tutorial
                </button>
              )}
              <button
                className="icon-button reset-button"
                onClick={props.onReset}
                disabled={busy}
                aria-label="Reset view"
              >
                <RotateCcw size={16} />
              </button>
              {busy ? (
                <button className="primary-button" onClick={props.onStop}>
                  <Square size={13} />
                  Stop watching
                </button>
              ) : (
                <div className="run-again-wrap">
                  <button
                    className="primary-button"
                    data-demo-focus="repeat"
                    aria-describedby={
                      tourStep === "repeat"
                        ? "demo-tour-description"
                        : undefined
                    }
                    onClick={() => {
                      if (sourceMode === "mock") runAgain();
                      else {
                        setPresentation(true);
                        props.onRun();
                      }
                    }}
                  >
                    <RotateCcw size={15} /> Run again <ArrowRight size={15} />
                  </button>
                </div>
              )}
            </div>
          </section>
          <div className={`notice notice-${sourceMode}`} role="status">
            <span>
              <FlaskConical size={13} />
              {props.notice}
            </span>
            <button onClick={() => setMethodology(true)}>
              How to read this <ArrowUpRight size={12} />
            </button>
          </div>
          <section className="metrics" aria-label="Run metrics">
            <Metric
              label="MODEL DISPATCHES"
              value={`${baselineUsage.model_calls} → ${usage.model_calls}`}
              detail={
                sourceMode === "mock" || canopy.result?.synthetic
                  ? "Synthetic handlers · no provider requests"
                  : "Baseline → Canopy · reported counts"
              }
            />
            <Metric
              label="REUSED COMPUTATIONS"
              value={`${reused} / ${canopy.task.steps.length}`}
              detail={
                scenario === "warm"
                  ? "Matching evidence, policy, and inputs"
                  : "Only eligible, verified work"
              }
              accent
            />
            <Metric
              label="CANOPY ELAPSED"
              value={formatDuration(usage)}
              detail={
                usage.elapsed_known
                  ? sourceMode === "mock"
                    ? "Illustrative mock execution time"
                    : "Server-measured execution time"
                  : "Awaiting a measured run"
              }
            />
            <Metric
              label="PROVIDER COST SAVED"
              value={formatCost(costSavings)}
              detail={
                costSavings === null
                  ? "Measurement pending"
                  : "Completed runs · reported cost difference"
              }
            />
          </section>
          {sourceMode === "mock" && complete && scenario !== "cold" && (
            <div className="repeat-result" role="status">
              <Sparkles size={18} />
              <strong>
                {reused} of {canopy.task.steps.length} steps reused.
              </strong>
              <span>
                {Math.round(
                  (1 -
                    usage.elapsed_ms / Math.max(1, baselineUsage.elapsed_ms)) *
                    100,
                )}
                % less time ·{" "}
                {(
                  baselineUsage.input_tokens +
                  baselineUsage.output_tokens -
                  usage.input_tokens -
                  usage.output_tokens -
                  modeledRouting.input_tokens -
                  modeledRouting.output_tokens
                ).toLocaleString()}{" "}
                fewer tokens in this illustrative run.
              </span>
            </div>
          )}
          <section className="execution-section" id="execution">
            <div className="section-heading">
              <div>
                <span className="eyebrow">THE WORK, SIDE BY SIDE</span>
                <h2>
                  Execution comparison{" "}
                  <span>{canopy.task.steps.length} steps</span>
                </h2>
              </div>
              <div className="graph-heading-actions">
                {sourceMode === "mock" && (
                  <button
                    className="secondary-button take-demo-tour"
                    disabled={busy}
                    onClick={() => {
                      setTourDismissed(false);
                      setTourStep("workflow");
                    }}
                  >
                    Take the tour
                  </button>
                )}
                {sourceMode === "mock" && (
                  <label className="mock-model-picker">
                    <span>MOCK SWARM MODEL</span>
                    <select
                      aria-label="Mock swarm model"
                      value={modelId}
                      onChange={(event) => setModelId(event.target.value)}
                    >
                      {swarmPrices.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <div
                  className="view-toggle"
                  role="group"
                  aria-label="Execution view"
                  data-demo-focus="view"
                >
                  <button
                    aria-pressed={view === "workflow"}
                    onClick={() => setView("workflow")}
                  >
                    <GitBranch size={14} /> Workflow
                  </button>
                  <button
                    aria-pressed={view === "tree"}
                    onClick={() => setView("tree")}
                  >
                    <Code2 size={14} /> Tree view
                  </button>
                </div>
                {presentation && (
                  <>
                    <select
                      className="mode-select"
                      aria-label="Presentation scenario"
                      value={scenario}
                      disabled={busy}
                      onChange={(event) =>
                        props.onScenario(event.target.value as Scenario)
                      }
                    >
                      {Object.entries(scenarioLabels)
                        .filter(
                          ([id]) =>
                            sourceMode === "mock" ||
                            ["cold", "warm", "changed", "unrelated"].includes(
                              id,
                            ),
                        )
                        .map(([id, label]) => (
                          <option key={id} value={id}>
                            {scenarioLabel(id as Scenario, canopy.task)}
                          </option>
                        ))}
                    </select>
                    <span className={`source-badge source-${sourceMode}`}>
                      {sourceMode.toUpperCase()}
                      {canopy.result?.synthetic ? " · SYNTHETIC" : ""}
                    </span>
                    <button
                      className="primary-button"
                      data-demo-focus="repeat"
                      onClick={busy ? props.onStop : runAgain}
                    >
                      {busy ? <Square size={12} /> : <Play size={12} />}
                      {busy ? "Stop watching" : "Run again"}
                    </button>
                  </>
                )}
                {!presentation && (
                  <div className="legend">
                    <span>
                      <i className="legend-running" />
                      Executing
                    </span>
                    <span>
                      <i className="legend-reused" />
                      Reused
                    </span>
                    <span>
                      <i className="legend-invalidated" />
                      Updated
                    </span>
                  </div>
                )}
                <button
                  className="secondary-button focus-button"
                  aria-label={presentation ? "Exit focus view" : "Focus view"}
                  onClick={() => {
                    setPresentation(!presentation);
                    window.scrollTo({ top: 0 });
                  }}
                >
                  {presentation ? (
                    <Minimize2 size={13} />
                  ) : (
                    <Maximize2 size={13} />
                  )}
                  {presentation ? "Exit focus" : "Focus view"}
                </button>
              </div>
            </div>
            <div className="comparison-grid">
              <RunLane
                state={baseline}
                reference={canopy}
                view={view}
                illustrative={sourceMode === "mock"}
                price={mockPrice}
                variant="baseline"
                selected={
                  selected?.lane === "baseline" ? selected.stepId : undefined
                }
                onSelect={(id) => props.onSelect("baseline", id)}
                evaluations={props.evaluations}
              />
              <RunLane
                state={canopy}
                reference={baseline}
                view={view}
                illustrative={sourceMode === "mock"}
                price={mockPrice}
                variant="canopy"
                selected={
                  selected?.lane === "canopy" ? selected.stepId : undefined
                }
                onSelect={(id) => props.onSelect("canopy", id)}
                evaluations={props.evaluations}
              />
            </div>
            <div className="execution-foot">
              <span>
                <Radio size={12} />
                {connectionLabel(canopy.connection)}
                <span className="separator">/</span>
                {sourceMode === "mock"
                  ? "Illustrative metrics · shared bar scales · no benchmark"
                  : "Ordered events · sequential dispatch"}
              </span>
              <span>
                <Fingerprint size={12} />
                Select any step to inspect its evidence
                <ChevronRight size={12} />
              </span>
            </div>
          </section>
          {(baseline.error || canopy.error) && (
            <div className="error-banner" role="alert">
              {baseline.error || canopy.error}
              <span>
                Execution may continue on the server. Inspect history before
                starting another run.
              </span>
            </div>
          )}
          <EvidenceTray
            task={canopy.task}
            scenario={scenario}
            sourceMode={sourceMode}
          />
          <div className="details-row">
            <div className="output-card">
              <div className="section-label">
                <FileCheck2 size={15} />
                <strong>Result & correctness</strong>
                <span>
                  {canopy.status === "completed"
                    ? "Task completed"
                    : "Waiting for execution"}
                </span>
              </div>
              {canopy.result ? (
                <>
                  <Quality state={canopy} evaluations={props.evaluations} />
                  <details>
                    <summary>
                      Inspect structured output <ChevronDown size={13} />
                    </summary>
                    <pre>{JSON.stringify(canopy.result.output, null, 2)}</pre>
                  </details>
                </>
              ) : (
                <p>
                  Completion and independent quality checks appear here after a
                  run.
                </p>
              )}
            </div>
            <div className="usage-card">
              <div className="section-label">
                <Zap size={15} />
                <strong>Usage boundary</strong>
              </div>
              <dl>
                <dt>
                  {sourceMode === "mock"
                    ? "New LLM tokens (excluding modeled JeV)"
                    : "New model tokens"}
                </dt>
                <dd>
                  {usage.tokens_known
                    ? (
                        usage.input_tokens + usage.output_tokens
                      ).toLocaleString()
                    : "Unknown"}
                </dd>
                <dt>Provider cached input tokens</dt>
                <dd>
                  {usage.tokens_known
                    ? usage.provider_cached_input_tokens.toLocaleString()
                    : "Unknown"}
                </dd>
                <dt>Measured energy</dt>
                <dd>
                  {usage.energy_j === null
                    ? "Not measured"
                    : `${usage.energy_j.toFixed(2)} J`}
                </dd>
              </dl>
            </div>
          </div>
          <Impact
            baseline={baseline}
            canopy={canopy}
            bundle={props.bundle}
            sourceMode={sourceMode}
          />
          <section className="integration-panel">
            <div>
              <span className="eyebrow">YOUR WORKSPACE, YOUR EVIDENCE</span>
              <p>Bring runtime recordings or connect the local Canopy API.</p>
            </div>
            <div className="integration-actions">
              <button
                className="secondary-button"
                onClick={props.onLoadCore}
                disabled={busy}
              >
                <Database size={14} />
                Core recordings
              </button>
              <label
                className={`secondary-button file-button${busy ? " disabled" : ""}`}
              >
                <Upload size={14} />
                Import recording
                <input
                  type="file"
                  accept="application/json,.json"
                  aria-label="Import recording"
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) props.onImport(file);
                    e.target.value = "";
                  }}
                />
              </label>
              <button
                className="secondary-button"
                onClick={props.onExport}
                disabled={busy || !baseline.result || !canopy.result}
              >
                <Download size={14} />
                Export session
              </button>
              {sourceMode === "live" && (
                <>
                  <button
                    className="secondary-button"
                    onClick={props.onLoadLiveTask}
                    disabled={busy}
                  >
                    Load core task
                  </button>
                  <label className="secondary-button file-button">
                    <Upload size={14} />
                    Import task
                    <input
                      type="file"
                      accept="application/json,.json"
                      aria-label="Import live task"
                      disabled={busy}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) props.onImportTask(file);
                        e.target.value = "";
                      }}
                    />
                  </label>
                </>
              )}
            </div>
          </section>
          <footer className="page-footer">
            <span>
              <Leaf size={12} />
              canopy<span className="footer-dot">·</span>Governed execution.
              Verifiable reuse.
            </span>
            <button onClick={() => setMethodology(true)}>
              Methodology & demo guide <ArrowUpRight size={12} />
            </button>
          </footer>
        </main>
        <main
          hidden={!isOptimization}
          className="optimization-main"
          aria-label="Optimization workspace content"
        >
          {props.optimization}
        </main>
      </div>
      {!isOptimization && selected && inspectedStep && (
        <Inspector
          state={inspectedState}
          step={inspectedStep}
          onClose={props.onCloseInspector}
          approval={props.approval}
          approvalBusy={props.approvalBusy}
          approvalError={props.approvalError}
          onApproval={props.onApproval}
          onLoadApproval={props.onLoadApproval}
        />
      )}
      {tourStep &&
        sourceMode === "mock" &&
        !isOptimization &&
        !methodology &&
        !selected &&
        !busy && (
          <DemoTour
            step={tourStep}
            onStep={setTourStep}
            onRun={runAgain}
            onClose={dismissTour}
          />
        )}
      {!isOptimization && methodology && (
        <div className="modal-backdrop" onClick={() => setMethodology(false)}>
          <section
            className="methodology-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="methodology-title"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="icon-button modal-close"
              onClick={() => setMethodology(false)}
              aria-label="Close methodology"
            >
              <X size={20} />
            </button>
            <span className="eyebrow">EVIDENCE BEFORE EXTRAPOLATION</span>
            <h2 id="methodology-title">What you’re seeing</h2>
            <p>
              <strong>Mock</strong> is a frontend-only cargo illustration. Its
              animation timing and dispatch counts are illustrative.{" "}
              <strong>Recorded</strong> plays actual exported runtime events and
              retains original metrics. <strong>Live</strong> connects to the
              configured local API.
            </p>
            <p>
              Application reuse, provider cached input, and historical replay
              are different mechanisms. A blocked or incomplete run does not
              count as successful savings. Missing measurements remain unknown.
            </p>
            <h3>Three-minute walkthrough</h3>
            <ol>
              <li>
                <strong>0:00–0:30</strong> Introduce the shipment packet and
                repeated analysis.
              </li>
              <li>
                <strong>0:30–1:35</strong> Compare baseline and warm execution,
                then verify correctness.
              </li>
              <li>
                <strong>1:35–2:10</strong> Change the certificate and inspect
                selective invalidation.
              </li>
              <li>
                <strong>2:10–3:00</strong> Inspect provenance and show measured
                enterprise impact.
              </li>
            </ol>
            <h3>Benchmark protocol</h3>
            <p>
              Five paired repetitions each for cold, unchanged, relevant-change,
              and unrelated-change cases. Alternate execution order; score
              outputs independently. Report median and observed range.
            </p>
            {props.bundle?.benchmark && (
              <div className="benchmark-table">
                {Object.entries(props.bundle.benchmark.cases).map(
                  ([name, pair]) => {
                    const range = (values: number[]) => {
                      if (
                        !values.length ||
                        values.some((value) => !Number.isFinite(value))
                      )
                        return "Not measured";
                      const sorted = [...values].sort((a, b) => a - b);
                      const center = Math.floor(sorted.length / 2);
                      const median =
                        sorted.length % 2
                          ? sorted[center]
                          : (sorted[center - 1] + sorted[center]) / 2;
                      return `${median.toFixed(1)} ms [${sorted[0].toFixed(1)}–${sorted.at(-1)!.toFixed(1)}]`;
                    };
                    return (
                      <div key={name}>
                        <strong>{name}</strong>
                        <span>Baseline {range(pair.baseline)}</span>
                        <span>Canopy {range(pair.governed)}</span>
                      </div>
                    );
                  },
                )}
                <small>
                  {props.bundle.benchmark.repetitions} repetitions. Median
                  [observed range]. Synthetic runs establish fixture behavior
                  only.
                </small>
              </div>
            )}
            <button
              className="primary-button"
              onClick={() => setMethodology(false)}
            >
              Back to workspace
              <ArrowRight size={14} />
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
