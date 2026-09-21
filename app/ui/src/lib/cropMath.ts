export interface CropRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Size {
  width: number;
  height: number;
}

/** libx264 requires even width/height for 4:2:0 chroma subsampling. */
export function roundToEven(rect: CropRect): CropRect {
  return {
    x: rect.x,
    y: rect.y,
    width: rect.width - (rect.width % 2),
    height: rect.height - (rect.height % 2),
  };
}

/** Convert a crop rectangle drawn in on-screen pixels to source-video pixels. */
export function toSourceRect(displayRect: CropRect, displaySize: Size, naturalSize: Size): CropRect {
  const scaleX = naturalSize.width / displaySize.width;
  const scaleY = naturalSize.height / displaySize.height;
  return roundToEven({
    x: Math.round(displayRect.x * scaleX),
    y: Math.round(displayRect.y * scaleY),
    width: Math.round(displayRect.width * scaleX),
    height: Math.round(displayRect.height * scaleY),
  });
}

/** Clamp a rectangle so it never extends past its containing size. */
export function clampToBounds(rect: CropRect, bounds: Size): CropRect {
  const width = Math.min(rect.width, bounds.width);
  const height = Math.min(rect.height, bounds.height);
  const x = Math.min(Math.max(rect.x, 0), bounds.width - width);
  const y = Math.min(Math.max(rect.y, 0), bounds.height - height);
  return { x, y, width, height };
}

/**
 * Resize a rectangle to a width/height ratio, anchored at its top-left,
 * shrinking as needed to stay inside `bounds`.
 */
export function applyAspectRatio(rect: CropRect, ratio: number, bounds: Size): CropRect {
  let width = rect.width;
  let height = width / ratio;
  if (height > bounds.height - rect.y) {
    height = bounds.height - rect.y;
    width = height * ratio;
  }
  if (rect.x + width > bounds.width) {
    width = bounds.width - rect.x;
    height = width / ratio;
  }
  return clampToBounds({ x: rect.x, y: rect.y, width, height }, bounds);
}
