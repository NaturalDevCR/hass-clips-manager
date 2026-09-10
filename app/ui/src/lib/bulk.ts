import { mapLimit } from "@/composables/useApi";

// Four at a time keeps the Worker's queue responsive without serializing a
// selection of a few hundred clips.
export const BULK_CONCURRENCY = 4;

export interface BulkOutcome {
  ok: number;
  failures: string[];
}

export async function runBulk(
  ids: readonly string[],
  task: (id: string) => Promise<unknown>,
): Promise<BulkOutcome> {
  const results = await mapLimit(ids, BULK_CONCURRENCY, task);
  const failures: string[] = [];
  let ok = 0;
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      ok += 1;
      return;
    }
    const reason: unknown = result.reason;
    failures.push(`${ids[index]}: ${reason instanceof Error ? reason.message : String(reason)}`);
  });
  return { ok, failures };
}
