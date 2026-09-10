/**
 * Choices and plain-language help for the processing profile.
 *
 * The Worker passes these values to FFmpeg without an allowlist, so offering a
 * closed set is safer than free text. The three fields that accept anything
 * FFmpeg does keep a text input backed by a datalist of the usual values.
 */

export interface Choice {
  value: string;
  label: string;
}

function plain(values: readonly string[]): Choice[] {
  return values.map((value) => ({ value, label: value }));
}

export const VIDEO_CODECS: Choice[] = [
  { value: "libx264", label: "libx264 — H.264, plays everywhere" },
  { value: "libx265", label: "libx265 — HEVC, smaller files, fussier players" },
];

export const VIDEO_PRESETS: Choice[] = plain([
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

export const H264_PROFILES: Choice[] = plain([
  "baseline",
  "main",
  "high",
  "high10",
  "high422",
  "high444",
]);

export const H264_LEVELS: Choice[] = plain([
  "3.0",
  "3.1",
  "4.0",
  "4.1",
  "4.2",
  "5.0",
  "5.1",
  "5.2",
  "6.0",
  "6.1",
  "6.2",
]);

export const PIXEL_FORMATS: Choice[] = plain(["yuv420p", "yuv422p", "yuv444p", "yuv420p10le"]);

export const AUDIO_CODECS: Choice[] = plain(["aac", "libopus", "libmp3lame", "flac"]);

export const AUDIO_CHANNELS: Choice[] = [
  { value: "1", label: "1 — mono" },
  { value: "2", label: "2 — stereo" },
  { value: "6", label: "6 — 5.1" },
  { value: "8", label: "8 — 7.1" },
];

export const SAMPLE_RATES: Choice[] = plain(["44100", "48000", "96000"]);

export const CONTAINERS: Choice[] = [
  { value: "mp4", label: "mp4 — the safe default" },
  { value: "mkv", label: "mkv — permissive, less portable" },
  { value: "webm", label: "webm — VP8/VP9 and Opus only" },
];

/** One line per field, saying what it changes rather than repeating its name. */
export const HELP = {
  width: "Output frame width. Sources are scaled to it, never stretched past it.",
  height: "Output frame height. With aspect fit, the source is padded to reach it.",
  fps: "Output frame rate. Sources at another rate are resampled to it.",
  codec: "The video encoder. libx264 is the compatible choice for TVs and projectors.",
  preset:
    "How hard the encoder works. Slower presets give a smaller file at the same quality and take longer to compile.",
  pixel_format:
    "Chroma layout and bit depth. yuv420p is what almost every player expects; the others trade compatibility for fidelity.",
  h264_profile:
    "The feature set the encoder may use. Older hardware often needs main or baseline.",
  level:
    "Declares the decoding load a player must handle. Too low for the resolution and frame rate makes strict players refuse the file.",
  quality:
    "Constant quality re-encodes each clip to a fixed look and lets the size vary. A target bitrate does the opposite.",
  crf: "Lower is better quality and a larger file. 18 is near-transparent, 23 is a good default, 28 is visibly soft.",
  bitrate: "The bitrate the encoder aims for, in kbps.",
  maxrate: "Caps the momentary bitrate. Set it with a buffer size when a player or network has a hard ceiling.",
  bufsize: "How much the encoder may bank against the cap. Roughly twice the cap is a common starting point.",
  keyframe:
    "Seconds between keyframes. Shorter seeks faster and costs a little size; leave it empty for the encoder's own choice.",
  fast_start:
    "Moves the index to the front of the file so playback can begin before the whole file is read.",
  scaling:
    "Fit keeps the whole frame and pads what is left over. Crop fills the frame and cuts what does not fit.",
  sar: "Pixel aspect ratio. Leave at 1:1 unless the source is genuinely anamorphic.",
  audio_codec: "The audio encoder. aac is the compatible choice.",
  audio_bitrate: "Audio bitrate in kbps. 192 is comfortable for stereo.",
  channels: "Channel count of the output. Sources are mixed to it.",
  sample_rate: "Output sample rate in Hz. 48000 is standard for video.",
  missing_policy:
    "What to do with a clip that has no audio track: refuse it, or give it silence so it still compiles.",
  fallback: "The track substituted when audio is missing and the clip is not refused.",
  pad_or_trim: "Pads or trims audio to match the video length exactly, so segments do not drift.",
  loudness:
    "Two-pass measures each clip and normalizes it so nothing jumps in volume between clips. Disabled leaves levels as they came.",
  integrated_lufs:
    "Target average loudness. −18 suits a room; broadcast usually asks for −23.",
  true_peak: "Ceiling for the loudest instant, in dBTP. −1.5 leaves room for lossy encoders.",
  lra: "How much loudness variation to preserve. Lower is more even and less dynamic.",
  intro: "Plays before every clip in a collection using this profile.",
  outro: "Plays after every clip.",
  fade_in: "Fade up from black at the start of the compiled clip.",
  fade_out: "Fade to black at the end.",
  container: "The output file format.",
  decode_error_policy:
    "What a damaged source does: warn and keep the clip, or fail the job so nothing broken is published.",
  timeout: "Base seconds a compile may take before the Worker gives up.",
  timeout_per_minute: "Extra seconds allowed per minute of source, added to the base.",
} as const;
