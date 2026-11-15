// Mock KPHX backend implementing docs/API_CONTRACT.md so the dashboard can be built without the real API.
// Usage: node mock/server.mjs [port]   (default 8000)
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");
const PORT = Number(process.argv[2] || process.env.PORT || 8000);

// ---------------------------------------------------------------- geometry helpers
const LAT0 = 33.435;
const M_LAT = 110574;
const M_LON = 111320 * Math.cos((LAT0 * Math.PI) / 180);
const dist = (a, b) => Math.hypot((b[0] - a[0]) * M_LON, (b[1] - a[1]) * M_LAT);
const key = (c) => `${c[0].toFixed(6)},${c[1].toFixed(6)}`;
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const rnd = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const r6 = (x) => Math.round(x * 1e6) / 1e6;
const angDiff = (a, b) => ((b - a + 540) % 360) - 180;
const bearing = (a, b) => ((Math.atan2((b[0] - a[0]) * M_LON, (b[1] - a[1]) * M_LAT) * 180) / Math.PI + 360) % 360;
const offsetPt = (c, brg, m) => [
  c[0] + (Math.sin((brg * Math.PI) / 180) * m) / M_LON,
  c[1] + (Math.cos((brg * Math.PI) / 180) * m) / M_LAT,
];

// ---------------------------------------------------------------- load geometry
function readJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function loadGeometry() {
  const processed = path.join(ROOT, "data/processed/kphx_graph.geojson");
  if (fs.existsSync(processed) && !process.env.MOCK_FORCE_OSM) {
    const gj = readJson(processed);
    const ways = [];
    const stands = [];
    for (const f of gj.features) {
      const k = f.properties?.kind;
      if (f.geometry.type === "LineString" && ["taxiway", "taxilane", "runway"].includes(k)) {
        ways.push({ ref: String(f.properties.ref ?? ""), kind: k, coords: f.geometry.coordinates });
      } else if (k === "stand") {
        stands.push({ name: f.properties.name, coord: f.geometry.coordinates });
      }
    }
    if (ways.length > 20) {
      console.log(`[mock] using ${processed} (${ways.length} ways, ${stands.length} stands)`);
      return { geojson: gj, ways, stands, source: "processed" };
    }
  }
  const osm = readJson(path.join(ROOT, "data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json"));
  const ways = [];
  const feats = [];
  const standsRaw = [];
  let n = 0;
  for (const e of osm.elements) {
    const a = e.tags?.aeroway;
    if (e.type === "way" && ["taxiway", "taxilane", "runway"].includes(a) && e.geometry) {
      const coords = e.geometry.map((p) => [p.lon, p.lat]);
      const ref = e.tags.ref ?? "";
      ways.push({ ref, kind: a, coords });
      feats.push({
        type: "Feature",
        properties: { kind: a, ref, id: `w${e.id}` },
        geometry: { type: "LineString", coordinates: coords },
      });
    } else if (e.type === "node" && a === "gate") {
      standsRaw.push({ name: `${e.tags.ref ?? "G"}${++n}`, coord: [e.lon, e.lat] });
    }
  }
  const poly = (f, kind, props) => ({ type: "Feature", properties: { kind, ...props }, geometry: f.geometry });
  const rw = readJson(path.join(ROOT, "data/kphx_geometry/faa_arcgis_Runways/runways_phx_bbox.geojson"));
  for (const f of rw.features) {
    if (f.properties.LENGTH < 5000) continue;
    const xs = f.geometry.coordinates[0].map((c) => c[0]);
    const ys = f.geometry.coordinates[0].map((c) => c[1]);
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const ref = cy > 33.439 ? "08/26" : cy > 33.4300 ? "07L/25R" : "07R/25L";
    void xs;
    feats.push(poly(f, "runway_pavement", { ref, name: ref, id: ref }));
  }
  for (const file of ["faa_arcgis_AM_Taxiway/AM_Taxiway_phx_bbox.geojson", "faa_arcgis_AM_Apron/AM_Apron_phx_bbox.geojson"]) {
    for (const f of readJson(path.join(ROOT, "data/kphx_geometry", file)).features) feats.push(poly(f, "apron", {}));
  }
  const geojson = { type: "FeatureCollection", features: feats };
  return { geojson, ways, stands: standsRaw, source: "osm+faa" };
}

const geo = loadGeometry();

// ---------------------------------------------------------------- routing graph
const nodes = new Map(); // key -> {k, c, adj:[{to,len,ref,kind}]}
function node(c) {
  const k = key(c);
  let n = nodes.get(k);
  if (!n) nodes.set(k, (n = { k, c, adj: [] }));
  return n;
}
for (const w of geo.ways) {
  for (let i = 0; i + 1 < w.coords.length; i++) {
    const a = node(w.coords[i]);
    const b = node(w.coords[i + 1]);
    if (a === b) continue;
    const len = dist(a.c, b.c);
    a.adj.push({ to: b, len, ref: w.ref, kind: w.kind });
    b.adj.push({ to: a, len, ref: w.ref, kind: w.kind });
  }
}
// largest connected component
const comp = new Map();
let best = [];
for (const n of nodes.values()) {
  if (comp.has(n.k)) continue;
  const list = [];
  const stack = [n];
  comp.set(n.k, n);
  while (stack.length) {
    const x = stack.pop();
    list.push(x);
    for (const e of x.adj) if (!comp.has(e.to.k)) (comp.set(e.to.k, e.to), stack.push(e.to));
  }
  if (list.length > best.length) best = list;
}
const main = new Set(best.map((n) => n.k));
const taxiKeys = new Set();
for (const w of geo.ways) if (w.kind !== "runway") for (const c of w.coords) taxiKeys.add(key(c));

function dijkstra(from, to) {
  const d = new Map([[from.k, 0]]);
  const prev = new Map();
  const open = [[0, from]];
  while (open.length) {
    open.sort((a, b) => a[0] - b[0]);
    const [dc, n] = open.shift();
    if (n === to) break;
    if (dc > (d.get(n.k) ?? Infinity)) continue;
    for (const e of n.adj) {
      const cost = e.len * (e.kind === "runway" ? 6 : 1);
      const nd = dc + cost;
      if (nd < (d.get(e.to.k) ?? Infinity)) {
        d.set(e.to.k, nd);
        prev.set(e.to.k, { n, e });
        open.push([nd, e.to]);
      }
    }
  }
  if (!prev.has(to.k) && from !== to) return null;
  const out = [];
  let cur = to;
  while (cur !== from) {
    const p = prev.get(cur.k);
    out.push({ n: cur, ref: p.e.ref });
    cur = p.n;
  }
  out.push({ n: from, ref: out.length ? out[out.length - 1].ref : "" });
  return out.reverse();
}

