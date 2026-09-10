import { describe, expect, it } from "vitest";
import { deterministicClipCompare, moveId } from "@/composables/useOrder";
import type { Clip } from "@/types";

function clip(id: string, rank: number, output: string): Clip {
  return {
    id,
    collection_id: "regular",
    relative_source_path: `regular/${id}.mp4`,
    relative_output_path: output,
    output_available: Boolean(output),
    state: "compiled",
    duration_seconds: 10,
    sequential_rank: rank,
    tags: [],
    notes: "",
    failed_reason: null,
  };
}

describe("deterministicClipCompare", () => {
  it("orders by sequential rank first", () => {
    const rows = [clip("b", 1, "B.mp4"), clip("a", 0, "A.mp4")].sort(deterministicClipCompare);
    expect(rows.map((row) => row.id)).toEqual(["a", "b"]);
  });

  it("breaks rank ties by casefolded path, then path, then id", () => {
    const rows = [clip("b", 0, "b.mp4"), clip("a", 0, "A.mp4")].sort(deterministicClipCompare);
    expect(rows.map((row) => row.id)).toEqual(["a", "b"]);
  });

  it("prefers the compiled output path over the source path", () => {
    const first = clip("a", 0, "zzz.mp4");
    const second = clip("b", 0, "aaa.mp4");
    expect([first, second].sort(deterministicClipCompare).map((row) => row.id)).toEqual(["b", "a"]);
  });
});

describe("moveId", () => {
  it("moves an id down without losing the others", () => {
    expect(moveId(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
  });

  it("moves an id up", () => {
    expect(moveId(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
  });
});
