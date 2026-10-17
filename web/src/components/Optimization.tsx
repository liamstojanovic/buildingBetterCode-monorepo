import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Copy,
  FileSearch,
  FlaskConical,
  Upload,
} from "lucide-react";
import "./optimization.css";
import {
  categoryLabels,
  compatibilityLabels,
  statusLabels,
  strategyLabels,
  type AdvisorRecommendation,
  type AdvisorReport,
} from "../api/advisor";

export interface OptimizationProps {
  report: AdvisorReport | null;
  origin: "demo" | "import" | null;
  busy: boolean;
  error: string | null;
  onImport: (file: File) => void;
  onLoadDemo: () => void;
}

function number(value: number | null | undefined, suffix = "") {
  return value == null
    ? "Unknown"
    : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
}

function Facts({ items }: { items: [string, string | number][] }) {
  return (
    <dl className="advisor-facts">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function TextList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="advisor-detail-section">
      <h3>{title}</h3>
      {items.length ? (
        <ul>
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="advisor-muted">None reported.</p>
      )}
    </section>
  );
}

function CopyReference({ value }: { value: string }) {
  const [message, setMessage] = useState("");
  return (
    <div className="advisor-reference">
      <code>{value}</code>
      <button
        className="icon-button"
        aria-label={`Copy reference ${value}`}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setMessage("Copied");
          } catch {
            setMessage("Select the reference text to copy.");
          }
        }}
      >
        <Copy size={14} />
      </button>
      <span className="advisor-copy-status" role="status">
        {message}
      </span>
    </div>
  );
}

function RecommendationDetail({ item }: { item: AdvisorRecommendation }) {
  const finding = item.finding;
  const observed = item.observed;
  return (
    <section
      className="advisor-detail advisor-panel"
      aria-label="Recommendation details"
    >
      <div className="advisor-detail-heading">
        <span className="eyebrow">
          {categoryLabels[finding.category ?? "unknown"]}
        </span>
        <h2>{item.step_id}</h2>
        <p>{strategyLabels[finding.strategy]}</p>
        <span className={`advisor-status advisor-status-${finding.status}`}>
          {statusLabels[finding.status]}
        </span>
      </div>
      <div className="advisor-evaluation">
        <strong>Not evaluated</strong>
        <p>
          Structural findings identify work to investigate. They do not
          establish replacement quality or savings.
        </p>
        <Facts
          items={[
            ["Measured quality", "Unknown"],
            ["Estimated savings", "Unknown"],
          ]}
        />
      </div>
      <section className="advisor-detail-section">
        <h3>Compatibility and retained work</h3>
        <p>
          {
            compatibilityLabels[
              finding.native_compatibility ?? "requires_mapping"
            ]
          }
        </p>
        {finding.native_compatibility === "native_request_shape" && (
          <p className="advisor-muted">
            The recorded request matches the native shape. Successful OpenJEV
            inference has not been established by this finding.
          </p>
        )}
        <p>
          {finding.partial_substitution
            ? "Partial substitution: argument generation, explanation, or other retained work must be accounted for as described below."
            : "No partial substitution reported. Required changes and evaluation still apply."}
        </p>
      </section>
      <TextList title="Choices" items={finding.choices ?? []} />
      <TextList title="Reasons" items={finding.reasons ?? []} />
      <TextList title="Signals" items={finding.signals ?? []} />
      <TextList title="Blockers" items={finding.blockers ?? []} />
      <TextList
        title="Required changes"
        items={finding.required_changes ?? []}
      />
      <section className="advisor-detail-section">
        <h3>Observed work</h3>
        <p className="advisor-muted">
          Recorded work includes fixture and failed dispatches where present. It
          is not prospective savings.
        </p>
        <Facts
          items={[
            ["Observations", observed.observations ?? 0],
            ["Synthetic observations", observed.synthetic_observations ?? 0],
            ["Completed", observed.completed ?? 0],
            ["Failed", observed.failed ?? 0],
            ["Blocked", observed.blocked ?? 0],
            ["Awaiting approval", observed.awaiting_approval ?? 0],
            ["Reused", observed.reused ?? 0],
            ["Model dispatches", observed.model_calls ?? 0],
            ["Input tokens", number(observed.input_tokens)],
            ["Output tokens", number(observed.output_tokens)],
            ["Recorded cost (USD)", number(observed.cost_usd)],
            ["Recorded elapsed", number(observed.elapsed_ms, " ms")],
            [
              "Unknown token observations",
              observed.unknown_token_observations ?? 0,
            ],
            [
              "Unknown cost observations",
              observed.unknown_cost_observations ?? 0,
            ],
            [
              "Unknown elapsed observations",
              observed.unknown_elapsed_observations ?? 0,
            ],
          ]}
        />
      </section>
      <details className="advisor-detail-section">
        <summary>Identity and revisions</summary>
        <Facts
          items={[
            ["Workflow", item.workflow_id],
            ["Step", item.step_id],
            ["Handler", item.handler],
            ["Handler version", item.handler_version],
            ["Model fingerprint", item.model_fingerprint],
            ["Prompt version", item.prompt_version],
            ["Grouping basis", item.grouping_basis],
            ["Recommendation ID", item.recommendation_id],
            ["Group fingerprint", item.group_fingerprint],
          ]}
        />
      </details>
      <section className="advisor-detail-section">
        <h3>Source samples</h3>
        <p className="advisor-muted">
          Run and step references belong to the source recordings. This import
          does not add them to the connected service.
        </p>
        {item.sample_limit_reached && (
          <p className="advisor-warning">
            Sample limit reached. Retained samples do not cover every
            observation.
          </p>
        )}
        {!item.samples.length && <p>No samples retained.</p>}
        {item.samples.map((sample, index) => (
          <div
            className="advisor-sample"
            key={`${sample.run_id}:${sample.step_id}:${index}`}
          >
            <CopyReference value={`${sample.run_id} / ${sample.step_id}`} />
            <p>
              {sample.mode} · {sample.status} ·{" "}
              {sample.synthetic ? "Synthetic" : "Not marked synthetic"}
              {sample.selected_choice != null && (
                <> · Choice: {sample.selected_choice}</>
              )}
            </p>
          </div>
        ))}
      </section>
    </section>
  );
}

