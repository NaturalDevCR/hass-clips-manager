import { describe, expect, it } from "vitest";
import {
  AUDIO_CHANNELS,
  H264_LEVELS,
  HELP,
  PIXEL_FORMATS,
  VIDEO_CODECS,
  VIDEO_PRESETS,
} from "@/lib/profileOptions";

describe("profile options", () => {
  it("offers the presets FFmpeg accepts, in speed order", () => {
    expect(VIDEO_PRESETS.map((choice) => choice.value)).toEqual([
      "ultrafast",
      "superfast",
      "veryfast",
      "faster",
      "fast",
      "medium",
      "slow",
      "slower",
      "veryslow",
      "placebo",
    ]);
  });

  it("labels a choice without losing the value the Worker stores", () => {
    const codec = VIDEO_CODECS.find((choice) => choice.value === "libx264");
    expect(codec?.label).toContain("libx264");
    expect(AUDIO_CHANNELS.find((choice) => choice.value === "2")?.label).toContain("stereo");
  });

  it("keeps the compatible defaults in the offered sets", () => {
    expect(PIXEL_FORMATS.map((choice) => choice.value)).toContain("yuv420p");
    expect(H264_LEVELS.map((choice) => choice.value)).toContain("5.1");
  });

  it("explains every field it names", () => {
    for (const [field, text] of Object.entries(HELP)) {
      expect(text.length, field).toBeGreaterThan(20);
    }
  });
});
