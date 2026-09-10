import { describe, expect, it } from "vitest";
import { withLoudnessMode, withQualityMode, withScalingMode } from "@/lib/profile";
import type { LoudnessMode, QualityMode, ScalingStrategy } from "@/types";

describe("withQualityMode", () => {
  it("keeps the abandoned mode's value so switching back restores it", () => {
    const crf: QualityMode = { mode: "crf", crf: 19, bitrate_kbps: null };
    const bitrate = withQualityMode(crf, "bitrate");

    expect(bitrate.mode).toBe("bitrate");
    expect(withQualityMode(bitrate, "crf").crf).toBe(19);
  });

  it("returns the same object when the mode already matches", () => {
    const crf: QualityMode = { mode: "crf", crf: 23, bitrate_kbps: null };
    expect(withQualityMode(crf, "crf")).toBe(crf);
  });
});

describe("withScalingMode", () => {
  it("discriminates on strategy, the field the Worker actually stores", () => {
    const fit: ScalingStrategy = {
      strategy: "aspect_fit",
      width: 1920,
      height: 1080,
      sar_num: 1,
      sar_den: 1,
    };

    const cropped = withScalingMode(fit, "crop");

    expect(cropped).toEqual({ strategy: "crop", width: 1920, height: 1080 });
    expect("mode" in cropped).toBe(false);
  });

  it("returns the same object when the strategy already matches", () => {
    const crop: ScalingStrategy = { strategy: "crop", width: 1920, height: 1080 };
    expect(withScalingMode(crop, "crop")).toBe(crop);
  });
});

describe("withLoudnessMode", () => {
  it("turns analysis off without inventing targets", () => {
    const two: LoudnessMode = {
      mode: "two_pass",
      integrated_lufs: -18,
      true_peak_dbtp: -1.5,
      lra_lu: 11,
      final_mix_normalization: true,
    };
    expect(withLoudnessMode(two, "disabled")).toEqual({
      mode: "disabled",
      final_mix_normalization: false,
    });
  });
});
