import type { Clip, Job } from "@/types";

export const DEFAULT_TIME_ZONE = "America/Costa_Rica";

export function formatDateTime(iso: string | null, timeZone: string = DEFAULT_TIME_ZONE): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("es-CR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone,
  }).format(date);
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "—";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function sourceName(clip: Clip): string {
  const path = clip.relative_source_path;
  return path.split("/").pop() || path;
}

export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Non-secure contexts withhold the Clipboard API; fall back to a selection.
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    if (!document.execCommand("copy")) throw new Error("copy command rejected");
  } finally {
    area.remove();
  }
}

/**
 * Label what a job acts on. A compile job's target is a source path, so only its
 * filename is shown; a scan reports its collections, and an empty target means
 * it covered the whole library.
 */
export function jobTarget(job: Job): string {
  const target = job.target ?? "";
  if (!target) return job.kind === "scan" ? "every collection" : "";
  if (job.kind === "compile") return target.split("/").pop() || target;
  return target;
}
