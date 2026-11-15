import "./style.css";
import { MapController, type View } from "./map";
import { Store } from "./store";
import { buildLayers, prepareStatic, type StaticData } from "./layers";
import { Panels } from "./panels";
import { Voice } from "./voice";
import type { LonLat } from "./types";

const $ = (id: string) => document.getElementById(id) as HTMLElement;
const store = new Store();
const voice = new Voice();
const map = new MapController($("map"));
let data: StaticData = prepareStatic(null);
let quality: 0 | 1 | 2 = 0;
const params = new URLSearchParams(location.search);
const allowDegrade = params.get("degrade") !== "0";

const panels = new Panels(store, { select: (id) => select(id, true) });

// ------------------------------------------------------------------ camera
const M_LAT = 110574;
const mpp = (zoom: number, lat: number) => (156543.03 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
let overview: View = { center: [-112.0095, 33.4333], zoom: 14.8 };
let tween: { from: View; to: View; t0: number; dur: number } | null = null;

/** Free (uncovered) rectangle between the panels, in CSS pixels. */
function freeRect() {
  const l = $("left").getBoundingClientRect();
  const r = $("right").getBoundingClientRect();
  const t = $("topbar").getBoundingClientRect();
  const b = $("bottom").getBoundingClientRect();
  return { x0: l.right + 12, x1: r.left - 12, y0: t.bottom + 12, y1: b.top - 12 };
}

function viewFor(center: LonLat, zoom: number): View {
  const f = freeRect();
  const cx = (f.x0 + f.x1) / 2;
  const cy = (f.y0 + f.y1) / 2;
  const m = mpp(zoom, center[1]);
  const mLon = 111320 * Math.cos((center[1] * Math.PI) / 180);
  return { center: [center[0] - ((cx - innerWidth / 2) * m) / mLon, center[1] + ((cy - innerHeight / 2) * m) / M_LAT], zoom };
}

function computeOverview(): View {
  const f = freeRect();
  const b = data.bounds;
  const lat = (b[1] + b[3]) / 2;
  const mLon = 111320 * Math.cos((lat * Math.PI) / 180);
  const need = Math.max(((b[2] - b[0]) * mLon) / ((f.x1 - f.x0) * 0.92), ((b[3] - b[1]) * M_LAT) / ((f.y1 - f.y0) * 0.86));
  const zoom = Math.min(15.6, Math.max(14.2, Math.log2((156543.03 * Math.cos((lat * Math.PI) / 180)) / need)));
  return viewFor([(b[0] + b[2]) / 2, lat], zoom);
}

const ease = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
function flyTo(to: View, dur: number): void {
  tween = { from: map.getView(), to, t0: performance.now(), dur };
}
function stepTween(now: number): void {
  if (!tween) return;
  const k = Math.min(1, (now - tween.t0) / tween.dur);
  const e = ease(k);
  const { from, to } = tween;
  // interpolate zoom linearly and centre in screen-proportional space so the arc feels like a flight
  map.setView({
    center: [from.center[0] + (to.center[0] - from.center[0]) * e, from.center[1] + (to.center[1] - from.center[1]) * e],
    zoom: from.zoom + (to.zoom - from.zoom) * e,
  });
  if (k >= 1) tween = null;
}

function select(id: string | null, fly: boolean): void {
  if (id && store.selected === id) id = null;
  store.selected = id;
  store.version++;
  document.querySelectorAll<HTMLElement>(".row").forEach((r) => r.classList.toggle("sel", r.dataset.id === id));
  if (!fly) return;
  const tr = id ? store.tracks.get(id) : null;
  if (tr) flyTo(viewFor([tr.lon, tr.lat], 16.7), 1200);
  else flyTo(overview, 1200);
}

// ------------------------------------------------------------------ controls
function setBadge(id: string, show: boolean): void {
  $(id).classList.toggle("show", show);
}
function toggleMute(): void {
  voice.unlock();
  voice.setEnabled(!voice.enabled);
}
voice.onChange = () => {
  $("btn-mute").classList.toggle("muted", !voice.enabled);
  setBadge("b-audio", voice.enabled && !voice.unlocked && voice.supported);
};
voice.onSpeaking = (id) => panels.setSpeaking(id);
store.onVoice = (l) => voice.push(l);
store.onConnection = (ok) => setBadge("b-reconnect", !ok);

function togglePause(): void {
  void store.control(store.paused ? "resume" : "pause");
}
async function toggleFullscreen(): Promise<void> {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    /* fullscreen can be blocked; presentation mode still toggles */
  }
  document.body.classList.toggle("presenting", !!document.fullscreenElement);
}
document.addEventListener("fullscreenchange", () => document.body.classList.toggle("presenting", !!document.fullscreenElement));