function nearestNode(c, filter) {
  let bestN = null;
  let bd = Infinity;
  for (const n of best) {
    if (filter && !filter(n)) continue;
    const dd = dist(n.c, c);
    if (dd < bd) (bd = dd, bestN = n);
  }
  return { n: bestN, d: bd };
}

// runways: polyline west -> east merged by ref, then reversed for west flow (landing/takeoff toward the west)
const runways = {};
{
  const byRef = {};
  for (const w of geo.ways) if (w.kind === "runway") (byRef[w.ref] ||= []).push(w.coords);
  for (const [ref, parts] of Object.entries(byRef)) {
    const oriented = parts.map((p) => (p[0][0] > p[p.length - 1][0] ? [...p].reverse() : p));
    oriented.sort((a, b) => a[0][0] - b[0][0]);
    const coords = [];
    for (const p of oriented) for (const c of p) if (!coords.length || key(coords[coords.length - 1]) !== key(c)) coords.push(c);
    const east = [...coords].reverse();
    const cum = [0];
    for (let i = 1; i < east.length; i++) cum.push(cum[i - 1] + dist(east[i - 1], east[i]));
    runways[ref] = { ref, pts: east, cum, length: cum[cum.length - 1] };
  }
}
const RWY_NAME = { "08/26": "26", "07L/25R": "25 right", "07R/25L": "25 left" };
const RWY_SHORT = { "08/26": "26", "07L/25R": "25R", "07R/25L": "25L" };
const ARR_RWY = ["08/26", "07L/25R"].filter((r) => runways[r]);
const DEP_RWY = ["07R/25L", "07L/25R"].filter((r) => runways[r]);

// stands: gates snapped to the taxi network
const stands = [];
for (const s of geo.stands) {
  const { n, d } = nearestNode(s.coord, (x) => taxiKeys.has(x.k));
  if (n && d < 140) stands.push({ name: s.name, coord: s.coord, node: n });
}
// thin to ~36 stands spread out
const standList = stands.filter((_, i) => i % Math.max(1, Math.floor(stands.length / 36)) === 0);
console.log(`[mock] graph nodes=${best.length} runways=${Object.keys(runways).join(",")} stands=${standList.length}`);

// ---------------------------------------------------------------- GET /graph payload
function graphPayload() {
  const gj = JSON.parse(JSON.stringify(geo.geojson));
  if (geo.source === "osm+faa") {
    for (const s of standList) gj.features.push({ type: "Feature", properties: { kind: "stand", name: s.name }, geometry: { type: "Point", coordinates: s.coord } });
    for (const r of Object.values(runways)) {
      const h = departureSetup(r.ref);
      if (h) gj.features.push({ type: "Feature", properties: { kind: "hold", runway: r.ref }, geometry: { type: "Point", coordinates: h.holdPt } });
    }
  }
  if (!gj.meta) {
    let w = 180, s = 90, e = -180, n = -90;
    for (const way of geo.ways) for (const c of way.coords) (w = Math.min(w, c[0]), e = Math.max(e, c[0]), s = Math.min(s, c[1]), n = Math.max(n, c[1]));
    gj.meta = { center: [(w + e) / 2, (s + n) / 2], bounds: [w, s, e, n] };
  }
  return gj;
}

// ---------------------------------------------------------------- path helpers
function makePath(pts) {
  const out = [pts[0]];
  for (const p of pts.slice(1)) if (dist(out[out.length - 1], p) > 0.5) out.push(p);
  const cum = [0];
  for (let i = 1; i < out.length; i++) cum.push(cum[i - 1] + dist(out[i - 1], out[i]));
  // slow zones at sharp corners
  const slow = [];
  for (let i = 1; i + 1 < out.length; i++) {
    const turn = Math.abs(angDiff(bearing(out[i - 1], out[i]), bearing(out[i], out[i + 1])));
    if (turn > 28) slow.push([cum[i], turn > 70 ? 4.5 : 7]);
  }
  return { pts: out, cum, total: cum[cum.length - 1], slow };
}
function posAt(P, s) {
  s = clamp(s, 0, P.total);
  let lo = 0;
  let hi = P.cum.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (P.cum[mid] <= s) lo = mid;
    else hi = mid;
  }
  const seg = P.cum[hi] - P.cum[lo] || 1;
  const f = (s - P.cum[lo]) / seg;
  return [P.pts[lo][0] + (P.pts[hi][0] - P.pts[lo][0]) * f, P.pts[lo][1] + (P.pts[hi][1] - P.pts[lo][1]) * f];
}
function tangentAt(P, s) {
  const a = Math.max(0, Math.min(s, P.total - 12));
  return bearing(posAt(P, a), posAt(P, a + 12));
}
function slicePath(P, s0, s1) {
  s0 = clamp(s0, 0, P.total);
  s1 = clamp(s1, 0, P.total);
  if (s1 - s0 < 2) return [];
  const out = [posAt(P, s0)];
  for (let i = 0; i < P.cum.length; i++) if (P.cum[i] > s0 && P.cum[i] < s1) out.push(P.pts[i]);
  out.push(posAt(P, s1));
  return out.map((c) => [r6(c[0]), r6(c[1])]);
}
function slowLimit(P, s) {
  let v = Infinity;
  for (const [cs, lim] of P.slow) {
    const d = cs - s;
    if (d > -20 && d < 60) v = Math.min(v, lim + Math.max(0, d) * 0.12);
  }
  return v;
}

// ---------------------------------------------------------------- runway entry / exit selection
function runwayIndexShared(r) {
  const idx = [];
  r.pts.forEach((c, i) => {
    if (taxiKeys.has(key(c)) && main.has(key(c))) idx.push(i);
  });
  return idx;
}
const setupCache = {};
function departureSetup(ref) {
  if (setupCache[ref] !== undefined) return setupCache[ref];
  const r = runways[ref];
  const idx = runwayIndexShared(r).filter((i) => r.cum[i] < 900);
  if (!r || !idx.length) return (setupCache[ref] = null);
  const i = idx[0];
  return (setupCache[ref] = { entry: nodes.get(key(r.pts[i])), idx: i, holdPt: null, r });
}
function arrivalExits(ref) {
  const r = runways[ref];
  return runwayIndexShared(r).filter((i) => r.cum[i] > 900 && r.cum[i] < 2100);
}
for (const ref of Object.keys(runways)) {
  const d = departureSetup(ref);
  if (d) {
    const taxi = d.entry.adj.find((e) => e.kind !== "runway");
    d.holdPt = taxi ? offsetPt(d.entry.c, bearing(d.entry.c, taxi.to.c), 55) : d.entry.c;
  }
}

