import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, mapLimit, setCsrfToken } from "@/composables/useApi";

describe("apiFetch", () => {
  beforeEach(() => setCsrfToken("token-1"));

  it("sends the CSRF header and returns the parsed body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("manager/clips")).resolves.toEqual({ ok: true });
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get("X-CSRF-Token")).toBe("token-1");
  });

  it("throws the server's message on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ message: "nope" }), { status: 422 })),
    );

    await expect(apiFetch("manager/clips")).rejects.toThrow("nope");
  });

  it("keeps request paths relative so the Ingress prefix survives", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("null", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("manager/jobs");

    expect(fetchMock.mock.calls[0][0]).toBe("manager/jobs");
  });
});

describe("mapLimit", () => {
  it("keeps every result in input order", async () => {
    const results = await mapLimit([1, 2, 3], 2, async (value) => value * 2);
    expect(results.map((result) => (result.status === "fulfilled" ? result.value : null))).toEqual([
      2, 4, 6,
    ]);
  });
});