export function Optimization({
  report,
  origin,
  busy,
  error,
  onImport,
  onLoadDemo,
}: OptimizationProps) {
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [selected, setSelected] = useState<{ id: string } | null>(null);
  const detailsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setCategory("all");
    setStatus("all");
    setSelected(null);
  }, [report]);
  useEffect(() => {
    if (selected) {
      detailsRef.current?.scrollIntoView?.({
        block: "start",
        behavior: "instant",
      });
      detailsRef.current?.focus({ preventScroll: true });
    }
  }, [selected]);
  const recommendations = report?.recommendations ?? [];
  const filtered = recommendations.filter(
    (item) =>
      (category === "all" ||
        (item.finding.category ?? "unknown") === category) &&
      (status === "all" || item.finding.status === status),
  );
  const selectedItem =
    filtered.find((item) => item.recommendation_id === selected?.id) ??
    filtered[0];
  const observations = recommendations.reduce(
    (sum, item) => sum + (item.observed.observations ?? 0),
    0,
  );
  const synthetic = recommendations.reduce(
    (sum, item) => sum + (item.observed.synthetic_observations ?? 0),
    0,
  );
  const analysis = report?.analysis;
  const limitReasons = analysis?.limit_reasons ?? [];

  return (
    <div className="optimization-workspace">
      <div className="advisor-hero">
        <div>
          <span className="eyebrow">OFFLINE WORKFLOW ADVISOR</span>
          <h1>Optimization opportunities</h1>
          <p>
            Inspect decisions that could merit OpenJEV evaluation, along with
            mapping gaps and work to retain.
          </p>
        </div>
        <div className="advisor-actions">
          <button
            className="secondary-button"
            disabled={busy}
            onClick={onLoadDemo}
          >
            <FlaskConical size={15} />
            Load advisor demo
          </button>
          <label
            className={`primary-button file-button${busy ? " disabled" : ""}`}
          >
            <Upload size={15} />
            Import advisor report
            <input
              type="file"
              accept="application/json,.json"
              aria-label="Import advisor report"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onImport(file);
                event.target.value = "";
              }}
            />
          </label>
        </div>
      </div>
      <div className="advisor-import-status" aria-live="polite">
        {busy
          ? "Loading advisor report…"
          : report
            ? `${origin === "demo" ? "Bundled synthetic demo" : "Imported report"} · Analysis snapshot`
            : "No report loaded"}
      </div>
      {error && (
        <p className="advisor-error" role="alert">
          {error}
        </p>
      )}
      {!report ? (
        <section className="advisor-empty advisor-panel">
          <FileSearch size={34} />
          <h2>Bring an advisor report</h2>
          <p>
            Import an <code>advisor-report.json</code> from the offline advisor,
            or load the bundled seven-group synthetic demo.
          </p>
          <p>
            Use an AdvisorReport up to 20 MiB. Tasks, comparison recordings, and
            demo manifests use different formats.
          </p>
          <p className="advisor-muted">
            Reports describe structural findings. Replacement quality and
            savings are not evaluated.
          </p>
        </section>
      ) : (
        <>
          <section
            className="advisor-provenance advisor-panel"
            aria-label="Report provenance"
          >
            <div className="advisor-section-heading">
              <h2>Report provenance</h2>
              <span className="advisor-status">Not evaluated</span>
            </div>
            <Facts
              items={[
                ["Tenant", report.tenant_id],
                ["Generated at", report.generated_at ?? "Unknown"],
                ["Source", report.source],
                ["Source integrity", report.source_integrity],
                ["Synthetic observations", `${synthetic} of ${observations}`],
                [
                  "Data origin",
                  observations === 0
                    ? "No observations"
                    : synthetic === observations
                      ? "All observations marked synthetic"
                      : synthetic > 0
                        ? "Mixed synthetic and non-synthetic observations"
                        : "No observations marked synthetic",
                ],
              ]}
            />
            <p className="advisor-muted">
              The JSON format is validated on import. The browser does not
              independently verify source recordings or model inference.
            </p>
          </section>
          <div className="advisor-metrics">
            {[
              ["Recommendation groups", recommendations.length],
              ["Runs scanned", analysis?.scanned_runs ?? 0],
              ["Model steps scanned", analysis?.scanned_model_steps ?? 0],
              ["Analysis elapsed", number(analysis?.elapsed_ms, " ms")],
            ].map(([label, value]) => (
              <div className="advisor-panel" key={label}>
                <span className="eyebrow">{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <details
            className="advisor-analysis advisor-panel"
            open={limitReasons.length > 0}
          >
            <summary>
              Analysis coverage and limits
              {limitReasons.length > 0 ? " · Partial analysis" : ""}
            </summary>
            {limitReasons.length > 0 && (
              <div className="advisor-warning" role="note">
                <strong>Partial analysis</strong>
                <p>
                  Limits were reached; this report may omit observations or
                  groups.
                </p>
                <ul>
                  {limitReasons.map((reason, index) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              </div>
            )}
            <Facts
              items={[
                ["Replay runs skipped", analysis?.skipped_replay_runs ?? 0],
                [
                  "Incomplete runs skipped",
                  analysis?.skipped_incomplete_runs ?? 0,
                ],
                [
                  "Duplicate runs skipped",
                  analysis?.skipped_duplicate_runs ?? 0,
                ],
                [
                  "Oversized steps skipped",
                  analysis?.skipped_oversized_steps ?? 0,
                ],
                [
                  "Group observations skipped",
                  analysis?.skipped_group_observations ?? 0,
                ],
                ["Analysis model calls", analysis?.model_calls ?? 0],
                ["Analysis tool calls", analysis?.tool_calls ?? 0],
                ["Analysis input tokens", analysis?.input_tokens ?? 0],
                ["Analysis output tokens", analysis?.output_tokens ?? 0],
                ["Analysis cost (USD)", "Unknown"],
                ["Analysis energy", "Unknown"],
                ...Object.entries(report.limits).map(
                  ([key, value]): [string, string | number] => [
                    `Limit: ${key.replaceAll("_", " ")}`,
                    value,
                  ],
                ),
              ]}
            />
            <TextList
              title="Report limitations"
              items={report.limitations ?? []}
            />
          </details>
          <div className="advisor-section-heading advisor-results-heading">
            <h2>Recorded opportunities</h2>
            <span>
              {filtered.length} of {recommendations.length} groups · Report
              order
            </span>
          </div>
          <div className="advisor-filters">
            <label>
              Category
              <select
                aria-label="Filter advisor category"
                value={category}
                onChange={(event) => setCategory(event.target.value)}
              >
                <option value="all">All categories</option>
                {Object.entries(categoryLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select
                aria-label="Filter advisor status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="all">All statuses</option>
                {Object.entries(statusLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!filtered.length ? (
            <div className="advisor-panel advisor-empty">
              <h3>
                {recommendations.length
                  ? "No matching recommendations"
                  : "No recommendations in this report"}
              </h3>
              <p>
                {recommendations.length
                  ? "Choose another category or status."
                  : "Inspect analysis coverage and limitations above."}
              </p>
            </div>
          ) : (
            <div className="advisor-results">
              <nav
                className="advisor-list"
                aria-label="Advisor recommendations"
              >
                {filtered.map((item, index) => (
                  <button
                    key={`${item.recommendation_id}:${index}`}
                    className={`advisor-card${item === selectedItem ? " selected" : ""}`}
                    aria-current={item === selectedItem ? "true" : undefined}
                    onClick={() => setSelected({ id: item.recommendation_id })}
                  >
                    <span className="eyebrow">
                      {categoryLabels[item.finding.category ?? "unknown"]}
                    </span>
                    <strong>{item.step_id}</strong>
                    <span
                      className={`advisor-status advisor-status-${item.finding.status}`}
                    >
                      {statusLabels[item.finding.status]}
                    </span>
                    <p>{strategyLabels[item.finding.strategy]}</p>
                    <small>
                      {item.observed.model_calls ?? 0} observed dispatches ·{" "}
                      {item.observed.synthetic_observations ?? 0}/
                      {item.observed.observations ?? 0} synthetic
                    </small>
                    <span className="advisor-card-action">
                      Inspect finding <ArrowRight size={14} />
                    </span>
                  </button>
                ))}
              </nav>
              {selectedItem && (
                <div
                  ref={detailsRef}
                  tabIndex={-1}
                  className="advisor-detail-target"
                >
                  <RecommendationDetail
                    key={selectedItem.recommendation_id}
                    item={selectedItem}
                  />
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
