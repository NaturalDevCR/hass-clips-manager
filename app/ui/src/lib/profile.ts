import type { LoudnessMode, ProfileSettings, QualityMode, ScalingStrategy } from "@/types";

/**
 * Switch a discriminated setting to another mode while keeping the values the
 * other mode used, so flipping back and forth does not silently reset fields.
 */
export function withQualityMode(quality: QualityMode, mode: QualityMode["mode"]): QualityMode {
  if (quality.mode === mode) return quality;
  if (mode === "crf") {
    return { mode: "crf", crf: quality.crf ?? 23, bitrate_kbps: quality.bitrate_kbps ?? null };
  }
  return { mode: "bitrate", bitrate_kbps: quality.bitrate_kbps ?? 8000, crf: quality.crf ?? null };
}

export function withScalingMode(
  scaling: ScalingStrategy,
  mode: ScalingStrategy["mode"],
): ScalingStrategy {
  if (scaling.mode === mode) return scaling;
  if (mode === "crop") return { mode: "crop", width: scaling.width, height: scaling.height };
  return {
    mode: "aspect_fit",
    width: scaling.width,
    height: scaling.height,
    sar_num: 1,
    sar_den: 1,
  };
}

export function withLoudnessMode(
  loudness: LoudnessMode,
  mode: LoudnessMode["mode"],
): LoudnessMode {
  if (loudness.mode === mode) return loudness;
  if (mode === "disabled") return { mode: "disabled", final_mix_normalization: false };
  return {
    mode: "two_pass",
    integrated_lufs: -18,
    true_peak_dbtp: -1.5,
    lra_lu: 11,
    final_mix_normalization: loudness.final_mix_normalization,
  };
}

/** A deep copy, so editing a draft never mutates the record it came from. */
export function cloneSettings(settings: ProfileSettings): ProfileSettings {
  return JSON.parse(JSON.stringify(settings)) as ProfileSettings;
}
