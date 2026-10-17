import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { advisorChallenge, advisorDemo } from "../fixtures/advisor";
import { Optimization } from "./Optimization";

function show(report = advisorDemo) {
  return render(
    <Optimization
      report={report}
      origin="demo"
      busy={false}
      error={null}
      onImport={vi.fn()}
      onLoadDemo={vi.fn()}
    />,
  );
}
function detail() {
  return screen.getByRole("region", { name: "Recommendation details" });
}

describe("optimization findings", () => {
  it("moves keyboard focus to details even when reopening the selected finding", () => {
    show();
    const button = screen.getByRole("button", { name: /review_operations/ });
    fireEvent.click(button);
    expect(detail().parentElement).toHaveFocus();
    button.focus();
    fireEvent.click(button);
    expect(detail().parentElement).toHaveFocus();
  });
  it("renders report provenance, ordered groups, and unevaluated measurements", () => {
    show();
    expect(screen.getByText("advisor-demo")).toBeInTheDocument();
    expect(screen.getByText("verified_recordings")).toBeInTheDocument();
    expect(screen.getByText("28 of 28")).toBeInTheDocument();
    expect(
      screen.getByText("All observations marked synthetic"),
    ).toBeInTheDocument();
    const buttons = within(
      screen.getByRole("navigation", { name: "Advisor recommendations" }),
    ).getAllByRole("button");
    expect(buttons).toHaveLength(7);
    expect(
      buttons.map(
        (button) =>
          within(button).getByText(
            /^(choose_next_step|choose_tool|write_response|classify_ticket|review_operations|check_threshold|unmapped_decision)$/,
          ).textContent,
      ),
    ).toEqual(advisorDemo.recommendations.map((item) => item.step_id));
    expect(within(detail()).getByText("Not evaluated")).toBeInTheDocument();
    expect(
      within(detail()).getAllByText("Unknown").length,
    ).toBeGreaterThanOrEqual(3);
  });

  it("filters categories and statuses together, including an empty result", () => {
    show();
    fireEvent.change(
      screen.getByRole("combobox", { name: "Filter advisor category" }),
      { target: { value: "tool_selection" } },
    );
    expect(
      screen.getByText("1 of 7 groups · Report order"),
    ).toBeInTheDocument();
    expect(
      within(detail()).getByRole("heading", { name: "choose_tool" }),
    ).toBeInTheDocument();
    fireEvent.change(
      screen.getByRole("combobox", { name: "Filter advisor status" }),
      { target: { value: "candidate" } },
    );
    expect(screen.getByText("No matching recommendations")).toBeInTheDocument();
  });

  it("shows native compatibility, revisions, bounded samples, and copyable references", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: /review_operations/ }));
    const panel = within(detail());
    expect(panel.getByText("Native request shape")).toBeInTheDocument();
    expect(
      panel.getByText(/Successful OpenJEV inference has not been established/),
    ).toBeInTheDocument();
    fireEvent.click(panel.getByText("Identity and revisions"));
    const item = advisorDemo.recommendations.find(
      (item) => item.step_id === "review_operations",
    )!;
    expect(panel.getByText(item.group_fingerprint)).toBeVisible();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const reference = `${item.samples[0].run_id} / ${item.samples[0].step_id}`;
    fireEvent.click(
      panel.getByRole("button", { name: `Copy reference ${reference}` }),
    );
    expect(writeText).toHaveBeenCalledWith(reference);
    expect(await panel.findByText("Copied")).toBeInTheDocument();
    expect(panel.queryByRole("link")).not.toBeInTheDocument();
  });

  it("displays partial-analysis and sample-limit notices with skipped counts", () => {
    show(advisorChallenge("partial"));
    expect(screen.getByText("Partial analysis")).toBeVisible();
    expect(screen.getByText("max_runs")).toBeVisible();
    expect(
      screen.getByText("Group observations skipped").nextElementSibling,
    ).toHaveTextContent("3");
    expect(screen.getByText(/Sample limit reached/)).toBeInTheDocument();
  });

  it("keeps unknown resources distinct from measured zero", () => {
    show(advisorChallenge("unknown"));
    const panel = within(detail());
    expect(
      panel.getByText("Input tokens").nextElementSibling,
    ).toHaveTextContent("Unknown");
    expect(
      panel.getByText("Recorded elapsed").nextElementSibling,
    ).toHaveTextContent("Unknown");
    expect(
      panel.getByText("Recorded cost (USD)").nextElementSibling,
    ).toHaveTextContent("Unknown");
  });

  it("shows mixed origins, partial substitution, and retained generation", () => {
    show(advisorChallenge("mixed"));
    expect(
      screen.getByText("Mixed synthetic and non-synthetic observations"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Partial substitution: argument generation/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Retain the separate explanation generator/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /write_response/ }));
    expect(within(detail()).getByText("Retain LLM")).toBeInTheDocument();
    expect(within(detail()).getByText("Unsupported")).toBeInTheDocument();
  });

  it("renders imported strings as text and accepts empty recommendation sets", () => {
    const report = structuredClone(advisorDemo);
    report.recommendations[0].finding.reasons = [
      "<img src=x onerror=alert(1)>",
    ];
    const view = show(report);
    expect(
      screen.getByText("<img src=x onerror=alert(1)>"),
    ).toBeInTheDocument();
    expect(view.container.querySelector("img")).toBeNull();
    view.rerender(
      <Optimization
        report={{ ...report, recommendations: [] }}
        origin="import"
        busy={false}
        error={null}
        onImport={vi.fn()}
        onLoadDemo={vi.fn()}
      />,
    );
    expect(
      screen.getByText("No recommendations in this report"),
    ).toBeInTheDocument();
  });
});
