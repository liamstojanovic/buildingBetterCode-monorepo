import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Handle,
  Position,
  MarkerType,
  type Node,
  type NodeProps,
  type Edge,
  type ReactFlowInstance,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import {
  Bot,
  Check,
  Circle,
  Database,
  FileCheck2,
  Loader2,
  LockKeyhole,
  ShieldAlert,
  X,
} from "lucide-react";
import { labelFor } from "../fixtures/cargo";
import type { Step } from "../api/types";
import type { RunState, StepView } from "../state/run";

export const statusLabels: Record<StepView["status"], string> = {
  pending: "Queued",
  running: "Executing",
  completed: "Completed",
  reused: "Reused",
  blocked: "Refused",
  awaiting_approval: "Approval required",
  failed: "Failed",
};
type ExecutionNode = Node<
  { step: Step; view: StepView; selectedStep: boolean; onInspect: () => void },
  "execution"
>;

const StepNode = memo(function StepNode({ data }: NodeProps<ExecutionNode>) {
  const { step, view } = data;
  const label = labelFor(step);
  const Icon =
    view.status === "reused"
      ? Database
      : view.status === "completed"
        ? Check
        : view.status === "running"
          ? Loader2
          : view.status === "awaiting_approval"
            ? LockKeyhole
            : view.status === "failed"
              ? X
              : view.status === "blocked"
                ? ShieldAlert
                : step.kind === "model"
                  ? Bot
                  : FileCheck2;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(event) => {
        event.stopPropagation();
        data.onInspect();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          event.stopPropagation();
          data.onInspect();
        }
      }}
      className={`execution-node node-${view.status}${data.selectedStep ? " is-selected" : ""}${view.invalidated ? " was-invalidated" : ""}`}
    >
      <Handle type="target" position={Position.Top} />
      <div className="node-title">
        <span className="node-icon">
          <Icon size={14} className={view.status === "running" ? "spin" : ""} />
        </span>
        <strong>{label.title}</strong>
      </div>
      <div className="node-subtitle">{label.subtitle}</div>
      <div className="node-state">
        <Circle size={6} fill="currentColor" />
        {statusLabels[view.status]}
        {view.invalidated && <span className="changed-label">updated</span>}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});
const nodeTypes = { execution: StepNode };

export function ExecutionGraph({
  state,
  selectedStep,
  onSelect,
}: {
  state: RunState;
  selectedStep?: string;
  onSelect: (stepId: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<ReactFlowInstance<ExecutionNode, Edge> | null>(null);
  const layout = useMemo(() => {
    const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
    graph.setGraph({
      rankdir: "TB",
      nodesep: 22,
      ranksep: 42,
      marginx: 24,
      marginy: 20,
    });
    for (const step of state.task.steps)
      graph.setNode(step.step_id, { width: 170, height: 82 });
    for (const step of state.task.steps)
      for (const dependency of step.depends_on)
        graph.setEdge(dependency, step.step_id);
    dagre.layout(graph);
    return Object.fromEntries(
      state.task.steps.map((step) => {
        const node = graph.node(step.step_id);
        return [step.step_id, { x: node.x - 85, y: node.y - 41 }];
      }),
    );
  }, [state.task.steps]);
  const fitContainer = useCallback(() => {
    const element = container.current;
    if (
      !element ||
      !instance.current ||
      !element.clientHeight ||
      !element.clientWidth
    )
      return;
    const positions = Object.values(layout);
    if (!positions.length) return;
    const minX = Math.min(...positions.map((point) => point.x));
    const minY = Math.min(...positions.map((point) => point.y));
    const width = Math.max(...positions.map((point) => point.x)) + 170 - minX;
    const height = Math.max(...positions.map((point) => point.y)) + 82 - minY;
    const zoom = Math.max(
      0.1,
      Math.min(
        1.2,
        (element.clientWidth - 32) / width,
        (element.clientHeight - 32) / height,
      ),
    );
    void instance.current.setViewport({
      x: (element.clientWidth - width * zoom) / 2 - minX * zoom,
      y: (element.clientHeight - height * zoom) / 2 - minY * zoom,
      zoom,
    });
  }, [layout]);
  useEffect(() => {
    let frame = 0;
    const fit = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(fitContainer);
    };
    const observer = new ResizeObserver(fit);
    if (container.current) observer.observe(container.current);
    fit();
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [fitContainer]);
  const nodes: ExecutionNode[] = state.task.steps.map((step) => ({
    id: step.step_id,
    type: "execution",
    position: layout[step.step_id],
    width: 170,
    height: 82,
    handles: [
      {
        type: "target",
        position: Position.Top,
        x: 82.5,
        y: -2.5,
        width: 5,
        height: 5,
      },
      {
        type: "source",
        position: Position.Bottom,
        x: 82.5,
        y: 79.5,
        width: 5,
        height: 5,
      },
    ],
    data: {
      step,
      view: state.steps[step.step_id],
      selectedStep: selectedStep === step.step_id,
      onInspect: () => onSelect(step.step_id),
    },
  }));
  const edges: Edge[] = state.task.steps.flatMap((step) =>
    step.depends_on.map((dependency) => ({
      id: `${dependency}-${step.step_id}`,
      source: dependency,
      target: step.step_id,
      type: "smoothstep",
      animated: state.steps[step.step_id].status === "running",
      className:
        state.steps[step.step_id].status === "reused" ? "edge-reused" : "",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 12,
        height: 12,
        color: "var(--line)",
      },
    })),
  );
  return (
    <div
      className="execution-canvas"
      ref={container}
      data-demo-focus="workflow"
    >
      <ReactFlow
        nodes={nodes}
        onInit={(value) => {
          instance.current = value;
          fitContainer();
        }}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.13 }}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        minZoom={0.1}
        maxZoom={1.2}
        panOnDrag
        zoomOnDoubleClick={false}
        onNodeClick={(_, node) => onSelect(node.id)}
        proOptions={{ hideAttribution: false }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="var(--line)"
        />
      </ReactFlow>
    </div>
  );
}
