import { beforeEach, describe, expect, it } from "vitest";
import { useClips } from "@/composables/useClips";
import type { Clip } from "@/types";

function clip(overrides: Partial<Clip>): Clip {
  return {
    id: "a",
    collection_id: "regular",
    relative_source_path: "regular/one.mp4",
    relative_output_path: "",
    output_available: false,
    state: "catalogued",
    duration_seconds: 30,
    sequential_rank: 0,
    tags: [],
    notes: "",
    failed_reason: null,
    ...overrides,
  };
}

const store = useClips();

describe("useClips", () => {
  beforeEach(() => {
    store.clips.value = [
      clip({ id: "a", relative_source_path: "regular/alpha.mp4", duration_seconds: 30 }),
      clip({
        id: "b",
        relative_source_path: "regular/beta.mp4",
        state: "failed",
        duration_seconds: 90,
      }),
      clip({
        id: "c",
        collection_id: "holiday",
        relative_source_path: "holiday/gamma.mp4",
        duration_seconds: 10,
      }),
    ];
    store.query.value = "";
    store.collectionFilter.value = "";
    store.stateFilter.value = "";
    store.sortKey.value = "name";
    store.clearSelection();
  });

  it("filters by filename fragment, case-insensitively", () => {
    store.query.value = "BET";
    expect(store.filtered.value.map((entry) => entry.id)).toEqual(["b"]);
  });

  it("filters by collection and state together", () => {
    store.collectionFilter.value = "regular";
    store.stateFilter.value = "failed";
    expect(store.filtered.value.map((entry) => entry.id)).toEqual(["b"]);
  });

  it("sorts by duration when asked", () => {
    store.sortKey.value = "duration";
    expect(store.filtered.value.map((entry) => entry.id)).toEqual(["c", "a", "b"]);
  });

  it("keeps a selection that the active filter hides", () => {
    store.toggle("a");
    store.toggle("b");
    store.query.value = "alpha";
    expect([...store.selected.value].sort()).toEqual(["a", "b"]);
  });

  it("lists the states present in the catalog", () => {
    expect(store.states.value).toEqual(["catalogued", "failed"]);
  });
});