// ---------------------------------------------------------------- fleet
const TYPES = [
  ["B738", 35.8, 39.5], ["A320", 35.8, 37.6], ["A321", 35.8, 44.5], ["B737", 34.3, 33.6], ["E75L", 26.0, 31.0],
  ["CRJ9", 24.9, 36.4], ["A319", 34.1, 33.8], ["B739", 35.8, 42.1], ["B752", 38.0, 47.3], ["B772", 60.9, 63.7],
];
const AIRLINES = [["SWA", "Southwest", 38], ["AAL", "American", 24], ["SKW", "Skywest", 12], ["DAL", "Delta", 8], ["UAL", "United", 6], ["FFT", "Frontier", 5], ["ASA", "Alaska", 4], ["ENY", "Envoy", 3]];
const NATO = { A: "Alpha", B: "Bravo", C: "Charlie", D: "Delta", E: "Echo", F: "Foxtrot", G: "Golf", H: "Hotel", J: "Juliet", K: "Kilo", L: "Lima", M: "Mike", N: "November", P: "Papa", Q: "Quebec", R: "Romeo", S: "Sierra", T: "Tango", U: "Uniform", V: "Victor", W: "Whiskey", Y: "Yankee", Z: "Zulu" };
function pickAirline() {
  let x = Math.random() * AIRLINES.reduce((s, a) => s + a[2], 0);
  for (const a of AIRLINES) if ((x -= a[2]) < 0) return a;
  return AIRLINES[0];
}

// ---------------------------------------------------------------- simulation state
let S;
function freshState() {
  return {
    t: -420,
    paused: false,
    speed: 1,
    controller: "tower",
    aircraft: new Map(),
    removed: [],
    gatesInUse: new Set(),
    windows: Object.fromEntries(Object.keys(runways).map((r) => [r, []])),
    clearances: [],
    decisions: [],
    voice: [],
    dirty: { clr: new Map(), dec: new Map(), voice: new Map() },
    scheduled: [],
    counters: { c: 0, d: 0, v: 0, a: 0 },
    haboob: { triggered: false, t0: 0 },
    nextArr: -400,
    nextDep: -380,
    completed: 0,
    delaySum: 0,
    completedTimes: [],
    thinkUntil: 0,
    tickCount: 0,
  };
}

function spokenCallsign(ac) {
  return `${ac.spoken} ${ac.callsign.replace(/^[A-Z]+/, "")}`;
}
function routeText(refs) {
  const names = [...new Set(refs.filter(Boolean))].slice(0, 4).map((r) => NATO[r] ?? r);
  return names.length ? names.join(", ") : "direct";
}
function refsOfRoute(route) {
  const out = [];
  for (const r of route) if (!out.length || out[out.length - 1] !== r.ref) out.push(r.ref);
  return out;
}
function windowFree(ref, t0, t1, ignoreCs) {
  return !S.windows[ref].some((w) => w.callsign !== ignoreCs && w.start_s < t1 && w.end_s > t0);
}
function schedule(at, fn) {
  S.scheduled.push({ at, fn });
}
function windDesc() {
  const w = weatherNow();
  return `wind ${String(w.wind_dir).padStart(3, "0")} at ${w.wind_kt}${w.gust_kt ? ` gust ${w.gust_kt}` : ""}`;
}

// ---------------------------------------------------------------- decisions, clearances, voice
function say(ac, speaker, text, status = "ok") {
  const v = { id: `v-${++S.counters.v}`, t: S.t, speaker, callsign: ac.callsign, text, status };
  S.voice.push(v);
  S.dirty.voice.set(v.id, v);
  if (S.voice.length > 80) S.voice.shift();
  return v;
}
function issue(ac, action, text, readback, dec, doneFn) {
  const c = { id: `c-${++S.counters.c}`, callsign: ac.callsign, action, text, state: "issued", issued_t: S.t, ack_t: null, done_t: null };
  S.clearances.push(c);
  if (S.clearances.length > 90) S.clearances.shift();
  S.dirty.clr.set(c.id, c);
  const v = say(ac, "agent", text);
  const d = { id: `d-${++S.counters.d}`, t: S.t, callsign: ac.callsign, action, voice_id: v.id, next_review_s: dec.next ?? 15, summary: dec.summary, facts: dec.facts, rules: dec.rules, alternatives: dec.alternatives };
  S.decisions.push(d);
  if (S.decisions.length > 50) S.decisions.shift();
  S.dirty.dec.set(d.id, d);
  S.thinkUntil = Date.now() + 900 + Math.random() * 700;
  const wrong = Math.random() < 0.1;
  schedule(S.t + rnd(2.2, 3.6), () => {
    if (wrong) {
      say(ac, "pilot", readback.replace(/\b(\d+)\b/, (m) => String((Number(m) % 9) + 1)), "corrected");
      schedule(S.t + 2, () => {
        say(ac, "agent", `${spokenCallsign(ac)}, negative, ${text.split(", ").slice(1).join(", ")}`);
        schedule(S.t + 2.5, () => ack());
      });
    } else ack();
  });
  function ack() {
    say(ac, "pilot", readback, "ok");
    c.state = "acknowledged";
    c.ack_t = S.t;
    S.dirty.clr.set(c.id, c);
  }
  if (doneFn) ac.pending.push({ c, doneFn });
  return c;
}

function checkDone(ac) {
  ac.pending = ac.pending.filter((p) => {
    if (p.c.state !== "issued" && p.doneFn(ac)) {
      p.c.state = "complete";
      p.c.done_t = S.t;
      S.dirty.clr.set(p.c.id, p.c);
      return false;
    }
    return true;
  });
}

