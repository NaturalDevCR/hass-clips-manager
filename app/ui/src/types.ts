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

export interface ActionResult {
  details?: Record<string, unknown>;
}
