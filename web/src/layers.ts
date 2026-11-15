import { IconLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import { buildAtlas, ICON_MAPPING } from "./icons";
import type { Store, Track } from "./store";
import type { Graph, LonLat, Runway } from "./types";

export type RGB = [number, number, number];
export const CYAN: RGB = [34, 211, 238];
export const AMBER: RGB = [245, 158, 11];
export const WHITE: RGB = [229, 231, 235];
export const GREEN: RGB = [34, 197, 94];
export const RED: RGB = [239, 68, 68];

export const kindColor = (k: string): RGB => (k === "arrival" ? CYAN : k === "departure" ? AMBER : WHITE);

const ADDITIVE: any = {
  blend: true,
  blendColorOperation: "add",
  blendColorSrcFactor: "src-alpha",
  blendColorDstFactor: "one",
  blendAlphaOperation: "add",
  blendAlphaSrcFactor: "one",
  blendAlphaDstFactor: "one",
};

interface Pavement {
  ref: string;
  rings: LonLat[][];
  centroidLat: number;
}
export interface StaticData {
  pavements: Pavement[];
  surfaces: { rings: LonLat[][] }[];
  taxiLines: { path: LonLat[] }[];
  runwayLines: { path: LonLat[] }[];
  gates: { name: string; pos: LonLat }[];
  twyLabels: { ref: string; pos: LonLat }[];
  bounds: [number, number, number, number];
  center: LonLat;
}

const RUNWAY_ORDER = ["08/26", "07L/25R", "07R/25L"];

export function prepareStatic(g: Graph | null): StaticData {
  const out: StaticData = {
    pavements: [], surfaces: [], taxiLines: [], runwayLines: [], gates: [], twyLabels: [],
    bounds: [-112.0298, 33.4233, -111.9891, 33.4432], center: [-112.0095, 33.4333],
  };
  if (!g) return out;
  if (g.meta) {
    out.bounds = g.meta.bounds;
    out.center = g.meta.center;
  }
  const byRef = new Map<string, { pos: LonLat; n: number }[]>();
  for (const f of g.features) {
    const k = f.properties.kind as string;
    const geom = f.geometry;
    if (k === "runway_pavement" && geom.type === "Polygon") {
      const ring: LonLat[] = geom.coordinates[0];
      const lat = ring.reduce((s, c) => s + c[1], 0) / ring.length;
      const ref = String(f.properties.ref ?? f.properties.id ?? f.properties.name ?? f.properties.runway ?? "");
      out.pavements.push({ ref, rings: geom.coordinates, centroidLat: lat });
    } else if ((k === "apron" || k === "taxiway_pavement") && geom.type === "Polygon") {
      out.surfaces.push({ rings: geom.coordinates });
    } else if (k === "runway" && geom.type === "LineString") {
      out.runwayLines.push({ path: geom.coordinates });
    } else if ((k === "taxiway" || k === "taxilane") && geom.type === "LineString") {
      out.taxiLines.push({ path: geom.coordinates });
      const ref = String(f.properties.ref ?? "");
      if (k === "taxiway" && /^[A-Z]{1,2}\d{0,2}$/.test(ref)) {
        const c: LonLat[] = geom.coordinates;
        const mid = c[Math.floor(c.length / 2)];
        const arr = byRef.get(ref) ?? [];
        arr.push({ pos: mid, n: c.length });
        byRef.set(ref, arr);
      }
    } else if (k === "stand" && geom.type === "Point" && f.properties.name) {
      out.gates.push({ name: String(f.properties.name), pos: geom.coordinates });
    }
  }
  for (const [ref, arr] of byRef) {
    // label the edge closest to the median position of the taxiway
    const mx = [...arr].sort((a, b) => a.pos[0] - b.pos[0])[Math.floor(arr.length / 2)].pos[0];
    const best = arr.reduce((a, b) => (Math.abs(b.pos[0] - mx) < Math.abs(a.pos[0] - mx) ? b : a));
    out.twyLabels.push({ ref, pos: best.pos });
  }
  // runway pavements that did not match a runway id are ranked north to south
  out.pavements.sort((a, b) => b.centroidLat - a.centroidLat);
  out.pavements.forEach((p, i) => {
    if (!RUNWAY_ORDER.includes(p.ref)) p.ref = RUNWAY_ORDER[i] ?? p.ref;
  });
  return out;
}

export interface FrameInput {
  store: Store;
  now: number;
  simT: number;
  view: { center: LonLat; zoom: number };
  quality: 0 | 1 | 2;
  data: StaticData;
  onPick: (id: string) => void;
}

const trackPos = (t: Track): [number, number, number] => [t.lon, t.lat, 0];
const fade = (rgb: RGB, a: number): [number, number, number, number] => [rgb[0], rgb[1], rgb[2], Math.round(a)];

let atlas: string | null = null;

let staticFor: StaticData | null = null;
let staticCache: any[] = [];
function staticLayers(data: StaticData): any[] {
  if (staticFor === data) return staticCache;
  staticFor = data;
  staticCache = [

    new PolygonLayer({
      id: "surfaces",
      data: data.surfaces,
      getPolygon: (d: { rings: LonLat[][] }) => d.rings,
      getFillColor: [70, 120, 190, 20],
      getLineColor: [120, 180, 255, 40],
      lineWidthUnits: "pixels",
      getLineWidth: 1,
      stroked: true,
      filled: true,
    }),
    new PathLayer({
      id: "taxi-network",
      data: data.taxiLines,
      getPath: (d: { path: LonLat[] }) => d.path,
      getColor: [140, 190, 240, 62],
      widthUnits: "pixels",
      getWidth: 1.2,
    }),
    new PathLayer({
      id: "runway-centerline",
      data: data.runwayLines,
      getPath: (d: { path: LonLat[] }) => d.path,
      getColor: [255, 255, 255, 110],
      widthUnits: "pixels",
      getWidth: 1.4,
      getDashArray: [10, 8],
      dashJustified: false,
      extensions: [new PathStyleExtension({ dash: true })],
    } as any),
  
  ];
  return staticCache;
}


export function buildLayers(f: FrameInput): any[] {
  const { store, data, quality, view } = f;
  const zoom = view.zoom;
  atlas ??= buildAtlas();
  const pulse = 0.5 + 0.5 * Math.sin((f.now / 1000) * Math.PI * 1.6);
  const fastPulse = 0.5 + 0.5 * Math.sin((f.now / 1000) * Math.PI * 3.4);
  const tracks = [...store.tracks.values()];
  const live = tracks.filter((t) => t.data.phase !== "parked");
  const selected = store.selected;
  const layers: any[] = [];

  layers.push(...staticLayers(data));

  // ---- runway pavement: status colour + animated glow
  const rwById = new Map<string, Runway>(store.runways.map((r) => [r.id, r]));
  const statusColor = (ref: string): { c: RGB; s: string } => {
    const s = rwById.get(ref)?.status ?? "free";
    return { c: s === "occupied" ? RED : s === "reserved" ? AMBER : GREEN, s };
  };
  const key = data.pavements.map((p) => statusColor(p.ref).s).join();
  const pv = (id: string, width: number, alpha: (s: string) => number, extra: any = {}) =>
    new PolygonLayer({
      id,
      data: data.pavements,
      getPolygon: (d: Pavement) => d.rings,
      filled: false,
      stroked: true,
      lineWidthUnits: "pixels",
      getLineWidth: width,
      getLineColor: (d: Pavement) => {
        const { c, s } = statusColor(d.ref);
        return fade(c, alpha(s));
      },
      updateTriggers: { getLineColor: [key, Math.round(pulse * 12), Math.round(fastPulse * 12)] },
      ...extra,
    } as any);
  layers.push(
    new PolygonLayer({
      id: "runway-fill",
      data: data.pavements,
      getPolygon: (d: Pavement) => d.rings,
      filled: true,
      stroked: false,
      getFillColor: (d: Pavement) => {
        const { c, s } = statusColor(d.ref);
        const p = s === "occupied" ? fastPulse : pulse;
        return fade(c, s === "free" ? 30 + p * 12 : 70 + p * 50);
      },
      updateTriggers: { getFillColor: [key, Math.round(pulse * 12), Math.round(fastPulse * 12)] },
    } as any),
  );
  if (quality < 1) layers.push(pv("runway-glow-wide", 16, (s) => (s === "free" ? 20 : 28 + (s === "occupied" ? fastPulse : pulse) * 34), { parameters: ADDITIVE }));
  layers.push(
    pv("runway-glow-mid", 7, (s) => (s === "free" ? 70 + pulse * 40 : 120 + (s === "occupied" ? fastPulse : pulse) * 80), { parameters: ADDITIVE }),
    pv("runway-edge", 1.6, () => 235),
  );

  // ---- taxi routes (glow + core). Cleared solid, planned dashed.
  const withHead = (t: Track, pts: LonLat[] | undefined, fromHead: boolean): LonLat[] => {
    if (!pts || pts.length < 2) return [];
    return fromHead ? [[t.lon, t.lat], ...pts.slice(1)] : pts;
  };
  const cleared: { path: LonLat[]; color: RGB; sel: boolean }[] = [];
  const planned: { path: LonLat[]; color: RGB; sel: boolean }[] = [];
  for (const t of live) {
    const d = t.data;
    if (d.phase === "airborne") continue;
    const color = kindColor(d.kind);
    const sel = d.id === selected;
    const r = withHead(t, d.route, true);
    if (r.length > 1) cleared.push({ path: r, color, sel });
    const pr = d.planned_route;
    if (pr && pr.length > 1) {
      const start = r.length > 1 ? [r[r.length - 1]] : [[t.lon, t.lat] as LonLat];
      planned.push({ path: [...start, ...pr.slice(1)], color, sel });
    }
  }
  if (quality < 2) {
    layers.push(
      new PathLayer({
        id: "route-glow",
        data: [...cleared, ...planned],
        getPath: (d: { path: LonLat[] }) => d.path,
        getColor: (d: { color: RGB; sel: boolean }) => fade(d.color, d.sel ? 70 : 46),
        widthUnits: "pixels",
        getWidth: quality ? 11 : 15,
        capRounded: true,
        jointRounded: true,
        parameters: ADDITIVE,
      } as any),
    );
  }
  layers.push(
    new PathLayer({
      id: "route-planned",
      data: planned,
      getPath: (d: { path: LonLat[] }) => d.path,
      getColor: (d: { color: RGB; sel: boolean }) => fade(d.color, d.sel ? 230 : 165),
      widthUnits: "pixels",
      getWidth: 2.4,
      getDashArray: [4, 3],
      dashJustified: true,
      extensions: [new PathStyleExtension({ dash: true })],
    } as any),
    new PathLayer({
      id: "route-cleared",
      data: cleared,
      getPath: (d: { path: LonLat[] }) => d.path,
      getColor: (d: { color: RGB; sel: boolean }) => fade(d.color, d.sel ? 255 : 225),
      widthUnits: "pixels",
      getWidth: 3,
      capRounded: true,
      jointRounded: true,
    } as any),
  );

  // ---- hold-short markers
  const holds = tracks.filter((t) => t.holdAlpha > 0.02 && t.holdPos);
  layers.push(
    new ScatterplotLayer({
      id: "hold-ring",
      data: holds,
      getPosition: (t: Track) => [t.holdPos![0], t.holdPos![1], 0],
      radiusUnits: "meters",
      getRadius: 16 + fastPulse * 5,
      radiusMinPixels: 8,
      stroked: true,
      filled: true,
      lineWidthUnits: "pixels",
      getLineWidth: 2.5,
      getFillColor: (t: Track) => fade(RED, t.holdAlpha * 70),
      getLineColor: (t: Track) => fade(RED, t.holdAlpha * 255),
      updateTriggers: { getRadius: Math.round(fastPulse * 10), getFillColor: holds.map((h) => h.holdAlpha.toFixed(1)).join() },
      parameters: ADDITIVE,
    } as any),
    new ScatterplotLayer({
      id: "hold-core",
      data: holds,
      getPosition: (t: Track) => [t.holdPos![0], t.holdPos![1], 0],
      radiusUnits: "meters",
      getRadius: 4,
      radiusMinPixels: 3,
      getFillColor: (t: Track) => fade([255, 140, 140], t.holdAlpha * 255),
      updateTriggers: { getFillColor: holds.map((h) => h.holdAlpha.toFixed(1)).join() },
    } as any),
  );

  // ---- trails
  if (quality < 2) {
    const trailLen = quality ? 12 : 25;
    const base = f.simT - 120;
    const trips = live
      .filter((t) => t.data.kind !== "vehicle" && t.trail.length > 1)
      .map((t) => ({
        color: kindColor(t.data.kind),
        path: [...t.trail.map((s) => s.p), [t.lon, t.lat] as LonLat],
        ts: [...t.trail.map((s) => s.t - base), f.simT - base],
      }));
    layers.push(
      new TripsLayer({
        id: "trails",
        data: trips,
        getPath: (d: (typeof trips)[number]) => d.path,
        getTimestamps: (d: (typeof trips)[number]) => d.ts,
        getColor: (d: (typeof trips)[number]) => d.color,
        currentTime: f.simT - base,
        trailLength: trailLen,
        fadeTrail: true,
        widthUnits: "pixels",
        getWidth: 4,
        widthMinPixels: 2,
        capRounded: true,
        jointRounded: true,
        parameters: ADDITIVE,
      } as any),
    );
  }

  // ---- aircraft icons
  const iconData = live.map((t) => ({
    id: t.data.id,
    pos: trackPos(t),
    angle: -t.hdg,
    size: (t.data.kind === "vehicle" ? 9 : Math.max(t.data.wingspan_m, 14)) * 4,
    color: kindColor(t.data.kind),
    icon: t.data.kind === "vehicle" ? "vehicle" : "plane",
    kind: t.data.kind,
  }));
  type Icon = (typeof iconData)[number];
  const iconProps = {
    iconAtlas: atlas,
    iconMapping: ICON_MAPPING,
    getIcon: (d: Icon) => d.icon,
    getPosition: (d: Icon) => d.pos,
    getAngle: (d: Icon) => d.angle,
    getSize: (d: Icon) => d.size,
    sizeUnits: "meters",
    sizeMinPixels: 14,
    sizeMaxPixels: 96,
  };
  if (quality < 2) {
    layers.push(
      new IconLayer({
        ...iconProps,
        id: "ac-shadow",
        data: iconData,
        getColor: [0, 0, 0, 120],
        getPixelOffset: [3, 4],
        updateTriggers: { getPosition: f.now, getAngle: f.now },
      } as any),
    );
  }
  if (selected) {
    const t = store.tracks.get(selected);
    if (t) {
      layers.push(
        new ScatterplotLayer({
          id: "selected-halo",
          data: [t],
          getPosition: trackPos,
          radiusUnits: "meters",
          getRadius: 70 + pulse * 26,
          radiusMinPixels: 26,
          stroked: true,
          filled: true,
          lineWidthUnits: "pixels",
          getLineWidth: 2.5,
          getFillColor: [255, 255, 255, 14 + pulse * 20],
          getLineColor: [255, 255, 255, 120 + pulse * 120],
          updateTriggers: { getPosition: f.now, getRadius: f.now, getFillColor: f.now, getLineColor: f.now },
          parameters: ADDITIVE,
        } as any),
      );
    }
  }
  layers.push(
    new IconLayer({
      ...iconProps,
      id: "aircraft",
      data: iconData,
      pickable: true,
      getColor: (d: Icon) => (d.id === selected ? [255, 255, 255, 255] : fade(d.color, 255)),
      onClick: (info: { object?: Icon }) => {
        if (info.object) f.onPick(info.object.id);
      },
      updateTriggers: { getPosition: f.now, getAngle: f.now, getColor: selected },
    } as any),
  );

  // ---- labels (greedy screen-space declutter so text never piles up)
  const mpx = (156543.03 * Math.cos((view.center[1] * Math.PI) / 180)) / 2 ** zoom;
  const toPx = (p: LonLat): [number, number] => [((p[0] - view.center[0]) * M_LON) / mpx, (-(p[1] - view.center[1]) * M_LAT) / mpx];
  const labelOffset = (t: Track) => Math.min(52, Math.max(16, ((t.data.wingspan_m || 30) * 4) / mpx / 2 + 8));
  const cand = tracks.filter((t) => t.data.id === selected || (t.data.kind !== "vehicle" && t.data.phase !== "parked"));
  const labelled = declutter(
    cand,
    (t) => {
      const [x, y] = toPx([t.lon, t.lat]);
      const w = (t.data.callsign.length + t.data.type.length + 2) * 7 + 12;
      return [x - w / 2, y + labelOffset(t), w, 20];
    },
    (t) => (t.data.id === selected ? 1000 : shown.has(t.data.id) ? 100 : 0) + t.data.speed_mps,
  );
  shown = new Set(labelled.map((t) => t.data.id));
  const textBase = {
    fontFamily: "Inter, system-ui, sans-serif",
    fontSettings: { sdf: true },
    getTextAnchor: "middle",
    getAlignmentBaseline: "top",
    billboard: true,
  };
  layers.push(
    new TextLayer({
      ...textBase,
      id: "labels",
      data: labelled,
      getPosition: trackPos,
      getText: (t: Track) => `${t.data.callsign}  ${t.data.type}`,
      getSize: 12,
      fontWeight: 600,
      getColor: (t: Track) => (t.data.id === selected ? [255, 255, 255, 255] : [235, 240, 248, 235]),
      getPixelOffset: (t: Track) => [0, labelOffset(t)],
      outlineWidth: 3,
      outlineColor: [4, 8, 16, 230],
      background: true,
      getBackgroundColor: [8, 12, 22, 150],
      backgroundPadding: [5, 2, 5, 2],
      updateTriggers: { getPosition: f.now, getPixelOffset: [zoom, f.now], getColor: selected },
    } as any),
  );
  if (zoom >= 16) {
    const gates = declutter(data.gates, (d) => { const [x, y] = toPx(d.pos); const w = d.name.length * 6.5 + 6; return [x - w / 2, y - 7, w, 14]; }, () => 0);
    const twy = declutter(data.twyLabels, (d) => { const [x, y] = toPx(d.pos); const w = d.ref.length * 10 + 12; return [x - w / 2, y - 10, w, 20]; }, () => 0);
    layers.push(
      new TextLayer({
        ...textBase,
        id: "gate-labels",
        data: gates,
        getPosition: (d: { pos: LonLat }) => d.pos,
        getText: (d: { name: string }) => d.name,
        getSize: 10.5,
        getColor: [190, 215, 255, 230],
        outlineWidth: 2,
        outlineColor: [4, 8, 16, 220],
        getAlignmentBaseline: "center",
        updateTriggers: { getText: gates.length },
      } as any),
      new TextLayer({
        ...textBase,
        id: "twy-labels",
        data: twy,
        getPosition: (d: { pos: LonLat }) => d.pos,
        getText: (d: { ref: string }) => d.ref,
        getSize: 14,
        fontWeight: 800,
        getColor: [12, 12, 12, 255],
        background: true,
        getBackgroundColor: [250, 204, 21, 235],
        backgroundPadding: [4, 1, 4, 1],
        getAlignmentBaseline: "center",
      } as any),
    );
  }
  return layers;
}

let shown = new Set<string>();
const M_LAT = 110574;
const M_LON = 111320 * Math.cos((33.435 * Math.PI) / 180);

/** Keep items in priority order, dropping any whose screen rectangle overlaps one already kept. */
function declutter<T>(items: T[], rect: (t: T) => [number, number, number, number], prio: (t: T) => number): T[] {
  const sorted = [...items].sort((a, b) => prio(b) - prio(a));
  const kept: T[] = [];
  const rects: [number, number, number, number][] = [];
  for (const it of sorted) {
    const r = rect(it);
    if (r[0] > 1200 || r[0] < -1200 || r[1] > 800 || r[1] < -800) continue; // off screen
    if (rects.some((k) => r[0] < k[0] + k[2] && r[0] + r[2] > k[0] && r[1] < k[1] + k[3] && r[1] + r[3] > k[1])) continue;
    kept.push(it);
    rects.push(r);
  }
  return kept;
}
