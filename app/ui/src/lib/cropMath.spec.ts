import { describe, expect, it } from "vitest";
import { applyAspectRatio, clampToBounds, toSourceRect } from "@/lib/cropMath";

describe("toSourceRect", () => {
  it("scales a rectangle drawn on a shrunk preview up to the source video's pixels", () => {
    const result = toSourceRect(
      { x: 10, y: 10, width: 100, height: 50 },
      { width: 200, height: 100 },
      { width: 1920, height: 960 },
    );
    expect(result).toEqual({ x: 96, y: 96, width: 960, height: 480 });
  });

  it("rounds odd dimensions down to even, as libx264 requires", () => {
    const result = toSourceRect(
      { x: 0, y: 0, width: 101, height: 51 },
      { width: 200, height: 100 },
      { width: 200, height: 100 },
    );
    expect(result.width % 2).toBe(0);
    expect(result.height % 2).toBe(0);
  });
});

describe("clampToBounds", () => {
  it("pulls a rectangle back inside its container instead of letting it overflow", () => {
    const result = clampToBounds(
      { x: 150, y: 150, width: 100, height: 100 },
      { width: 200, height: 200 },
    );
    expect(result).toEqual({ x: 100, y: 100, width: 100, height: 100 });
  });
});

describe("applyAspectRatio", () => {
  it("keeps a rectangle that already fits its aspect ratio", () => {
    const result = applyAspectRatio(
      { x: 0, y: 0, width: 160, height: 200 },
      16 / 9,
      { width: 400, height: 400 },
    );
    expect(result.width).toBe(160);
    expect(result.height).toBeCloseTo(90);
  });

  it("shrinks height when the ratio would push the rectangle past the bottom edge", () => {
    const result = applyAspectRatio(
      { x: 0, y: 320, width: 160, height: 90 },
      16 / 9,
      { width: 400, height: 400 },
    );
    expect(result.height).toBeCloseTo(80);
    expect(result.width).toBeCloseTo(142.22, 1);
  });
});