$("speed").addEventListener("click", (e) => {
  const b = (e.target as HTMLElement).closest("button");
  if (!b) return;
  if (b.dataset.act === "pause") togglePause();
  else if (b.dataset.speed) {
    void store.control("speed", Number(b.dataset.speed));
    if (store.paused) void store.control("resume");
  }
});
$("btn-mute").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleMute();
});
const unlock = () => voice.unlock();
window.addEventListener("pointerdown", unlock, { once: true });
window.addEventListener("keydown", unlock, { once: true });

let toastTimer = 0;
function toast(msg: string): void {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el!.classList.remove("show"), 4000);
}

window.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  switch (e.key) {
    case "f": case "F": void toggleFullscreen(); break;
    case " ": e.preventDefault(); togglePause(); break;
    case "1": case "2": case "3": case "4": {
      void store.control("speed", [1, 2, 5, 10][Number(e.key) - 1]);
      if (store.paused) void store.control("resume");
      break;
    }
    case "r": case "R": void store.control("reset"); break;
    case "m": case "M": toggleMute(); break;
    case "o": case "O": select(null, true); break;
    case "Escape":
      if (document.fullscreenElement) void document.exitFullscreen();
      document.body.classList.remove("presenting");
      if (store.selected) select(store.selected, true);
      break;
  }
});

// ------------------------------------------------------------------ frame loop + FPS governor
const perf = { frames: 0, last: performance.now(), fps: 0, slow: 0 };
(window as any).__kphx = perf;
(window as any).__store = store;
function frame(now: number): void {
  requestAnimationFrame(frame);
  perf.frames++;
  if (now - perf.last >= 1000) {
    perf.fps = (perf.frames * 1000) / (now - perf.last);
    perf.frames = 0;
    perf.last = now;
    if (allowDegrade && store.tracks.size > 0 && !tween) {
      perf.slow = perf.fps < 45 ? perf.slow + 1 : 0;
      if (perf.slow >= 3 && quality < 2) {
        quality = (quality + 1) as 0 | 1 | 2;
        perf.slow = 0;
        console.info(`FPS ${perf.fps.toFixed(0)} < 45: degrading to quality ${quality}`);
      }
    }
    (window as any).__kphx.quality = quality;
  }
  store.update(now);
  stepTween(now);
  const simT = store.simTime(now);
  map.setLayers(
    buildLayers({ store, now, simT, view: map.getView(), quality, data, onPick: (id) => select(id, true) }),
  );
  panels.update(now);
}

// ------------------------------------------------------------------ boot
async function boot(): Promise<void> {
  map.onFallback = (reason) => {
    console.info("Using offline map:", reason);
    setBadge("b-offline", true);
    $("b-offline").title = reason;
  };
  await map.init();
  const fallbackView: View = { center: [-112.07, 33.45], zoom: 9.4 };
  map.setView(fallbackView);
  voice.onChange();
  store.connect();
  requestAnimationFrame(frame);

  // graph (the dashboard never blocks on it for more than ~3 s)
  const graph = await Promise.race([store.loadGraph(), new Promise<null>((r) => setTimeout(() => r(null), 3000))]);
  document.body.classList.add("ready");
  await new Promise((r) => setTimeout(r, 60));
  if (graph) data = prepareStatic(graph);
  else void store.loadGraph().then((g) => (data = prepareStatic(g)));
  overview = computeOverview();
  flyTo(overview, 2000);
  window.addEventListener("resize", () => {
    if (!store.selected) {
      overview = computeOverview();
      if (!tween) map.setView(overview);
    }
  });
  // key hints for 4 s
  $("hints").classList.add("show");
  setTimeout(() => $("hints").classList.remove("show"), 4000);
  if (!store.connected) setBadge("b-reconnect", true);
}
void boot();
