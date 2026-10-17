import type { Step } from "../api/types";
import type { RunState } from "../state/run";
import { labelFor } from "../fixtures/cargo";
import { statusLabels } from "./ExecutionGraph";

/** Show each DAG node once; additional dependencies remain explicit cross-links. */
export function ExecutionTree({
  state,
  selectedStep,
  onSelect,
}: {
  state: RunState;
  selectedStep?: string;
  onSelect: (id: string) => void;
}) {
  const steps = state.task.steps;
  const ids = new Set(steps.map((step) => step.step_id));
  const visited = new Set<string>();
  const parent = (step: Step) => step.depends_on.find((id) => ids.has(id));
  function branch(step: Step): React.ReactNode {
    if (visited.has(step.step_id)) return null;
    visited.add(step.step_id);
    const status = state.steps[step.step_id].status;
    const extra = step.depends_on.filter((id) => id !== parent(step));
    const children = steps.filter(
      (child) => parent(child) === step.step_id && !visited.has(child.step_id),
    );
    return (
      <li key={step.step_id}>
        <button
          className={`tree-task tree-${status}${selectedStep === step.step_id ? " tree-selected" : ""}`}
          onClick={() => onSelect(step.step_id)}
        >
          <span className="tree-task-top">
            <span>
              {step.kind === "model" ? "λ" : "$"} {step.step_id}
            </span>
            <b>{status === "reused" ? "CACHE HIT" : statusLabels[status]}</b>
          </span>
          <span className="tree-task-label">
            {labelFor(step).title} <span>/{step.kind}</span>
          </span>
          {extra.length > 0 && (
            <span className="tree-dependencies">
              ↳ also depends on {extra.join(", ")}
            </span>
          )}
        </button>
        {children.length > 0 && <ul>{children.map(branch)}</ul>}
      </li>
    );
  }
  const roots = steps.filter((step) => !parent(step)).map(branch);
  // Malformed or cyclic imported graphs still expose all steps for inspection.
  const remaining = steps
    .filter((step) => !visited.has(step.step_id))
    .map(branch);
  return (
    <div
      className="execution-tree"
      aria-label="Task dependency tree"
      data-demo-focus="workflow"
    >
      <div className="terminal-heading">
        <span>
          <i />
          <i />
          <i />
        </span>
        CANOPY://EXECUTION_TRACE <span className="terminal-cursor">▌</span>
      </div>
      <div className="tree-root">
        ~/ {state.task.workflow_id}
        <span>{state.status}</span>
      </div>
      <ul className="tree-branches">
        {roots}
        {remaining}
      </ul>
      <div className="terminal-foot">
        {steps.length} nodes · dependency tree · select a task to inspect
      </div>
    </div>
  );
}