// ---------------------------------------------------------------- spawning
function makeBase(kind, extra) {
  const al = kind === "vehicle" ? ["VEH", "Ops", 0] : pickAirline();
  const ty = pick(TYPES);
  const num = Math.floor(rnd(100, 4999));
  const id = kind === "vehicle" ? "OPS1" : `${al[0]}${num}`;
  return Object.assign(
    {
      id, callsign: id, airline: al[0], spoken: al[1], type: kind === "vehicle" ? "VEH" : ty[0],
      wingspan_m: kind === "vehicle" ? 5 : ty[1], length_m: kind === "vehicle" ? 6 : ty[2], kind,
      s: 0, v: 0, heading: 0, pending: [], phase: "parked", dirty: true, spawnT: S.t, altitude: 0,
    },
    extra,
  );
}

function trySpawnArrival() {
  const ref = pick(ARR_RWY);
  const exits = arrivalExits(ref);
  const free = standList.filter((g) => !S.gatesInUse.has(g.name));
  if (!exits.length || !free.length) return false;
  const r = runways[ref];
  const tdS = 5500 + 250;
  const tTouch = S.t + tdS / 72;
  if (!windowFree(ref, tTouch - 25, tTouch + 70)) return false;
  const exitIdx = pick(exits);
  const gate = pick(free);
  const route = dijkstra(nodes.get(key(r.pts[exitIdx])), gate.node);
  if (!route) return false;
  const brg = bearing(r.pts[1], r.pts[0]);
  const approachStart = offsetPt(r.pts[0], brg, 5500);
  const rollPts = r.pts.slice(0, exitIdx + 1);
  const P = makePath([approachStart, ...rollPts, ...route.slice(1).map((x) => x.n.c), gate.coord]);
  const exitS = 5500 + r.cum[exitIdx];
  const ac = makeBase("arrival", { gate: gate.name, runway: ref, path: P, tdS, exitS, route, accel: 1.4 });
  if (S.aircraft.has(ac.id)) return false;
  ac.v = 72;
  ac.phase = "final";
  ac.win = { callsign: ac.id, start_s: tTouch - 12, end_s: tTouch + 62, kind: "reserved" };
  S.windows[ref].push(ac.win);
  S.gatesInUse.add(gate.name);
  S.aircraft.set(ac.id, ac);
  const rn = RWY_NAME[ref];
  const prev = [...S.aircraft.values()].filter((o) => o !== ac && o.kind === "arrival" && o.runway === ref && o.phase === "final")[0];
  issue(ac, "cleared_to_land", `${spokenCallsign(ac)}, runway ${rn}, ${windDesc()}, cleared to land`, `Cleared to land runway ${rn}, ${spokenCallsign(ac)}`, {
    summary: `Cleared ${ac.callsign} to land on runway ${RWY_SHORT[ref]}, ${prev ? `behind ${prev.callsign}` : "no traffic ahead"}`,
    facts: [`${ac.callsign} 4.5 NM final, ${ac.type}`, prev ? `${prev.callsign} landing ${RWY_SHORT[ref]} ${Math.round(Math.max(0, (prev.win.end_s - S.t)))} s ahead` : `Runway ${RWY_SHORT[ref]} free for 90 s`, `Wind ${windDesc().replace("wind ", "")}`],
    rules: ["Land only on a clear runway (JO 7110.65 3-10-5)", "Wake separation 3 NM behind same category (JO 7110.126A)"],
    alternatives: [{ option: `Runway ${RWY_SHORT[ARR_RWY.find((x) => x !== ref) ?? ref]} instead`, reason: "Longer taxi to the Terminal 4 gates, no landing-rate benefit" }, { option: "Hold on final for the departure", reason: "Departure window already closed 20 s earlier" }],
    next: 20,
  }, (a) => a.s > a.tdS + 40);
  return true;
}

function trySpawnDeparture(ga = false) {
  const free = standList.filter((g) => !S.gatesInUse.has(g.name));
  if (!free.length) return false;
  const ref = pick(DEP_RWY);
  const setup = departureSetup(ref);
  if (!setup) return false;
  const gate = pick(free);
  const route = dijkstra(gate.node, setup.entry);
  if (!route) return false;
  const r = runways[ref];
  const brgEnd = bearing(r.pts[r.pts.length - 2], r.pts[r.pts.length - 1]);
  const ext = offsetPt(r.pts[r.pts.length - 1], brgEnd, 5000);
  const taxiPts = [gate.coord, ...route.map((x) => x.n.c)];
  const P = makePath([...taxiPts, ...r.pts.slice(setup.idx + 1), ext]);
  const entryS = P.cum[taxiPts.length - 1] ?? 0;
  const entryDist = (() => {
    let d = 0;
    for (let i = 1; i < taxiPts.length; i++) d += dist(taxiPts[i - 1], taxiPts[i]);
    return d;
  })();
  void entryS;
  const holdS = Math.max(60, entryDist - 55);
  const ac = makeBase("departure", { gate: gate.name, runway: ref, path: P, entryS: entryDist, holdS, liftS: entryDist + 1150, route, accel: 2.4, readyT: S.t + rnd(10, 40), cleared: false, taxiCleared: false });
  if (ga) Object.assign(ac, { id: "N172SP", callsign: "N172SP", airline: "GA", spoken: "Cessna", type: "C172", wingspan_m: 11, length_m: 8.3, accel: 1.4 });
  if (S.aircraft.has(ac.id)) return false;
  ac.phase = "at_gate";
  ac.heading = tangentAt(P, 0);
  S.gatesInUse.add(gate.name);
  S.aircraft.set(ac.id, ac);
  return true;
}

function spawnVehicle() {
  const n1 = pick(best);
  const n2 = pick(best.filter((n) => dist(n.c, n1.c) > 500 && taxiKeys.has(n.k)));
  const route = dijkstra(n1, n2);
  if (!route) return;
  const P = makePath(route.map((x) => x.n.c));
  const ac = makeBase("vehicle", { gate: null, runway: null, path: P, route: [], accel: 1.2 });
  ac.phase = "vehicle_moving";
  ac.s = 0;
  S.aircraft.set(ac.id, ac);
}

