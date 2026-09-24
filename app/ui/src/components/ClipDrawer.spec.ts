import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import ClipDrawer from "./ClipDrawer.vue";
import type { Clip } from "@/types";

it("keeps metadata drafts while compiled duration is recovered", async () => {
  const clip: Clip = {
    id: "clip-id", collection_id: "films", relative_source_path: "films/movie.mp4",
    relative_output_path: "films/output.mp4", output_available: true, state: "ready",
    duration_seconds: 130, output_duration_seconds: null, sequential_rank: 0,
    tags: [], notes: "", failed_reason: null,
  };
  const wrapper = mount(ClipDrawer, { props: { clip } });
  await wrapper.get("textarea").setValue("My unsaved note");
  await wrapper.setProps({ clip: { ...clip, output_duration_seconds: 144 } });
  expect((wrapper.get("textarea").element as HTMLTextAreaElement).value).toBe("My unsaved note");
  wrapper.unmount();
});
