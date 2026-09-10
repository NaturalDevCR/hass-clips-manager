import { describe, expect, it, vi } from "vitest";
import { useCollections } from "@/composables/useCollections";

function stubFetch(): { url: string; options: RequestInit }[] {
  const calls: { url: string; options: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      calls.push({ url, options });
      return new Response("[]", { status: 200 });
    }),
  );
  return calls;
}

describe("useCollections", () => {
  it("posts a new collection and reloads the lists", async () => {
    const calls = stubFetch();
    await useCollections().createCollection({ id: "films", name: "Films" });

    expect(calls[0].url).toBe("manager/collections");
    expect(JSON.parse(String(calls[0].options.body)).id).toBe("films");
    expect(calls.slice(1).map((call) => call.url)).toEqual([
      "manager/collections",
      "manager/profiles",
    ]);
  });

  it("sends the revision a patch was built from", async () => {
    const calls = stubFetch();
    await useCollections().patchCollection("films", 3, { priority: 5 });

    const headers = new Headers(calls[0].options.headers);
    expect(headers.get("If-Match-Revision")).toBe("3");
  });

  it("keeps every request relative so the Ingress prefix survives", async () => {
    const calls = stubFetch();
    await useCollections().patchProfile("default", 1, { name: "Renamed" });

    expect(calls.every((call) => !call.url.startsWith("/"))).toBe(true);
  });
});
