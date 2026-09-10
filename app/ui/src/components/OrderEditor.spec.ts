import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import OrderEditor from "@/components/OrderEditor.vue";
import type { Clip } from "@/types";

function clip(id: string, name: string, rank: number): Clip {
  return {
    id,
    collection_id: "halloween",
    relative_source_path: `halloween/${name}`,
    relative_output_path: `halloween/${id}.mp4`,
    output_available: true,
    state: "ready",
    duration_seconds: 60,
    sequential_rank: rank,
    tags: [],
    notes: "",
    failed_reason: null,
  };
}

const CLIPS = [clip("a", "alpha.mp4", 0), clip("b", "beta.mp4", 1), clip("c", "gamma.mp4", 2)];

describe("OrderEditor", () => {
  it("reorders with the arrow keys from a focused handle", async () => {
    const wrapper = mount(OrderEditor, {
      props: { clips: CLIPS, modelValue: ["a", "b", "c"] },
    });

    await wrapper.findAll(".order-handle")[0].trigger("keydown", { key: "ArrowDown" });

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([["b", "a", "c"]]);
  });

  it("ignores an arrow key that would move past the ends", async () => {
    const wrapper = mount(OrderEditor, {
      props: { clips: CLIPS, modelValue: ["a", "b", "c"] },
    });

    await wrapper.findAll(".order-handle")[0].trigger("keydown", { key: "ArrowUp" });

    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("still lists an id the catalog no longer holds", () => {
    const wrapper = mount(OrderEditor, {
      props: { clips: CLIPS, modelValue: ["a", "gone"] },
    });

    expect(wrapper.text()).toContain("Not in the current catalog");
  });

  it("read-only mode shows the order without offering to change it", () => {
    const wrapper = mount(OrderEditor, {
      props: { clips: CLIPS, modelValue: ["a", "b", "c"], readonly: true },
    });

    expect(wrapper.findAll(".order-handle")).toHaveLength(0);
    expect(wrapper.findAll("button")).toHaveLength(0);
    expect(wrapper.text()).toContain("calculated rather than arranged");
    expect(wrapper.text()).toContain("alpha.mp4");
  });
});
