import { apiFetch } from "@/composables/useApi";

// Fixed-size chunks keep every request well below reverse-proxy body limits
// (Cloudflare's 100 MB, for instance). The Worker stages the chunks and only
// publishes the file on finish.
export const UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;

export const COLLECTION_ID_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export const COLLECTION_ID_HINT =
  "That looks like a folder path, not a collection ID. Enter the collection's short ID " +
  '(e.g. "regular"), or leave it empty to scan everything.';

export async function uploadChunked(
  kind: "clip" | "asset",
  file: File,
  collection: string | null,
  onProgress: (fraction: number) => void,
): Promise<unknown> {
  const params = new URLSearchParams({ kind });
  if (collection) params.set("collection_id", collection);
  const begun = await apiFetch<{ upload_id: string }>(`manager/uploads?${params}`, {
    method: "POST",
    headers: { "X-Filename": file.name },
  });
  const uploadId = begun.upload_id;
  try {
    for (let offset = 0; offset < file.size; offset += UPLOAD_CHUNK_BYTES) {
      const chunk = file.slice(offset, offset + UPLOAD_CHUNK_BYTES);
      await apiFetch(`manager/uploads/${uploadId}/chunk`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: chunk,
      });
      onProgress(Math.min(1, (offset + chunk.size) / file.size));
    }
  } catch (error) {
    // Leave no staging file behind when a chunk fails.
    try {
      await apiFetch(`manager/uploads/${uploadId}/abort`, { method: "POST" });
    } catch {
      // Best-effort cleanup; the original failure is what the caller needs.
    }
    throw error;
  }
  return apiFetch(`manager/uploads/${uploadId}/finish`, { method: "POST" });
}
