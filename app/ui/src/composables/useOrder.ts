import type { Clip } from "@/types";

// The Home Assistant bridge lives at the domain root, outside the Ingress
// prefix, so it is the one absolute URL this app builds. Every Worker route
// stays relative.
export const ORDER_BRIDGE_PATH = "/api/cinema_collections/order";
export const ORDER_BRIDGE_HEADER = "X-Cinema-Collections-Order-Capability";

export interface BridgeOrder {
  available: boolean;
  ids: string[];
  error?: string;
}

export function orderBridgeUrl(): URL {
  return new URL(ORDER_BRIDGE_PATH, window.location.origin);
}

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

/**
 * `available: false` also covers "the bridge answered but rejected the
 * collection", where saving makes no sense either.
 */
export async function bridgeGetOrder(collection: string, capability: string): Promise<BridgeOrder> {
  const url = orderBridgeUrl();
  url.searchParams.set("collection_id", collection);
  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json", [ORDER_BRIDGE_HEADER]: capability },
    });
    if (!response.ok) {
      let message = `Home Assistant bridge returned HTTP ${response.status}.`;
      try {
        const body: unknown = await response.json();
        if (body && typeof body === "object" && "message" in body) {
          const detail = (body as { message?: unknown }).message;
          if (typeof detail === "string") message = detail;
        }
      } catch {
        // The error response was not JSON; the generic message stands.
      }
      return { available: false, ids: [], error: message };
    }
    const body = (await response.json()) as {
      ordered_clip_ids?: unknown;
      playback_mode?: unknown;
    };
    const ids = Array.isArray(body.ordered_clip_ids)
      ? body.ordered_clip_ids.filter((id): id is string => typeof id === "string")
      : [];
    return { available: true, ids: body.playback_mode === "custom" ? ids : [] };
  } catch {
    return { available: false, ids: [] };
  }
}

export async function bridgeSaveOrder(
  collection: string,
  ids: readonly string[],
  capability: string,
): Promise<void> {
  const response = await fetch(orderBridgeUrl(), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      [ORDER_BRIDGE_HEADER]: capability,
    },
    body: JSON.stringify({ collection_id: collection, ordered_clip_ids: [...ids] }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
}
