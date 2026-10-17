import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ImprovementSummary } from "./ImprovementSummary";
import { improvements } from "../fixtures/improvements";
import evidence from "../../public/benchmarks/latest.json";

describe("homepage improvement evidence", () => {
  it("highlights benefits and exposes all 14 rows with context and sources, without a PDF", () => {
    const { container } = render(<ImprovementSummary />);
    expect(screen.getAllByText("53.8% smaller")[0]).toBeVisible();
    fireEvent.click(screen.getByText("Explore all 14 improvements"));
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(15);
    for (const item of improvements) {
      const row = within(table)
        .getByRole("rowheader", { name: item.label })
        .closest("tr")!;
      fireEvent.click(within(row).getByText("Context & source"));
      expect(within(row).getByText(item.context)).toBeVisible();
      for (const link of within(row).getAllByRole("link"))
        expect(link).toHaveAttribute("href");
    }
    expect(
      container.querySelector('a[href$=".pdf"], iframe, embed'),
    ).toBeNull();
    expect(
      screen.getByText(/Business choices can be correct and still defer/),
    ).toBeVisible();
  });
  it("keeps the published communication and JEV numbers consistent", () => {
    const byId = Object.fromEntries(improvements.map((row) => [row.id, row]));
    expect(byId.bytes.benefit).toBe(
      `${(evidence.comms.reductionFraction * 100).toFixed(1)}% smaller`,
    );
    expect(byId.writes.measured).toBe(
      `${evidence.comms.mailbox.individualTransactions} → ${evidence.comms.mailbox.batchTransactions} writes`,
    );
    expect(byId.duplicates.benefit).toBe(
      `${evidence.comms.mailbox.duplicatesSuppressed} duplicates blocked`,
    );
    expect(byId.discovery.measured).toBe(
      `${evidence.advisor.groups} candidate groups from ${evidence.advisor.reports} reports`,
    );
    expect(
      evidence.jev.cases
        .filter((c) => c.category === "business_decision")
        .every((c) => c.trials.every((t) => t.disposition === "defer")),
    ).toBe(true);
  });
});
