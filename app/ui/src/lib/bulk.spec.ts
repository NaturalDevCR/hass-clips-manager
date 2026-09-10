import { describe, expect, it } from "vitest";
import { runBulk } from "@/lib/bulk";

describe("runBulk", () => {
  it("reports successes and failures without aborting the batch", async () => {
    const result = await runBulk(["a", "b", "c"], async (id) => {
      if (id === "b") throw new Error("boom");
      return id;
    });

    expect(result.ok).toBe(2);
    expect(result.failures).toEqual(["b: boom"]);
  });

  it("never runs more than four calls at once", async () => {
    let inFlight = 0;
    let peak = 0;

    await runBulk(["1", "2", "3", "4", "5", "6", "7", "8"], async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
    });

    expect(peak).toBeLessThanOrEqual(4);
  });
});