// ---------------------------------------------------------------- per-aircraft stepping
const TAXI_V = 11;
function wxFactor() {
  return 1 - 0.4 * haboobProgress();
}
function holdEff(ac) {
  if (ac.queueN === undefined && ac.s > ac.holdS - 140) {
    ac.queueN = [...S.aircraft.values()].filter((o) => o !== ac && o.kind === "departure" && o.runway === ac.runway && o.queueN !== undefined && ["taxi_out", "hold_short"].includes(o.phase)).length;
  }
  return ac.holdS - 42 * (ac.queueN ?? 0);
}
function targetSpeed(ac) {
  const P = ac.path;
  const remain = P.total - ac.s;
  const taxi = Math.min(TAXI_V * wxFactor(), slowLimit(P, ac.s));
  const stopAt = (d) => Math.sqrt(2 * 1.6 * Math.max(0, d));
  switch (ac.phase) {
    case "final": return 72;
    case "landing_roll": return 14;
    case "runway_exit": return Math.min(taxi, 9);
    case "taxi_in": return Math.min(taxi, stopAt(remain));
    case "at_gate": return 0;
    case "pushback": return 1.6;
    case "taxi_out": return Math.min(taxi, stopAt(holdEff(ac) - ac.s));
    case "hold_short": return 0;
    case "line_up": return Math.min(6, stopAt(Math.max(0, ac.entryS + 5 - ac.s)) + 2);
    case "takeoff_roll": return 88;
    case "airborne": return 90;
    case "vehicle_moving": return Math.min(6, slowLimit(P, ac.s), stopAt(remain) + 1);
    default: return 0;
  }
}

function stepAircraft(ac, dt) {
  if (ac.kind === "departure" && ac.phase === "at_gate" && S.t >= ac.readyT) {
    ac.phase = "pushback";
    ac.pushEnd = ac.s + 45;
    const refs = refsOfRoute(ac.route);
    issue(ac, "pushback_approved", `${spokenCallsign(ac)}, pushback approved, facing west`, `Pushback approved, ${spokenCallsign(ac)}`, {
      summary: `Approved pushback for ${ac.callsign} at gate ${ac.gate}`,
      facts: [`Gate ${ac.gate} departure ready`, `Ramp clear behind ${ac.gate}`, `Taxi route ${routeText(refs)} free of traffic`],
      rules: ["Ground control: movement area entry only with clearance (JO 7110.65 3-7-1)"],
      alternatives: [{ option: "Delay pushback 60 s", reason: "No inbound conflict on the push lane, delay would cost 60 s" }],
      next: 30,
    }, (a) => a.s > a.pushEnd);
  }
  const vt = targetSpeed(ac);
  const rate = vt < ac.v ? (ac.v > 25 ? 3.6 : 2.2) : ac.accel ?? 1.5;
  ac.v += clamp(vt - ac.v, -rate * dt, rate * dt);
  ac.s = Math.min(ac.path.total, ac.s + ac.v * dt);
  const P = ac.path;

  switch (ac.phase) {
    case "final":
      ac.altitude = Math.max(0, (ac.tdS - ac.s) * 0.0524 * 3.28);
      if (ac.s >= ac.tdS) {
        ac.phase = "landing_roll";
        ac.altitude = 0;
        ac.win.kind = "occupied";
      }
      break;
    case "landing_roll":
      if (ac.s >= ac.exitS) {
        ac.phase = "runway_exit";
        ac.win.end_s = S.t + 8;
        schedule(S.t + 1, () => {});
        const refs = refsOfRoute(ac.route);
        const rn = RWY_SHORT[ac.runway];
        issue(ac, "taxi_via", `${spokenCallsign(ac)}, vacate runway ${rn}, taxi to gate ${ac.gate} via ${routeText(refs)}`, `Vacate, taxi gate ${ac.gate} via ${routeText(refs)}, ${spokenCallsign(ac)}`, {
          summary: `Taxi ${ac.callsign} to gate ${ac.gate} via ${routeText(refs)}`,
          facts: [`${ac.callsign} exiting ${rn} at ${Math.round(ac.v * 1.94)} kt`, `Gate ${ac.gate} assigned, no pushback in progress`, `${refs.length} taxiway segments, shortest route 0 conflicts`],
          rules: ["Aircraft vacates runway promptly to restore capacity (JO 7110.65 3-7-4)"],
          alternatives: [{ option: "Longer route via outer taxiway", reason: `+${Math.round(rnd(150, 400))} m with no conflict avoided` }],
          next: 25,
        }, (a) => a.phase === "at_gate" || a.phase === "parked");
      }
      break;
    case "runway_exit":
      if (ac.s > ac.exitS + 70) ac.phase = "taxi_in";
      break;
    case "taxi_in":
      if (P.total - ac.s < 0.6 && ac.v < 0.4) {
        ac.phase = "at_gate";
        ac.atGateT = S.t;
        S.completed++;
        S.completedTimes.push(S.t);
        S.delaySum += rnd(5, 40);
      }
      break;
    case "at_gate":
      if (ac.kind === "arrival" && S.t - ac.atGateT > 25) {
        ac.phase = "parked";
        ac.gone = true;
      }
      break;
    case "pushback":
      if (ac.s >= ac.pushEnd) {
        ac.phase = "taxi_out";
        const refs = refsOfRoute(ac.route);
        const rn = RWY_SHORT[ac.runway];
        issue(ac, "taxi_via", `${spokenCallsign(ac)}, taxi to runway ${RWY_NAME[ac.runway]} via ${routeText(refs)}, hold short of runway ${RWY_NAME[ac.runway]}`, `Taxi runway ${rn} via ${routeText(refs)}, hold short, ${spokenCallsign(ac)}`, {
          summary: `Taxi ${ac.callsign} to runway ${rn} via ${routeText(refs)}, hold short`,
          facts: [`Departure runway ${rn} active`, `${[...S.aircraft.values()].filter((o) => o.kind === "departure" && o.runway === ac.runway && o !== ac).length} departures ahead on ${rn}`, `Route length ${Math.round(ac.holdS)} m`],
          rules: ["Hold short of runway unless explicitly cleared (JO 7110.65 3-7-2)", "Sequence departures first come first served"],
          alternatives: [{ option: `Runway ${RWY_SHORT[DEP_RWY.find((x) => x !== ac.runway) ?? ac.runway]}`, reason: "Queue is longer there, +90 s expected hold" }],
          next: 20,
        }, (a) => a.phase === "hold_short" || a.s >= a.holdS - 2);
      }
      break;
    case "taxi_out":
      if (ac.s >= holdEff(ac) - 1.5 && ac.v < 0.6) {
        ac.phase = "hold_short";
        ac.holdStart = S.t;
      }
      break;
    case "hold_short": {
      const rn = RWY_SHORT[ac.runway];
      if (!ac.holdDecided && S.t - ac.holdStart > 1) {
        const blockers = S.windows[ac.runway].filter((w) => w.end_s > S.t && w.callsign !== ac.id);
        if (blockers.length) {
          ac.holdDecided = true;
          const b = blockers[0];
          issue(ac, "hold_short", `${spokenCallsign(ac)}, hold short of runway ${RWY_NAME[ac.runway]}, traffic ${b.kind === "occupied" ? "on the runway" : "landing"}`, `Holding short runway ${rn}, ${spokenCallsign(ac)}`, {
            summary: `Holding ${ac.callsign} short of ${rn} for ${b.callsign}`,
            facts: [`${b.callsign} ${b.kind} on ${rn} until T+${Math.round(b.end_s)} s`, `${ac.callsign} at hold line, ready`, `Window opens in ${Math.max(1, Math.round(b.end_s - S.t))} s`],
            rules: ["Runway occupied: no line-up or takeoff clearance (JO 7110.65 3-9-4)", "Crossing or entry only with explicit clearance (JO 7110.65 3-7-2)"],
            alternatives: [{ option: "Line up and wait now", reason: `Arrival ${b.callsign} would reach the threshold with ${ac.callsign} on the runway` }, { option: "Cancel takeoff slot, re-sequence", reason: "Window opens in under 40 s, a re-sequence costs more delay" }],
            next: 10,
          }, (a) => a.phase !== "hold_short");
        }
      }
      const ahead = [...S.aircraft.values()].some((o) => o !== ac && o.kind === "departure" && o.runway === ac.runway && o.phase === "hold_short" && o.holdStart < ac.holdStart);
      if (!ahead && windowFree(ac.runway, S.t + 2, S.t + 75, ac.id) && S.t - ac.holdStart > 4) {
        ac.phase = "line_up";
        ac.win = { callsign: ac.id, start_s: S.t, end_s: S.t + 70, kind: "reserved" };
        S.windows[ac.runway].push(ac.win);
        issue(ac, "line_up_and_wait", `${spokenCallsign(ac)}, runway ${RWY_NAME[ac.runway]}, line up and wait`, `Line up and wait runway ${rn}, ${spokenCallsign(ac)}`, {
          summary: `Line up ${ac.callsign} on ${rn}, takeoff to follow`,
          facts: [`Runway ${rn} free for 75 s`, `No arrival inside 3 NM`, `Hold line crossed under clearance`],
          rules: ["Line up and wait only with a clear runway ahead (JO 7110.65 3-9-4)", "Departure released after preceding arrival clears (JO 7110.65 3-9-6)"],
          alternatives: [{ option: "Immediate takeoff clearance", reason: "Aircraft not yet on the runway, clearing it now saves no time" }],
          next: 8,
        }, (a) => a.phase === "takeoff_roll" || a.phase === "airborne");
        schedule(S.t + 5, () => {
          issue(ac, "cleared_for_takeoff", `${spokenCallsign(ac)}, ${windDesc()}, runway ${RWY_NAME[ac.runway]}, cleared for takeoff`, `Cleared for takeoff runway ${rn}, ${spokenCallsign(ac)}`, {
            summary: `Cleared ${ac.callsign} for takeoff, runway ${rn}`,
            facts: [`Runway ${rn} clear, reserved until T+${Math.round(ac.win.end_s)} s`, `Wind ${windDesc().replace("wind ", "")}`, `Next arrival ${Math.round(rnd(70, 140))} s from threshold`],
            rules: ["Takeoff clearance requires a clear runway and spacing (JO 7110.65 3-9-1)"],
            alternatives: [{ option: "Hold for wake turbulence delay", reason: "Leader was 4 minutes ago, wake spacing already met" }],
            next: 10,
          }, (a) => a.phase === "airborne");
          ac.cleared = true;
        });
      }
      break;
    }
    case "line_up":
      if (ac.s >= ac.entryS + 3 && ac.cleared) {
        ac.phase = "takeoff_roll";
        ac.win.kind = "occupied";
      }
      break;
    case "takeoff_roll":
      if (ac.s >= ac.liftS) {
        ac.phase = "airborne";
        ac.win.end_s = S.t + 3;
        S.completed++;
        S.completedTimes.push(S.t);
        S.delaySum += rnd(5, 40);
      }
      break;
    case "airborne":
      ac.altitude = Math.max(0, (ac.s - ac.liftS) * 0.105 * 3.28);
      if (ac.s >= P.total - 20) ac.gone = true;
      break;
    case "vehicle_moving":
      if (P.total - ac.s < 1 && ac.v < 0.5) {
        const here = nodes.get(key(P.pts[P.pts.length - 1])) ?? best[0];
        const to = pick(best.filter((n) => dist(n.c, here.c) > 400 && taxiKeys.has(n.k)));
        const rt = dijkstra(here, to);
        if (rt) {
          ac.path = makePath(rt.map((x) => x.n.c));
          ac.s = 0;
        }
      }
      break;
  }
  // heading follows the path tangent with a smooth turn rate
  if (ac.phase !== "at_gate" || ac.v > 0.1) {
    const target = tangentAt(ac.path, ac.s);
    ac.heading = (ac.heading + angDiff(ac.heading, target) * Math.min(1, dt * 3) + 360) % 360;
  }
  ac.dirty = true;
  checkDone(ac);
}

