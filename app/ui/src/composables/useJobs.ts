import { computed, shallowRef } from "vue";
import { apiFetch } from "@/composables/useApi";
import type { Job } from "@/types";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

const jobs = shallowRef<Job[]>([]);
let timer: number | null = null;

export function jobIdFrom(details: unknown): string | null {
  if (!details || typeof details !== "object") return null;
  const record = details as Record<string, unknown>;
  if (typeof record.job_id === "string") return record.job_id;
  if (Array.isArray(record.job_ids) && typeof record.job_ids[0] === "string") {
    return record.job_ids[0];
  }
  return null;
}

export function useJobs() {
  const active = computed(() => jobs.value.filter((job) => !TERMINAL.has(job.state)));

  async function refresh(): Promise<void> {
    try {
      jobs.value = await apiFetch<Job[]>("manager/jobs");
    } catch {
      // A dropped poll is not worth interrupting the view over; the next tick retries.
    }
  }

  function start(): void {
    if (timer !== null) return;
    void refresh();
    timer = window.setInterval(() => void refresh(), 2000);
  }

  function stop(): void {
    if (timer === null) return;
    window.clearInterval(timer);
    timer = null;
  }

  /** Follow one job to a terminal state, reporting progress on every poll. */
  async function follow(jobId: string, onUpdate: (job: Job) => void): Promise<Job | null> {
    for (;;) {
      let job: Job;
      try {
        job = await apiFetch<Job>(`manager/jobs/${jobId}`);
      } catch {
        return null;
      }
      onUpdate(job);
      if (TERMINAL.has(job.state)) return job;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  return { jobs, active, refresh, start, stop, follow };
}
