import { describe, expect, it } from "vitest";
import { jobTarget } from "@/lib/format";
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
