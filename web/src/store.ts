import type {
  AgentInfo, Aircraft, Clearance, Decision, Graph, LonLat, Message, Runway, Score, ScenarioInfo, VoiceLine, Weather,
} from "./types";

export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined) || "http://localhost:8000";
const WS_URL = API_URL.replace(/^http/, "ws") + "/ws";

export interface Track {
  data: Aircraft;
  fromLon: number; fromLat: number; fromHdg: number;
  toLon: number; toLat: number; toHdg: number;
  tStart: number; dur: number;
  lon: number; lat: number; hdg: number;
  trail: { p: LonLat; t: number }[];
  holdAlpha: number;
  holdPos: LonLat | null;
}

const angDiff = (a: number, b: number) => ((b - a + 540) % 360) - 180;

export class Store {
  tracks = new Map<string, Track>();
  runways: Runway[] = [];
  clearances: Clearance[] = [];
  decisions: Decision[] = [];
  voice: VoiceLine[] = [];
  weather: Weather | null = null;
  score: Score | null = null;
  agent: AgentInfo = { status: "idle", controller: "tower" };
  scenario: ScenarioInfo | null = null;
  speed = 1;
  paused = false;
  t = 0;
  tRecv = 0;
  connected = false;
  gotSnapshot = false;
  selected: string | null = null;
  graph: Graph | null = null;
  version = 0;
  private lastMsgAt = 0;
  private interval = 100;
  private retry = 0;
  onVoice: (line: VoiceLine) => void = () => {};
  onSnapshot: () => void = () => {};
  onConnection: (ok: boolean) => void = () => {};

  simTime(now: number): number {
    if (this.paused) return this.t;
    return this.t + Math.min((now - this.tRecv) / 1000, 0.5) * this.speed;
  }

  async loadGraph(): Promise<Graph> {
    for (;;) {
      try {
        const r = await fetch(`${API_URL}/graph`);
        if (r.ok) {
          this.graph = (await r.json()) as Graph;
          return this.graph;
        }
      } catch {
        /* retry */
      }
      await new Promise((res) => setTimeout(res, 1500));
    }
  }