function serializeAircraft(ac) {
  const P = ac.path;
  const p = posAt(P, ac.s);
  const statusText = {
    final: `On final runway ${RWY_SHORT[ac.runway]}`, landing_roll: `Landing roll ${RWY_SHORT[ac.runway]}`, runway_exit: "Vacating runway",
    taxi_in: `Taxiing to gate ${ac.gate}`, at_gate: `At gate ${ac.gate}`, pushback: `Pushing back from ${ac.gate}`,
    taxi_out: `Taxi to runway ${RWY_SHORT[ac.runway]}`, hold_short: `Holding short ${RWY_SHORT[ac.runway]}`, line_up: `Lining up ${RWY_SHORT[ac.runway]}`,
    takeoff_roll: `Takeoff roll ${RWY_SHORT[ac.runway]}`, airborne: "Departed", vehicle_moving: "Ground vehicle", parked: "Parked",
  }[ac.phase];
  let route = [];
  let planned = [];
  if (ac.kind === "arrival" && ["final", "landing_roll"].includes(ac.phase)) {
    route = slicePath(P, ac.s, ac.exitS);
    planned = slicePath(P, ac.exitS, P.total);
  } else if (ac.kind === "arrival" && ["runway_exit", "taxi_in"].includes(ac.phase)) {
    route = slicePath(P, ac.s, P.total);
  } else if (ac.kind === "departure" && ["pushback", "taxi_out", "hold_short", "line_up", "takeoff_roll"].includes(ac.phase)) {
    const lim = ac.cleared || ac.phase === "line_up" ? ac.liftS : ac.holdS;
    route = slicePath(P, ac.s, lim);
    planned = ac.cleared ? [] : slicePath(P, ac.holdS, Math.min(P.total, ac.entryS + 700));
  } else if (ac.kind === "departure" && ac.phase === "at_gate") {
    planned = slicePath(P, 0, ac.holdS);
  } else if (ac.kind === "vehicle") {
    route = slicePath(P, ac.s, P.total);
  }
  const holding = ac.kind === "departure" && ((ac.phase === "taxi_out" && ac.s > ac.holdS - 160) || ac.phase === "hold_short");
  const hp = holding ? posAt(P, ac.holdS) : null;
  let eta = null;
  if (ac.phase === "final") eta = Math.max(0, (ac.tdS - ac.s) / 72);
  else if (ac.phase === "taxi_in") eta = (P.total - ac.s) / 8;
  else if (ac.phase === "at_gate" && ac.kind === "departure") eta = Math.max(0, ac.readyT - S.t);
  else if (ac.phase === "taxi_out" || ac.phase === "pushback") eta = Math.max(0, (ac.holdS - ac.s) / 8);
  return {
    id: ac.id, callsign: ac.callsign, type: ac.type, kind: ac.kind, airline: ac.airline,
    lon: r6(p[0]), lat: r6(p[1]), heading_deg: Math.round(ac.heading * 10) / 10, speed_mps: Math.round(ac.v * 100) / 100,
    phase: ac.phase, status_text: statusText, wingspan_m: ac.wingspan_m, length_m: ac.length_m,
    gate: ac.gate, runway: ac.runway,
    route, planned_route: planned,
    hold: hp ? { runway: ac.runway, lon: r6(hp[0]), lat: r6(hp[1]) } : null,
    eta_s: eta === null ? null : Math.round(eta * 10) / 10,
    altitude_ft: Math.round(ac.altitude ?? 0),
  };
}

