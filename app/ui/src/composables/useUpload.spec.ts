import { describe, expect, it, vi } from "vitest";
import { COLLECTION_ID_PATTERN, uploadChunked } from "@/composables/useUpload";

describe("COLLECTION_ID_PATTERN", () => {
  it("accepts short slugs and rejects paths", () => {
    expect(COLLECTION_ID_PATTERN.test("regular")).toBe(true);
    expect(COLLECTION_ID_PATTERN.test("holiday-2026")).toBe(true);
    expect(COLLECTION_ID_PATTERN.test("/media/regular")).toBe(false);
    expect(COLLECTION_ID_PATTERN.test("Regular")).toBe(false);
  });
});

describe("uploadChunked", () => {
  it("aborts the staged upload when a chunk fails", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        if (url.includes("/chunk")) return new Response("{}", { status: 500 });
        return new Response(JSON.stringify({ upload_id: "u1" }), { status: 200 });
      }),
    );

    const file = new File([new Uint8Array(16)], "clip.mp4");

    await expect(uploadChunked("clip", file, "regular", () => {})).rejects.toThrow();
    expect(calls.some((url) => url.includes("/abort"))).toBe(true);
    expect(calls.every((url) => !url.startsWith("/"))).toBe(true);
  });

  it("finishes the upload after every chunk lands", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return new Response(JSON.stringify({ upload_id: "u1" }), { status: 200 });
      }),
    );

    const file = new File([new Uint8Array(16)], "clip.mp4");
    await uploadChunked("clip", file, "regular", () => {});

    expect(calls.at(-1)).toBe("manager/uploads/u1/finish");
  });
});
