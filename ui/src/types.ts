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
