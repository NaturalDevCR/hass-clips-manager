let csrfToken = "";

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

/**
 * Call a Worker route. `path` is always relative: Home Assistant serves the App
 * under a per-install Ingress prefix, and a leading "/" would resolve against
 * the domain root instead, never reaching the Worker.
 */
export async function apiFetch<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const message =
      body && typeof body === "object" && "message" in body && typeof body.message === "string"
        ? body.message
        : `Request failed (HTTP ${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

/** Run `task` over `items` with at most `limit` calls in flight. */
export async function mapLimit<T, R>(
  items: readonly T[],
  limit: number,
  task: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results = new Array<PromiseSettledResult<R>>(items.length);
  let cursor = 0;
  async function worker(): Promise<void> {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) return;
      try {
        results[index] = { status: "fulfilled", value: await task(items[index]) };
      } catch (error) {
        results[index] = { status: "rejected", reason: error };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}