// ---------------------------------------------------------------- weather
function haboobProgress() {
  if (!S.haboob.triggered) return 0;
  return clamp((S.t - S.haboob.t0) / 120, 0, 1);
}
function weatherNow() {
  const p = haboobProgress();
  const baseMin = 22 * 60 + 50;
  const sec = Math.floor(baseMin * 60 + S.t);
  const hh = String(Math.floor(sec / 3600) % 24).padStart(2, "0");
  const mm = String(Math.floor(sec / 60) % 60).padStart(2, "0");
  const dir = Math.round(270 + (110 - 270) * p + 360) % 360;
  const kt = Math.round(9 + 26 * p);
  const gust = p > 0.1 ? Math.round(18 + 38 * p) : null;
  const vis = p === 0 ? 10 : Math.max(0.25, Math.round((10 - 9.75 * p) * 4) / 4);
  const visTxt = vis >= 10 ? "10SM" : vis < 1 ? "1/4SM" : `${vis}SM`;
  const wx = p > 0.2 ? "DS" : null;
  const metar = `KPHX 25${hh}${mm}Z ${String(dir).padStart(3, "0")}${String(kt).padStart(2, "0")}${gust ? `G${gust}` : ""}KT ${visTxt}${wx ? " +DS" : ""} ${p > 0.2 ? "BKN012" : "FEW080"} ${p > 0.2 ? "33/12" : "41/10"} A2984 RMK AO2`;
  return { t: Math.round(S.t * 10) / 10, active: p > 0, metar, wind_dir: dir, wind_kt: kt, gust_kt: gust, vis_sm: vis, wx, temp_f: null, haboob_progress: Math.round(p * 1000) / 1000 };
}

// ---------------------------------------------------------------- tick
function runwaysPayload() {
  return Object.keys(runways).map((ref) => {
    const ws = S.windows[ref].filter((w) => w.end_s > S.t - 180 && w.start_s < S.t + 180);
    const occ = ws.find((w) => w.kind === "occupied" && w.end_s > S.t - 1);
    const res = ws.find((w) => w.kind === "reserved" && w.start_s < S.t + 40 && w.end_s > S.t);
    return {
      id: ref, name: ref, status: occ ? "occupied" : res ? "reserved" : "free", owner: occ ? occ.callsign : res ? res.callsign : null,
      occupancy: ws.map((w) => ({ callsign: w.callsign, start_s: Math.round(w.start_s * 10) / 10, end_s: Math.round(w.end_s * 10) / 10, kind: w.kind })),
    };
  });
}
function scorePayload() {
  const recent = S.completedTimes.filter((x) => x > S.t - 600).length;
  return { separation_losses: 0, runway_conflicts: 0, deadlocks: 0, readback_errors_accepted: 0, completed: S.completed, avg_delay_s: S.completed ? Math.round((S.delaySum / S.completed) * 10) / 10 : 0, throughput_per_10min: recent };
}
function agentPayload() {
  return { status: Date.now() < S.thinkUntil ? "thinking" : "idle", controller: S.controller };
}
function scenarioPayload() {
  return { name: "kphx_haboob", duration_s: 480, haboob_triggered: S.haboob.triggered };
}

function simStep(dt) {
  S.t += dt;
  while (S.scheduled.length) {
    const i = S.scheduled.findIndex((x) => x.at <= S.t);
    if (i < 0) break;
    S.scheduled.splice(i, 1)[0].fn();
  }
  // spawning
  const live = [...S.aircraft.values()].filter((a) => a.kind !== "vehicle" && a.phase !== "airborne").length;
  if (live < 13 && S.t >= S.nextArr && trySpawnArrival()) S.nextArr = S.t + rnd(30, 44);
  if (live < 13 && S.t >= S.nextDep && trySpawnDeparture(Math.random() < 0.06)) S.nextDep = S.t + rnd(24, 38);
  for (const ac of S.aircraft.values()) {
    stepAircraft(ac, dt);
    if (ac.gone) {
      S.removed.push(ac.id);
      S.aircraft.delete(ac.id);
      if (ac.gate) S.gatesInUse.delete(ac.gate);
    }
  }
  for (const ref of Object.keys(S.windows)) S.windows[ref] = S.windows[ref].filter((w) => w.end_s > S.t - 200);
}

