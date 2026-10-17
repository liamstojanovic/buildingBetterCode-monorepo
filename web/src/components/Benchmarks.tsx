import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  CirclePause,
  Download,
  ExternalLink,
  FlaskConical,
  Layers3,
  RefreshCw,
} from "lucide-react";
import {
  fetchBenchmarkEvidence,
  type BenchmarkEvidence,
} from "../api/benchmarks";
import "./benchmarks.css";

type Category = "business_decision" | "tool_selection" | "next_step";
type View = Category | "comms";
type Case = BenchmarkEvidence["jev"]["cases"][number];

const categories: { key: View; label: string; description: string }[] = [
  {
    key: "business_decision",
    label: "Business decisions",
    description: "A correct choice can still be deferred",
  },
  {
    key: "tool_selection",
    label: "Tool selection",
    description: "Choose a tool with arguments already bound",
  },
  {
    key: "next_step",
    label: "Next steps",
    description: "Compare transitions with the rule baseline",
  },
  {
    key: "comms",
    label: "CBOR-LD-EX savings",
    description: "Fewer message bytes, with CPU costs visible",
  },
];

function number(value: number, digits = 0) {
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function ms(value: number) {
  return `${number(value, 2)} ms`;
}

function date(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : `${parsed.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="benchmark-metric">
      <dt>{label}</dt>
      <dd>
        {value}
        <p style={{ letterSpacing: "normal" }}>{detail}</p>
      </dd>
    </div>
  );
}

function Facts({ items }: { items: [string, string | number][] }) {
  return (
    <dl className="benchmark-facts">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function DecisionDetail({ item }: { item: Case }) {
  const [trialIndex, setTrialIndex] = useState(0);
  const trial = item.trials[trialIndex] ?? item.trials[0];
  const deferred = trial.acceptedChoice == null;
  const deferredTrials = item.trials.filter(
    (record) => record.acceptedChoice == null,
  ).length;
  const caution = deferred || !trial.acceptedMatch;
  const sortedScores = Object.entries(trial.scores).sort((a, b) => b[1] - a[1]);
  return (
    <section
      className="benchmark-case-detail benchmark-panel"
      aria-label="Measured decision detail"
    >
      <div className="benchmark-detail-top">
        <div>
          <span className="benchmark-kicker">{item.stepId}</span>
          <h2>{item.expectedChoice.replaceAll("_", " ")}</h2>
        </div>
        <span
          className={`benchmark-outcome ${caution ? "is-deferred" : "is-accepted"}`}
        >
          {caution ? <CirclePause size={15} /> : <Check size={15} />}
          {deferred
            ? "Deferred"
            : trial.acceptedMatch
              ? "Accepted"
              : "Accepted · expected mismatch"}
        </span>
      </div>
      <div
        className="benchmark-trial-switch"
        role="group"
        aria-label="Recorded JEV trial"
      >
        {item.trials.map((record, index) => (
          <button
            key={record.trial}
            aria-pressed={trialIndex === index}
            onClick={() => setTrialIndex(index)}
          >
            Pass {record.trial} · {record.cold ? "cold worker" : "warm worker"}
          </button>
        ))}
      </div>
      <div className="benchmark-choice-flow">
        <div>
          <span>Expected choice</span>
          <strong>{item.expectedChoice}</strong>
        </div>
        <ArrowRight aria-hidden="true" size={18} />
        <div>
          <span>Raw JEV choice</span>
          <strong>{trial.rawChoice ?? "None"}</strong>
          <small>
            {trial.rawMatch ? "Matches expected" : "Does not match expected"}
          </small>
        </div>
        <ArrowRight aria-hidden="true" size={18} />
        <div>
          <span>After acceptance gate</span>
          <strong>{trial.acceptedChoice ?? "Defer"}</strong>
          <small>
            {deferred
              ? "Keep the existing decision path"
              : trial.acceptedMatch
                ? "Accepted and matches expected"
                : "Accepted, but does not match expected"}
          </small>
        </div>
      </div>
      <div
        className={`benchmark-callout ${caution ? "benchmark-caution" : ""}`}
      >
        {deferred ? (
          <p>
            <strong>
              {trial.rawMatch
                ? "The raw choice matches expected, but the gate defers it."
                : "The raw choice does not match expected and stays deferred."}
            </strong>{" "}
            {trial.margin < trial.minimumMargin && (
              <>
                The gap of {number(trial.margin, 3)} is below the{" "}
                {number(trial.minimumMargin, 2)} minimum.{" "}
              </>
            )}
            {trial.selectedEntailment < trial.minimumEntailment && (
              <>
                Selected entailment {number(trial.selectedEntailment, 3)} is
                below the {number(trial.minimumEntailment, 2)} minimum.{" "}
              </>
            )}
            Deferred in {deferredTrials} of {item.trials.length} recorded
            trials. The recorded reasons appear below.
          </p>
        ) : (
          <p>
            <strong>
              {trial.acceptedMatch
                ? "The mapped choice passes the recorded gate."
                : "The gate accepted a choice that does not match expected."}
            </strong>{" "}
            {!trial.acceptedMatch && (
              <>Acceptance is not proof of correctness. </>
            )}
            This retrospective evaluation selects a label; it does not execute a
            tool or transition, or enable automatic routing.
          </p>
        )}
        {item.requiresExplanation && (
          <p>
            The <code>reason</code> field still needs explanation generation.
            JEV has evaluated the bounded choice only.
          </p>
        )}
        {trial.ruleBaseline != null && (
          <p>
            <strong>
              Deterministic baseline: <code>{trial.ruleBaseline}</code>.
            </strong>{" "}
            These supplied transition rules can select the next step without a
            model call; evaluate that simpler option first.
          </p>
        )}
        {trial.preboundArguments != null && (
          <p>
            Tool arguments were prebound. Argument generation and the tool
            action are outside this comparison.
          </p>
        )}
      </div>
      <div className="benchmark-score-heading">
        <h3>Independent entailment scores</h3>
        <span>Not calibrated confidence</span>
      </div>
      <div className="benchmark-scores">
        {sortedScores.map(([choice, score]) => (
          <div key={choice} className="benchmark-score">
            <span>{choice}</span>
            <div className="benchmark-score-track" aria-hidden="true">
              <i
                style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }}
              />
            </div>
            <strong>{score.toFixed(4)}</strong>
          </div>
        ))}
      </div>
      <Facts
        items={[
          [
            "Selected entailment / minimum",
            `${number(trial.selectedEntailment, 4)} / ${number(trial.minimumEntailment, 2)}`,
          ],
          [
            "Margin / minimum",
            `${number(trial.margin, 4)} / ${number(trial.minimumMargin, 2)}`,
          ],
          ["End-to-end trial", ms(trial.elapsedMs)],
          ["Action executed", trial.actionExecuted ? "Yes" : "No"],
        ]}
      />
      <p className="benchmark-muted">
        {trial.thresholdBasis === "uncalibrated_demo_heuristic" ? (
          "The thresholds are uncalibrated demo heuristics, not production quality guarantees."
        ) : (
          <>
            Recorded threshold basis: <code>{trial.thresholdBasis}</code>.
          </>
        )}{" "}
        {trial.reasons.length > 0 && (
          <>
            Recorded reason: <code>{trial.reasons.join(", ")}</code>.
          </>
        )}
      </p>
      <details className="benchmark-input">
        <summary>Inspect the mapped input and hypotheses</summary>
        <p>{item.premise}</p>
        <dl>
          {item.choices.map((choice) => (
            <div key={choice.name}>
              <dt>
                <code>{choice.name}</code>
              </dt>
              <dd>{choice.hypothesis}</dd>
            </div>
          ))}
        </dl>
        {trial.preboundArguments != null && (
          <pre>{JSON.stringify(trial.preboundArguments, null, 2)}</pre>
        )}
        <p className="benchmark-muted">
          Expected result: {item.expectationBasis}
        </p>
      </details>
    </section>
  );
}

function JevResults({
  report,
  category,
}: {
  report: BenchmarkEvidence;
  category: Category;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const cases = report.jev.cases.filter((item) => item.category === category);
  const selected = cases.find((item) => item.id === selectedId) ?? cases[0];
  return (
    <>
      <div className="benchmark-section-heading">
        <div>
          <span className="benchmark-kicker">01 / Mapped decisions</span>
          <h2>
            {categories.find((item) => item.key === category)?.description}
          </h2>
        </div>
        <span>
          {cases.length} distinct {cases.length === 1 ? "case" : "cases"} ·{" "}
          {cases.reduce((total, item) => total + item.trials.length, 0)}{" "}
          recorded calls
        </span>
      </div>
      <div className="benchmark-decisions">
        <nav className="benchmark-case-list" aria-label="Benchmark cases">
          {cases.map((item) => {
            const accepted = item.trials.filter(
              (trial) => trial.acceptedMatch,
            ).length;
            return (
              <button
                key={item.id}
                aria-pressed={selected.id === item.id}
                onClick={() => setSelectedId(item.id)}
              >
                <span>{item.expectedChoice.replaceAll("_", " ")}</span>
                <code>{item.stepId}</code>
                <small>
                  {accepted}/{item.trials.length} accepted matches ·{" "}
                  {item.trials.filter((trial) => trial.rawMatch).length}/
                  {item.trials.length} raw matches
                </small>
                <ArrowRight size={16} aria-hidden="true" />
              </button>
            );
          })}
          <p>Recorded sample tasks. No tools run when you select a case.</p>
        </nav>
        <DecisionDetail key={selected.id} item={selected} />
      </div>
      <section
        className="benchmark-panel benchmark-timing"
        aria-labelledby="benchmark-timing-heading"
      >
        <div>
          <span className="benchmark-kicker">02 / Timing & comparison</span>
          <h2 id="benchmark-timing-heading">
            Separate loading from warm inference
          </h2>
          <p>
            This evaluation covers {report.jev.distinctCases} distinct cases and{" "}
            {report.jev.trials} recorded calls. The warm sample includes{" "}
            {report.jev.warm.count} calls after the first model load.
          </p>
          <p className="benchmark-muted">
            Evaluated model: <code>{report.jev.model}</code> through
            Pollard-Jev.
          </p>
        </div>
        <dl className="benchmark-timing-metrics">
          <Metric
            label="First cold call"
            value={ms(report.jev.coldMs)}
            detail={`${ms(report.jev.modelLoadMs)} model load included`}
          />
          <Metric
            label="Warm median"
            value={ms(report.jev.warm.medianMs)}
            detail={`${ms(report.jev.warm.minMs)}–${ms(report.jev.warm.maxMs)} range`}
          />
          <Metric
            label="Recorded hosted median"
            value={ms(report.jev.hosted.latency.medianMs)}
            detail={`${report.jev.hosted.latency.count} historical provider calls`}
          />
        </dl>
        <p className="benchmark-callout">
          These are separate historical runs with different models, hardware,
          output work and conditions. The timings do not establish a controlled
          speedup or end-to-end replacement savings.
        </p>
        <div className="benchmark-two-column">
          <section>
            <h3>Actual hosted calls on the source sample</h3>
            <Facts
              items={[
                [
                  "Normalized expected matches",
                  `${report.jev.hosted.normalizedMatches}/${report.jev.hosted.calls}`,
                ],
                [
                  "Correct + strict bare JSON",
                  `${report.jev.hosted.strictMatches}/${report.jev.hosted.calls}`,
                ],
                ["Reused steps excluded", report.jev.hosted.excludedReused],
              ]}
            />
            {report.jev.hosted.wrongChoices.map((item) => (
              <p
                className="benchmark-muted"
                key={`${item.provider}-${item.caseId}-${item.runLabel}`}
              >
                Recorded disagreement: {item.provider} chose{" "}
                <code>{item.choice}</code> for <code>{item.caseId}</code>;
                expected <code>{item.expected}</code>.
              </p>
            ))}
          </section>
          <section>
            <h3>Hardware and capacity scope</h3>
            <p>{report.gpu.name}</p>
            <Facts
              items={[
                ["Device memory", `${number(report.gpu.totalMiB)} MiB`],
                [
                  "Preflight peak allocated",
                  `${number(report.gpu.peakAllocatedMiB, 2)} MiB`,
                ],
                [
                  "Allocator ceiling",
                  `${number(report.gpu.allocatorLimitMiB)} MiB`,
                ],
              ]}
            />
            <p className="benchmark-muted">
              The teammate’s physical 12 GB GPU has not been tested. Run the
              documented preflight there. This local validation used a
              compatibility override for an older Docker Desktop; the standard
              current-Docker GPU path remains unverified here.
            </p>
          </section>
        </div>
      </section>
    </>
  );
}

function CommsResults({ report }: { report: BenchmarkEvidence }) {
  const data = report.comms;
  const jsonTiming = data.timings.find((timing) => timing.encoding === "json")!;
  const cborTiming = data.timings.find((timing) => timing.encoding === "cbor")!;
  const compareTime = (measured: number, baseline: number) =>
    measured === baseline
      ? "equal to"
      : measured > baseline
        ? "higher than"
        : "lower than";
  const encodings: Record<string, string> = {
    json: "Compact JSON",
    single_cbor: "Single-message CBOR",
    cbor: "Shared-context CBOR",
    auto: "Automatic selection",
  };
  return (
    <>
      <div className="benchmark-section-heading">
        <div>
          <span className="benchmark-kicker">01 / Message representation</span>
          <h2>The same messages, fewer payload bytes</h2>
        </div>
        <span>
          {number(data.messages)} messages · {number(data.batches)} batches
        </span>
      </div>
      <section
        className="benchmark-panel benchmark-wire"
        aria-label="CBOR byte comparison"
      >
        <div className="benchmark-wire-summary">
          <Layers3 size={28} />
          <strong>{number(data.reductionFraction * 100, 2)}%</strong>
          <h3>smaller binary payload</h3>
          <p>
            {number(data.bytesSaved)} bytes removed across the same complete
            envelopes.
          </p>
          <span className="benchmark-outcome is-accepted">
            <Check size={15} />
            {data.roundtripVerified
              ? "Lossless round trip verified"
              : "Round trip not verified"}
          </span>
        </div>
        <div className="benchmark-byte-bars">
          {[
            ["Compact JSON", data.jsonBytes],
            ["Single-message CBOR", data.singleCborBytes],
            ["Shared-context CBOR", data.batchCborBytes],
          ].map(([label, bytes]) => (
            <div className="benchmark-byte-row" key={label}>
              <div>
                <span>{label}</span>
                <strong>{number(bytes as number)} bytes</strong>
              </div>
              <div className="benchmark-byte-track" aria-hidden="true">
                <i
                  style={{
                    width: `${((bytes as number) / data.jsonBytes) * 100}%`,
                  }}
                />
              </div>
            </div>
          ))}
          <p className="benchmark-muted">
            Shared context and annotations are included. Transport headers are
            excluded. The inspectable base64 export is{" "}
            {number(data.base64Bytes)} bytes; the bar measures binary bytes.
          </p>
        </div>
      </section>
      <p className="benchmark-callout">
        This is a local replay of retained hosted-model events on sample tasks.{" "}
        {data.networkCalls} network calls were made. Byte savings are not token,
        dollar, network-latency or energy savings.
      </p>
      <div className="benchmark-two-column benchmark-comms-details">
        <section className="benchmark-panel">
          <span className="benchmark-kicker">02 / CPU tradeoff</span>
          <h2>Smaller does not mean faster serialization</h2>
          <p>
            Validated encode/decode, median of {data.timingRepetitions} repeats
            over all {number(data.messages)} messages. Shared-context CBOR
            encode time was{" "}
            {compareTime(cborTiming.encodeMs, jsonTiming.encodeMs)} compact
            JSON, and decode time was{" "}
            {compareTime(cborTiming.decodeMs, jsonTiming.decodeMs)} compact
            JSON.
          </p>
          <div className="benchmark-table-wrap">
            <table>
              <caption className="sr-only">Local serialization times</caption>
              <thead>
                <tr>
                  <th scope="col">Encoding</th>
                  <th scope="col">Encode</th>
                  <th scope="col">Decode</th>
                </tr>
              </thead>
              <tbody>
                {data.timings.map((timing) => (
                  <tr key={timing.encoding}>
                    <th scope="row">
                      {encodings[timing.encoding] ?? timing.encoding}
                    </th>
                    <td>{ms(timing.encodeMs)}</td>
                    <td>{ms(timing.decodeMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="benchmark-muted">
            Excludes source I/O, SQLite, network and batch accumulation.
            Batching can delay first-event delivery.
          </p>
        </section>
        <section className="benchmark-panel">
          <span className="benchmark-kicker">
            03 / Choose the smaller format
          </span>
          <h2>Dense floats fall back to JSON</h2>
          <p>{data.denseFallback.detail}</p>
          <Facts
            items={[
              ["JSON", `${number(data.denseFallback.jsonBytes)} bytes`],
              ["CBOR", `${number(data.denseFallback.cborBytes)} bytes`],
              [
                "Selected format",
                data.denseFallback.selectedEncoding.toUpperCase(),
              ],
              [
                "Selected size",
                `${number(data.denseFallback.selectedBytes)} bytes`,
              ],
            ]}
          />
          <p className="benchmark-muted">
            This additional synthetic fixture tests format selection. It is
            separate from the {number(data.messages)}-message replay above.
          </p>
        </section>
      </div>
      <section className="benchmark-panel benchmark-mailbox">
        <span className="benchmark-kicker">04 / Local delivery</span>
        <h2>SQLite commits with batching</h2>
        <Facts
          items={[
            [
              "Individual delivery",
              `${ms(data.mailbox.individualMs)} · ${number(data.mailbox.individualTransactions)} transactions`,
            ],
            [
              "Batch delivery",
              `${ms(data.mailbox.batchMs)} · ${number(data.mailbox.batchTransactions)} transactions`,
            ],
            [
              "Delivered / acknowledged",
              `${number(data.mailbox.delivered)} / ${number(data.mailbox.acknowledged)}`,
            ],
            [
              "Duplicate messages suppressed",
              data.mailbox.duplicatesSuppressed,
            ],
          ]}
        />
        <p className="benchmark-muted">{data.mailbox.scope}</p>
        <p className="benchmark-muted">
          Each duplicate is one redelivered message from a batch, not a
          duplicated whole batch. These opt-in primitives are not automatic
          runtime networking.
        </p>
      </section>
    </>
  );
}

export function Benchmarks({ active = true }: { active?: boolean }) {
  const [report, setReport] = useState<BenchmarkEvidence | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [view, setView] = useState<View>("business_decision");
  const businessTrials =
    report?.jev.cases
      .filter((item) => item.category === "business_decision")
      .flatMap((item) => item.trials) ?? [];
  const deferredBusinessTrials = businessTrials.filter(
    (trial) => trial.acceptedChoice == null,
  ).length;
  useEffect(() => {
    if (!active || report) return;
    let current = true;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchBenchmarkEvidence(controller.signal)
      .then((value) => {
        if (current) {
          setReport(value);
          setLoading(false);
        }
      })
      .catch((reason: unknown) => {
        if (current) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to load benchmark evidence.",
          );
          setLoading(false);
        }
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [active, attempt, report]);

  return (
    <div className="benchmarks-workspace">
      <header className="benchmark-hero">
        <div>
          <span className="benchmark-kicker">
            <FlaskConical size={14} /> Measured evidence
          </span>
          <h1>
            Decisions, measured.
            <br />
            <span>Tradeoffs, visible.</span>
          </h1>
          <p>
            Inspect actual OpenJEV results for three decision categories, then
            follow the bytes through CBOR-LD-EX. Historical evaluations on
            sample tasks; no model calls are made by this page.
          </p>
        </div>
        <span className="benchmark-evidence-badge">
          Recorded results
          <br />
          <strong>Real model inference</strong>
        </span>
      </header>
      {loading && (
        <p role="status" className="benchmark-loading">
          Loading the published benchmark evidence…
        </p>
      )}
      {error && (
        <section className="benchmark-panel benchmark-load-error" role="alert">
          <h2>Benchmark evidence unavailable</h2>
          <p>{error}</p>
          <p>
            Results cannot be shown until the published evidence loads
            successfully.
          </p>
          <button
            className="secondary-button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            <RefreshCw size={15} /> Retry loading evidence
          </button>
        </section>
      )}
      {report && (
        <>
          <div className="benchmark-publication">
            <span>
              Published{" "}
              <time dateTime={report.publishedAt}>
                {date(report.publishedAt)}
              </time>
            </span>
            <a href={report.sourceUrl} target="_blank" rel="noreferrer">
              Source {report.sourceCommit.slice(0, 7)}{" "}
              <ExternalLink size={12} />
            </a>
          </div>
          <dl className="benchmark-overview">
            <Metric
              label="Raw JEV matches"
              value={`${report.jev.rawMatches}/${report.jev.trials}`}
              detail={`${report.jev.distinctCases} distinct cases · ${report.jev.trials} calls`}
            />
            <Metric
              label="Accepted JEV matches"
              value={`${report.jev.acceptedMatches}/${report.jev.trials}`}
              detail={`${deferredBusinessTrials}/${businessTrials.length} business trials deferred`}
            />
            <Metric
              label="CBOR payload reduction"
              value={`${number(report.comms.reductionFraction * 100, 2)}%`}
              detail={`${number(report.comms.messages)} messages · lossless`}
            />
            <Metric
              label="New provider spend"
              value={`$${number(report.spend.providerUsd, 2)}`}
              detail={`$${number(report.spend.budgetUsd)} refresh budget`}
            />
          </dl>
          <nav
            className="benchmark-category-nav"
            aria-label="Benchmark categories"
          >
            {categories.map((item, index) => (
              <button
                key={item.key}
                aria-pressed={view === item.key}
                onClick={() => setView(item.key)}
              >
                <small>0{index + 1}</small>
                <span>{item.label}</span>
                {view === item.key && (
                  <ArrowRight size={16} aria-hidden="true" />
                )}
              </button>
            ))}
          </nav>
          {view === "comms" ? (
            <CommsResults report={report} />
          ) : (
            <JevResults report={report} category={view} />
          )}
          <section
            className="benchmark-panel benchmark-provenance"
            aria-labelledby="benchmark-provenance-heading"
          >
            <div>
              <span className="benchmark-kicker">Source & scope</span>
              <h2 id="benchmark-provenance-heading">
                Every headline has a source
              </h2>
              <p>
                {report.advisor.reports} offline advisor reports,{" "}
                {report.advisor.groups} groups, {report.advisor.modelCalls} new
                advisor model calls. Discovery findings remain distinct from
                this measured mapping evaluation and the guided execution demo’s
                modeled token prices.
              </p>
              <Facts
                items={[
                  ["JEV evaluated", date(report.dates.jev)],
                  ["CPU / advisor refresh", date(report.dates.cpu)],
                  ["Communications measured", date(report.dates.comms)],
                  ["Last source audit", date(report.auditedAt)],
                ]}
              />
              <p className="benchmark-muted">
                Historical hosted cost, replacement cost savings and energy
                savings are unknown. The ${number(report.spend.providerUsd, 2)}{" "}
                incremental provider spend applies to this evidence refresh, not
                to free inference or the original hosted calls.
              </p>
              <p className="benchmark-muted">
                The {report.jev.distinctCases} distinct cases are a small
                demonstration, not a general quality benchmark. All source
                decisions are historical; their original evidence may now be
                stale. No production action or automatic JEV activation is
                enabled.
              </p>
            </div>
            <div className="benchmark-downloads">
              <h3>Download raw evidence</h3>
              {report.downloads.map((item) => (
                <a href={item.url} key={item.url} download>
                  <Download size={15} />
                  <span>
                    {item.label}
                    <small>SHA-256 {item.sha256.slice(0, 12)}…</small>
                  </span>
                  <ArrowRight size={14} />
                </a>
              ))}
              <a href="/benchmarks/latest.json" download>
                <Download size={15} />
                <span>
                  Frontend evidence projection
                  <small>The exact data used on this page</small>
                </span>
                <ArrowRight size={14} />
              </a>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
