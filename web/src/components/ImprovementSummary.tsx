import { ArrowDownRight, ChevronDown, ShieldCheck, Zap } from "lucide-react";
import { improvements, improvementSources } from "../fixtures/improvements";
import "./comparison-enhancements.css";

export function ImprovementSummary() {
  return (
    <section
      className="improvement-summary"
      aria-labelledby="improvements-title"
    >
      <div className="benefits-heading">
        <div>
          <span className="eyebrow">FROM RETAINED SAMPLE MEASUREMENTS</span>
          <h2 id="improvements-title">Less repeat work. More room to build.</h2>
        </div>
        <span className="benefits-date">October 17, 2026</span>
      </div>
      <p className="comparison-context">
        Measured examples of reuse, communication efficiency and controls. These
        results come from separate sample workloads, not the cargo simulation
        below.
      </p>
      <div className="benefit-highlights">
        {["calls", "time", "bytes", "writes"].map((id) => {
          const item = improvements.find((row) => row.id === id)!;
          return (
            <div className={`benefit-card benefit-${item.tone}`} key={id}>
              {item.tone === "saving" ? (
                <ArrowDownRight size={18} aria-hidden="true" />
              ) : (
                <Zap size={18} aria-hidden="true" />
              )}
              <strong>{item.benefit}</strong>
              <span>{item.label}</span>
              <small>{item.measured}</small>
            </div>
          );
        })}
      </div>
      <details className="improvement-details">
        <summary>
          Explore all 14 improvements
          <ChevronDown size={16} aria-hidden="true" />
        </summary>
        <div
          className="improvement-table-scroll"
          role="region"
          aria-label="All improvement measurements"
          tabIndex={0}
        >
          <table className="improvement-table">
            <caption>
              Retained sample results · measurement context and source for every
              claim
            </caption>
            <thead>
              <tr>
                <th scope="col">What improves</th>
                <th scope="col">Measured numbers</th>
                <th scope="col">Benefit</th>
              </tr>
            </thead>
            <tbody>
              {improvements.map((item) => (
                <tr key={item.id} className={`benefit-${item.tone}`}>
                  <th scope="row">{item.label}</th>
                  <td>
                    {item.measured}
                    <details className="measurement-context">
                      <summary>Context & source</summary>
                      <p>{item.context}</p>
                      {[
                        item.source,
                        ...(item.additionalSource
                          ? [item.additionalSource]
                          : []),
                      ].map((source) => (
                        <a
                          key={source}
                          href={improvementSources[source].href}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {improvementSources[source].label} ↗
                        </a>
                      ))}
                    </details>
                  </td>
                  <td>
                    <span className="benefit-value">
                      {item.tone === "guardrail" && (
                        <ShieldCheck size={13} aria-hidden="true" />
                      )}
                      {item.benefit}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <p className="comparison-context">
        JEV accuracy covers authored sample cases. Business choices can be
        correct and still defer. Byte reductions do not establish token, dollar
        or energy savings.
      </p>
    </section>
  );
}
