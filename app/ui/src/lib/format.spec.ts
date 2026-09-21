import { describe, expect, it } from "vitest";
import { formatDateTime, jobTarget } from "@/lib/format";
import type { Job } from "@/types";

function job(overrides: Partial<Job>): Job {
  return {
    id: "j1",
    kind: "compile",
    state: "succeeded",
    created_at: null,
    finished_at: null,
    error: null,
    ...overrides,
  };
}

describe("jobTarget", () => {
  it("shows a compiled clip by filename, not by its whole path", () => {
    expect(jobTarget(job({ target: "films/feature.mp4" }))).toBe("feature.mp4");
  });

  it("shows a scan's collections as they came", () => {
    expect(jobTarget(job({ kind: "scan", target: "films, shorts" }))).toBe("films, shorts");
  });

  it("names a library-wide scan rather than leaving it blank", () => {
    expect(jobTarget(job({ kind: "scan", target: "" }))).toBe("every collection");
  });

  it("stays empty when a job kind carries no target", () => {
    expect(jobTarget(job({ kind: "cleanup", target: "" }))).toBe("");
  });
});

describe("formatDateTime", () => {
  // 2026-01-15T04:30:00Z is 2026-01-14 22:30 in America/Costa_Rica (UTC-6).
  const timestamp = "2026-01-15T04:30:00Z";

  it("renders in Costa Rica time by default", () => {
    const result = formatDateTime(timestamp);
    expect(result).toContain("14");
    expect(result).toContain("2026");
    expect(result).toContain("10:30");
  });

  it("renders in an overridden zone", () => {
    const result = formatDateTime(timestamp, "UTC");
    expect(result).toContain("15");
    expect(result).toContain("4:30");
  });

  it("shows a dash for a missing timestamp", () => {
    expect(formatDateTime(null)).toBe("—");
  });

  it("falls back to the raw string for an unparseable timestamp", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});
