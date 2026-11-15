// Single-colour top-down silhouettes used with IconLayer { mask: true }. Nose points up (north).
const AIRLINER =
  "M64 3 C68.5 3 70.5 12 70.5 22 L70.5 48 L119 78 C121 79.3 121 82 119 83 L70.5 70 L70.5 98 L86 112 C87.4 113.2 87.6 115.4 86 116 L70.5 112 C69.6 120 67 125 64 125 C61 125 58.4 120 57.5 112 L42 116 C40.4 115.4 40.6 113.2 42 112 L57.5 98 L57.5 70 L9 83 C7 82 7 79.3 9 78 L57.5 48 L57.5 22 C57.5 12 59.5 3 64 3 Z " +
  "M30 64 h7 v14 h-7 Z M91 64 h7 v14 h-7 Z";

const VEHICLE = "M44 18 H84 C90 18 94 22 94 28 V100 C94 106 90 110 84 110 H44 C38 110 34 106 34 100 V28 C34 22 38 18 44 18 Z";

/** Atlas: left cell = airliner, right cell = vehicle. Both 128x128. */
export function buildAtlas(): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="128" viewBox="0 0 256 128">` +
    `<g fill="#fff"><path d="${AIRLINER}"/></g><g transform="translate(128 0)" fill="#fff"><path d="${VEHICLE}"/></g></svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

export const ICON_MAPPING = {
  plane: { x: 0, y: 0, width: 128, height: 128, mask: true, anchorY: 64 },
  vehicle: { x: 128, y: 0, width: 128, height: 128, mask: true, anchorY: 64 },
};
