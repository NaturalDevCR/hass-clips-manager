import type { Clip } from "@/types";

/**
 * Mirror the integration's deterministic sequential order: rank first, then the
 * casefolded compiled-or-source path, then the raw path, then the clip id.
 */
export function deterministicClipCompare(a: Clip, b: Clip): number {
  if (a.sequential_rank !== b.sequential_rank) return a.sequential_rank - b.sequential_rank;
  const pathA = a.relative_output_path || a.relative_source_path;
  const pathB = b.relative_output_path || b.relative_source_path;
  const foldA = pathA.toLowerCase();
  const foldB = pathB.toLowerCase();
  if (foldA !== foldB) return foldA < foldB ? -1 : 1;
  if (pathA !== pathB) return pathA < pathB ? -1 : 1;
  if (a.id === b.id) return 0;
  return a.id < b.id ? -1 : 1;
}

export function moveId(ids: readonly string[], from: number, to: number): string[] {
  const next = [...ids];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}