function resetSim() {
  S = freshState();
  spawnVehicle();
  const pre = 0.1;
  while (S.t < 0) simStep(pre);
  S.t = Math.round(S.t * 10) / 10;
  S.dirty = { clr: new Map(), dec: new Map(), voice: new Map() };
  S.removed = [];
  for (const a of S.aircraft.values()) a.dirty = true;
}
resetSim();

function snapshot() {
  return {
    type: "snapshot", t: Math.round(S.t * 10) / 10, speed: S.speed, paused: S.paused, agent: agentPayload(), scenario: scenarioPayload(),
    weather: weatherNow(), aircraft: [...S.aircraft.values()].map(serializeAircraft), runways: runwaysPayload(),
    clearances: S.clearances.slice(-60), decisions: S.decisions.slice(-40), voice: S.voice.slice(-40), score: scorePayload(),
  };
}

let lastSlow = 0;
let lastAgent = "";
function buildDelta() {
  const d = { type: "delta", t: Math.round(S.t * 10) / 10 };
  const ac = [...S.aircraft.values()].filter((a) => a.dirty);
  if (ac.length) d.aircraft = ac.map(serializeAircraft);
  for (const a of ac) a.dirty = false;
  if (S.removed.length) d.removed = S.removed.splice(0);
  if (S.dirty.clr.size) d.clearances = [...S.dirty.clr.values()];
  if (S.dirty.dec.size) d.decisions = [...S.dirty.dec.values()];
  if (S.dirty.voice.size) d.voice = [...S.dirty.voice.values()];
  S.dirty = { clr: new Map(), dec: new Map(), voice: new Map() };
  S.tickCount++;
  if (S.tickCount % 10 === 0 || Date.now() - lastSlow > 1000) {
    lastSlow = Date.now();
    d.runways = runwaysPayload();
    d.weather = weatherNow();
    d.score = scorePayload();
    d.scenario = scenarioPayload();
    d.speed = S.speed;
    d.paused = S.paused;
  }
  const ag = JSON.stringify(agentPayload());
  if (ag !== lastAgent || S.tickCount % 10 === 0) {
    lastAgent = ag;
    d.agent = JSON.parse(ag);
  }
  return d;
}

// ---------------------------------------------------------------- control
function control(action, value) {
  switch (action) {
    case "pause": S.paused = true; break;
    case "resume": S.paused = false; break;
    case "speed":
      if (![1, 2, 5, 10, 20].includes(Number(value))) return { ok: false, error: "speed must be 1,2,5,10 or 20" };
      S.speed = Number(value);
      break;
    case "trigger_haboob":
      if (!S.haboob.triggered) {
        S.haboob = { triggered: true, t0: S.t };
        S.thinkUntil = Date.now() + 3000;
        const v = [...S.aircraft.values()].find((a) => a.kind !== "vehicle");
        if (v) {
          const d = { id: `d-${++S.counters.d}`, t: S.t, callsign: v.callsign, action: "hold_position", voice_id: null, next_review_s: 20,
            summary: "Haboob inbound: slowing surface traffic and re-spacing arrivals",
            facts: ["Dust wall 20 NM southeast, moving 35 kt", "Visibility forecast below 1/2 SM within 2 min", "Taxi speed reduced 40 percent"],
            rules: ["Reduced visibility: increase spacing between taxiing aircraft (JO 7110.65 3-7-1)", "Runway occupancy time grows in dust (assumption A-14)"],
            alternatives: [{ option: "Suspend all departures", reason: "Three departures can still leave before visibility drops" }, { option: "Divert arrivals now", reason: "Only two arrivals inside 15 NM, both can land first" }] };
          S.decisions.push(d);
          S.dirty.dec.set(d.id, d);
        }
      }
      break;
    case "reset": {
      const sp = S.speed;
      resetSim();
      S.speed = sp;
      resetFlag = true;
      break;
    }
    default: return { ok: false, error: `unknown action ${action}` };
  }
  return { ok: true, state: { paused: S.paused, speed: S.speed, controller: S.controller } };
}
let resetFlag = false;

// ---------------------------------------------------------------- http + ws
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "Content-Type" };
const graphJson = JSON.stringify(graphPayload());
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  const send = (obj, raw) => {
    res.writeHead(200, { "Content-Type": "application/json", ...CORS });
    res.end(raw ?? JSON.stringify(obj));
  };
  if (req.method === "OPTIONS") return (res.writeHead(204, CORS), res.end());
  if (url.pathname === "/health") return send({ ok: true });
  if (url.pathname === "/graph") return send(null, graphJson);
  if (url.pathname === "/state") return send(snapshot());
  if (url.pathname.startsWith("/decisions/")) {
    const d = S.decisions.find((x) => x.id === url.pathname.split("/")[2]);
    if (!d) return (res.writeHead(404, CORS), res.end("{}"));
    return send(d);
  }
  if (url.pathname === "/control" && req.method === "POST") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      try {
        const { action, value } = JSON.parse(body || "{}");
        send(control(action, value));
      } catch (e) {
        res.writeHead(400, CORS);
        res.end(JSON.stringify({ ok: false, error: String(e) }));
      }
    });
    return;
  }
  res.writeHead(404, CORS);
  res.end("not found");
});

const wss = new WebSocketServer({ server, path: "/ws" });
const clients = new Set();
wss.on("connection", (ws) => {
  clients.add(ws);
  ws.send(JSON.stringify(snapshot()));
  ws.on("close", () => clients.delete(ws));
});

setInterval(() => {
  if (!S.paused) for (let i = 0; i < S.speed; i++) simStep(0.1);
  if (!clients.size) return;
  let payload;
  if (resetFlag) {
    resetFlag = false;
    payload = JSON.stringify(snapshot());
  } else payload = JSON.stringify(buildDelta());
  for (const c of clients) if (c.readyState === 1) c.send(payload);
}, 100);

server.on("error", (e) => {
  if (e.code === "EADDRINUSE") console.error(`[mock] port ${PORT} is busy (real API running?). Start with: node mock/server.mjs 8001 and run the dashboard with VITE_API_URL=http://localhost:8001`);
  else console.error(e);
  process.exit(1);
});
server.listen(PORT, () => console.log(`[mock] KPHX mock API on http://localhost:${PORT} (ws /ws)`));
