import { describe, expect, it, vi } from "vitest";
import demo from "../../public/recordings/advisor-demo.json";
import {
  MAX_ADVISOR_BYTES,
  parseAdvisorReport,
  readAdvisorFile,
} from "./advisor";

describe("offline advisor imports", () => {
  it("validates the actual seven-group demo without modifying it", () => {
    const before = structuredClone(demo);
    const report = parseAdvisorReport(demo);
    expect(report.recommendations).toHaveLength(7);
    expect(
      report.recommendations.find(
        (item) => item.step_id === "review_operations",
      )?.finding.native_compatibility,
    ).toBe("native_request_shape");
    expect(report).not.toBe(demo);
    expect(demo).toEqual(before);
  });

  it("normalizes declared schema defaults on a detached import", () => {
    const input = structuredClone(demo) as Record<string, any>;
    delete input.analysis.scanned_runs;
    delete input.recommendations[0].finding.category;
    const report = parseAdvisorReport(input);
    expect(report.analysis.scanned_runs).toBe(0);
    expect(report.recommendations[0].finding.category).toBe("unknown");
    expect(input.analysis.scanned_runs).toBeUndefined();
  });

  it.each([
    null,
    [],
    { task: {} },
    { format: "canopy-comparison/v1" },
    { demo_type: "canopy.offline_advisor.synthetic_demo" },
  ])("rejects the wrong format: %j", (input) => {
    expect(() => parseAdvisorReport(input)).toThrow(
      /AdvisorReport|report_type/,
    );
  });

  it.each(["schema_version", "analyzer_version", "report_type"])(
    "requires an explicitly supported %s",
    (field) => {
      expect(() => parseAdvisorReport({ ...demo, [field]: "future" })).toThrow(
        /Unsupported|Expected/,
      );
      const missing = { ...demo } as Record<string, unknown>;
      delete missing[field];
      expect(() => parseAdvisorReport(missing)).toThrow(/Unsupported|Expected/);
    },
  );

  it("rejects malformed fields and invented evaluated states", () => {
    expect(() => parseAdvisorReport({ ...demo, tenant_id: 10 })).toThrow(
      /Invalid advisor report/,
    );
    const input = structuredClone(demo);
    input.recommendations[0].evaluation_status = "validated";
    expect(() => parseAdvisorReport(input)).toThrow(/Invalid advisor report/);
  });

  it("rejects malformed JSON and read failures clearly", async () => {
    await expect(
      readAdvisorFile({ size: 1, text: async () => "{" }),
    ).rejects.toThrow("valid UTF-8 JSON");
    await expect(
      readAdvisorFile({
        size: 1,
        text: async () => {
          throw new Error("read failed");
        },
      }),
    ).rejects.toThrow("Could not read");
  });

  it("checks the 20 MiB cap before reading and accepts the boundary", async () => {
    const text = vi.fn(async () => JSON.stringify(demo));
    await expect(
      readAdvisorFile({ size: MAX_ADVISOR_BYTES + 1, text }),
    ).rejects.toThrow("20 MiB");
    expect(text).not.toHaveBeenCalled();
    await expect(
      readAdvisorFile({ size: MAX_ADVISOR_BYTES, text }),
    ).resolves.toHaveProperty("tenant_id", "advisor-demo");
  });
});
