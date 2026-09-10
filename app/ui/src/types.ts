export interface Clip {
  id: string;
  collection_id: string;
  relative_source_path: string;
  relative_output_path: string;
  output_available: boolean;
  state: string;
  duration_seconds: number;
  sequential_rank: number;
  tags: string[];
  notes: string;
  failed_reason: string | null;
}

export interface JobProgress {
  stage: string;
  percent: number;
  eta_seconds: number | null;
}

export interface Job {
  id: string;
  kind: string;
  state: string;
  created_at: string | null;
  finished_at: string | null;
  error: string | null;
  /** The clip a compile job acts on; null for jobs that act on no single clip. */
  clip_id?: string | null;
  /** What the job acts on: a clip's source path, or a scan's collections. */
  target?: string;
  progress?: JobProgress;
}

export interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  job_id: string | null;
}

export interface TrashEntry {
  id: string;
  clip_id: string;
  target: string;
  created_at: string;
}

export interface CollectionSummary {
  id: string;
  name: string;
}

export type PlaybackMode = "random" | "sequential" | "custom";

export interface Collection {
  id: string;
  name: string;
  enabled: boolean;
  priority: number;
  source_directory: string;
  compiled_output_prefix: string;
  processing_profile_id: string;
  is_default: boolean;
  allow_manual_override: boolean;
  tags: string[];
  notes: string | null;
  starts_at: string | null;
  ends_at: string | null;
  schedule: Record<string, unknown>;
  playback_mode: PlaybackMode;
  ordered_clip_ids: string[];
  revision: number;
}

export interface CrfQuality {
  mode: "crf";
  crf: number;
  bitrate_kbps: number | null;
}

export interface BitrateQuality {
  mode: "bitrate";
  bitrate_kbps: number;
  crf: number | null;
}

export type QualityMode = CrfQuality | BitrateQuality;

export interface AspectFitScaling {
  strategy: "aspect_fit";
  width: number;
  height: number;
  sar_num: number;
  sar_den: number;
}

export interface CropScaling {
  strategy: "crop";
  width: number;
  height: number;
}

export type ScalingStrategy = AspectFitScaling | CropScaling;

export interface VideoSettings {
  width: number;
  height: number;
  fps: number;
  codec: string;
  preset: string;
  quality: QualityMode;
  h264_profile: string;
  level: string;
  pixel_format: string;
  scaling: ScalingStrategy;
  fast_start: boolean;
  maxrate_kbps: number | null;
  bufsize_kbps: number | null;
  keyframe_interval_seconds: number | null;
}

export interface AudioSettings {
  codec: string;
  bitrate_kbps: number;
  channels: number;
  sample_rate: number;
  missing_policy: { mode: "required" | "silence" };
  fallback: "none" | "silence";
  pad_or_trim: boolean;
}

export interface TwoPassLoudness {
  mode: "two_pass";
  integrated_lufs: number;
  true_peak_dbtp: number;
  lra_lu: number;
  final_mix_normalization: boolean;
}

export interface DisabledLoudness {
  mode: "disabled";
  final_mix_normalization: boolean;
}

export type LoudnessMode = TwoPassLoudness | DisabledLoudness;

export interface ProfileSettings {
  profile_version: number;
  video: VideoSettings;
  audio: AudioSettings;
  loudness: LoudnessMode;
  transitions: {
    type: "fade";
    duration_seconds: number;
    from_segment: "intro" | "clip" | "outro";
    to_segment: "intro" | "clip" | "outro";
  }[];
  fade_in_seconds: number;
  fade_out_seconds: number;
  output: {
    container: "mp4" | "mkv" | "webm";
    extension: string;
    atomic_finalize: boolean;
    temporary_output: boolean;
  };
  hardware_acceleration: boolean;
  decode_error_policy: "warn" | "fail";
  intro_reference: string | null;
  outro_reference: string | null;
  timeout_seconds: number;
  timeout_seconds_per_minute: number;
  minimum_segment_duration_seconds: number | null;
}

export interface Profile {
  id: string;
  name: string;
  version: number;
  settings: ProfileSettings;
  revision: number;
}

export interface ActionResult {
  details?: Record<string, unknown>;
}