  /** Returns an error message when the server answered ok:false, otherwise null. */
  async control(action: string, value?: unknown): Promise<string | null> {
    try {
      const r = await fetch(`${API_URL}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, value }),
      });
      const j = await r.json();
      if (j?.ok && j.state) {
        this.paused = !!j.state.paused;
        this.speed = j.state.speed ?? this.speed;
        if (j.state.controller) this.agent = { ...this.agent, controller: j.state.controller };
        this.version++;
      } else if (j && j.ok === false) {
        return String(j.error ?? "request refused");
      }
    } catch {
      /* offline: ignore */
    }
    return null;
  }

  connect(): void {
    const ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      this.retry = 0;
      this.connected = true;
      this.onConnection(true);
    };
    ws.onmessage = (ev) => {
      try {
        this.apply(JSON.parse(ev.data as string) as Message);
      } catch (e) {
        console.error("bad message", e);
      }
    };
    ws.onclose = () => {
      this.connected = false;
      this.gotSnapshot = false;
      this.onConnection(false);
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 500 + this.retry * 500);
    };
    ws.onerror = () => ws.close();
  }

  private sample(tr: Track, now: number): void {
    const f = Math.min(1, Math.max(0, (now - tr.tStart) / tr.dur));
    tr.lon = tr.fromLon + (tr.toLon - tr.fromLon) * f;
    tr.lat = tr.fromLat + (tr.toLat - tr.fromLat) * f;
    tr.hdg = (tr.fromHdg + angDiff(tr.fromHdg, tr.toHdg) * f + 360) % 360;
  }

  /** Advance interpolation for every aircraft to wall-clock `now` (ms). */
  update(now: number): void {
    for (const tr of this.tracks.values()) {
      this.sample(tr, now);
      const hold = tr.data.hold;
      if (hold) tr.holdPos = [hold.lon, hold.lat];
      const target = hold ? 1 : 0;
      tr.holdAlpha += (target - tr.holdAlpha) * 0.12;
      if (!hold && tr.holdAlpha < 0.02) tr.holdAlpha = 0;
    }
  }

  private upsert(a: Aircraft, now: number, animate: boolean, simT: number): void {
    let tr = this.tracks.get(a.id);
    if (!tr) {
      tr = {
        data: a,
        fromLon: a.lon, fromLat: a.lat, fromHdg: a.heading_deg,
        toLon: a.lon, toLat: a.lat, toHdg: a.heading_deg,
        tStart: now, dur: 100, lon: a.lon, lat: a.lat, hdg: a.heading_deg,
        trail: [], holdAlpha: a.hold ? 1 : 0, holdPos: a.hold ? [a.hold.lon, a.hold.lat] : null,
      };
      this.tracks.set(a.id, tr);
    } else {
      this.sample(tr, now);
      tr.fromLon = animate ? tr.lon : a.lon;
      tr.fromLat = animate ? tr.lat : a.lat;
      tr.fromHdg = animate ? tr.hdg : a.heading_deg;
      tr.toLon = a.lon; tr.toLat = a.lat; tr.toHdg = a.heading_deg;
      tr.tStart = now;
      tr.dur = Math.min(260, Math.max(70, this.interval * 1.05));
      tr.data = a;
    }
    const last = tr.trail[tr.trail.length - 1];
    const moved = !last || Math.hypot((a.lon - last.p[0]) * 93000, (a.lat - last.p[1]) * 110574) > 0.4;
    if (moved && a.kind !== "vehicle") tr.trail.push({ p: [a.lon, a.lat], t: simT });
    while (tr.trail.length && tr.trail[0].t < simT - 60) tr.trail.shift();
  }

  private merge<T extends { id: string }>(list: T[], items: T[] | undefined, cap: number): T[] {
    if (!items?.length) return list;
    const m = new Map(list.map((x) => [x.id, x]));
    for (const it of items) m.set(it.id, it);
    const out = [...m.values()];
    return out.length > cap ? out.slice(out.length - cap) : out;
  }

  apply(msg: Message): void {
    const now = performance.now();
    const snap = msg.type === "snapshot";
    if (this.lastMsgAt) this.interval = this.interval * 0.8 + Math.min(500, now - this.lastMsgAt) * 0.2;
    this.lastMsgAt = now;
    if (snap) {
      if (msg.t < this.t - 1) for (const tr of this.tracks.values()) tr.trail = [];
      const ids = new Set((msg.aircraft ?? []).map((a) => a.id));
      for (const id of [...this.tracks.keys()]) if (!ids.has(id)) this.tracks.delete(id);
    }
    this.t = msg.t;
    this.tRecv = now;
    if (msg.speed !== undefined) this.speed = msg.speed;
    if (msg.paused !== undefined) this.paused = msg.paused;
    if (msg.agent) this.agent = msg.agent;
    if (msg.scenario) this.scenario = msg.scenario;
    if (msg.weather) this.weather = msg.weather;
    if (msg.score) this.score = msg.score;
    if (msg.runways) this.runways = msg.runways;
    for (const a of msg.aircraft ?? []) this.upsert(a, now, !snap, msg.t);
    for (const id of msg.removed ?? []) {
      this.tracks.delete(id);
      if (this.selected === id) this.selected = null;
    }
    if (snap) {
      this.clearances = msg.clearances ?? [];
      this.decisions = msg.decisions ?? [];
      this.voice = msg.voice ?? [];
    } else {
      this.clearances = this.merge(this.clearances, msg.clearances, 120);
      this.decisions = this.merge(this.decisions, msg.decisions, 80);
      const known = new Set(this.voice.map((v) => v.id));
      this.voice = this.merge(this.voice, msg.voice, 80);
      for (const v of msg.voice ?? []) if (!known.has(v.id) && this.gotSnapshot) this.onVoice(v);
    }
    if (snap) {
      this.gotSnapshot = true;
      this.onSnapshot();
    }
    this.version++;
  }
}
