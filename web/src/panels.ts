import type { Store, Track } from "./store";
import type { Clearance, Decision, Runway, VoiceLine } from "./types";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const pad = (n: number) => String(Math.floor(n)).padStart(2, "0");
const mmss = (s: number) => `${s < 0 ? "-" : ""}${pad(Math.abs(s) / 60)}:${pad(Math.abs(s) % 60)}`;
const hms = (s: number) => `${pad((s / 3600) % 24)}:${pad((s / 60) % 60)}:${pad(s % 60)}`;
const short = (r?: string | null) => (r ? r.split("/").map((x) => x.replace(/^0/, "")).join("/") : "");

function tone(phase: string): string {
  switch (phase) {
    case "final": case "taxi_in": case "runway_exit": return "t-cyan";
    case "landing_roll": case "go_around": case "hold_short": return "t-red";
    case "pushback": case "taxi_out": case "line_up": return "t-amber";
    case "takeoff_roll": case "airborne": return "t-green";
    default: return "";
  }
}

export interface PanelActions {
  select: (id: string | null) => void;
}

export class Panels {
  private lastVersion = -1;
  private rows = new Map<string, HTMLElement>();
  private lastDecKey = "";
  private lastTl = "";
  private lastScore = "";
  private voiceIds = new Map<string, HTMLElement>();
  private lastVoiceSig = "";
  private utcBase: number | null = null;
  private lastMetar = "";
  private speakingId: string | null = null;
  private gantt = $<HTMLCanvasElement>("gantt");
  private ganttAt = 0;

  constructor(private store: Store, private actions: PanelActions) {
    for (const id of ["list-arr", "list-dep"]) {
      $(id).addEventListener("click", (e) => {
        const row = (e.target as HTMLElement).closest(".row") as HTMLElement | null;
        if (row) this.actions.select(row.dataset.id!);
      });
    }
  }

  setSpeaking(id: string | null): void {
    this.speakingId = id;
    this.voiceIds.forEach((el, vid) => {
      el.classList.toggle("speaking", vid === id);
      const tx = el.querySelector(".tx") as HTMLElement;
      const w = tx.querySelector(".wave");
      if (vid === id && !w) tx.insertAdjacentHTML("afterbegin", `<span class="wave"><i></i><i></i><i></i><i></i></span>`);
      if (vid !== id && w) w.remove();
    });
  }

  update(now: number): void {
    const s = this.store;
    this.clocks(now);
    if (now - this.ganttAt > 66) {
      this.ganttAt = now;
      this.drawGantt(now);
    }
    this.nextReview(now);
    if (s.version === this.lastVersion) return;
    this.lastVersion = s.version;
    this.topbar();
    this.lists();
    this.right();
    this.voice();
  }

  // ---------------------------------------------------------------- top bar
  private clocks(now: number): void {
    const s = this.store;
    const t = s.simTime(now);
    $("clk-sim").textContent = `T${t < 0 ? "-" : "+"}${mmss(Math.abs(t))}`;
    const w = s.weather;
    if (w && w.metar !== this.lastMetar) {
      this.lastMetar = w.metar;
      const m = /\b(\d{2})(\d{2})(\d{2})Z\b/.exec(w.metar);
      if (m) this.utcBase = +m[2] * 3600 + +m[3] * 60 - (w.t ?? s.t);
    }
    const start = s.scenario?.start_utc ? Date.parse(s.scenario.start_utc) : NaN;
    let utc: number | null = null;
    if (!Number.isNaN(start)) utc = ((start / 1000) % 86400) + t;
    else if (this.utcBase !== null) utc = this.utcBase + t;
    const has = utc !== null;
    $("clk-utc-wrap").style.display = has ? "" : "none";
    if (has) {
      const u = ((utc! % 86400) + 86400) % 86400;
      $("clk-utc").textContent = hms(u) + "Z";
      $("clk-phx").textContent = hms((((u - 7 * 3600) % 86400) + 86400) % 86400);
    }
  }

  private topbar(): void {
    const s = this.store;
    document.querySelectorAll<HTMLButtonElement>("#speed [data-speed]").forEach((b) => b.classList.toggle("on", +b.dataset.speed! === s.speed && !s.paused));
    $("btn-pause").classList.toggle("paused", s.paused);
    const thinking = s.agent.status === "thinking";
    $("agent").classList.toggle("thinking", thinking);
    $("agent-name").textContent = s.agent.controller === "tower" ? "PHX TOWER" : s.agent.controller.toUpperCase();
    $("agent-state").textContent = thinking ? "thinking…" : s.paused ? "paused" : "monitoring";
    const w = s.weather;
    if (w) {
      $("metar").classList.toggle("alert", (w.vis_sm ?? 10) < 3);
      const wd = w.wind_dir;
      ($("wind-arrow") as unknown as SVGElement).style.transform = `rotate(${((wd ?? 0) + 180) % 360}deg)`;
      $("m-wind").textContent = wd === null ? "VRB" : `${String(Math.round(wd)).padStart(3, "0")}° ${w.wind_kt ?? 0}${w.gust_kt ? `G${w.gust_kt}` : ""}kt`;
      $("m-vis").textContent = w.vis_sm === null ? "--" : `${w.vis_sm} SM`;
      $("m-raw").textContent = w.metar;
      $("m-raw").title = w.metar;
    }
  }

  // ---------------------------------------------------------------- left lists
  private lists(): void {
    const tracks = [...this.store.tracks.values()].filter((t) => t.data.kind !== "vehicle" && t.data.phase !== "parked");
    const etaKey = (t: Track) => t.data.eta_s ?? 1e6;
    const arr = tracks.filter((t) => t.data.kind === "arrival").sort((a, b) => etaKey(a) - etaKey(b) || a.data.id.localeCompare(b.data.id));
    const dep = tracks.filter((t) => t.data.kind === "departure").sort((a, b) => etaKey(a) - etaKey(b) || a.data.id.localeCompare(b.data.id));
    $("n-arr").textContent = String(arr.length);
    $("n-dep").textContent = String(dep.length);
    this.syncRows($("list-arr"), arr, "arr");
    this.syncRows($("list-dep"), dep, "dep");
  }

  private syncRows(host: HTMLElement, list: Track[], cls: string): void {
    const seen = new Set<string>();
    list.forEach((t, i) => {
      const d = t.data;
      seen.add(d.id);
      let row = this.rows.get(d.id);
      if (!row) {
        row = document.createElement("div");
        row.dataset.id = d.id;
        row.innerHTML = `<div class="row-main"><span class="cs"><i></i><b></b><small class="ty"></small></span><span class="m gt"></span><span class="m rw"></span><span class="eta"></span></div><span class="chip"></span>`;
        this.rows.set(d.id, row);
        row.classList.add("enter");
        setTimeout(() => row?.classList.remove("enter"), 450);
      }
      const keep = row.classList.contains("enter") ? " enter" : "";
      row.className = `row ${cls}${this.store.selected === d.id ? " sel" : ""}${d.phase === "at_gate" || d.phase === "airborne" ? " dim" : ""}${keep}`;
      const set = (sel: string, txt: string) => {
        const el = row!.querySelector(sel)!;
        if (el.textContent !== txt) el.textContent = txt;
      };
      set(".cs b", d.callsign);
      set(".ty", d.type);
      set(".gt", d.gate ?? "—");
      set(".rw", short(d.runway) || "—");
      set(".eta", d.eta_s === null || d.eta_s === undefined ? "—" : mmss(d.eta_s));
      const chip = row.querySelector(".chip") as HTMLElement;
      const txt = d.status_text || d.phase.replace(/_/g, " ");
      const cl = `chip ${tone(d.phase)}`;
      if (chip.textContent !== txt) {
        chip.textContent = txt;
        chip.className = cl + " pop";
        setTimeout(() => chip.classList.remove("pop"), 520);
      } else if (!chip.className.startsWith(cl)) chip.className = cl;
      if (host.children[i] !== row) host.insertBefore(row, host.children[i] ?? null);
    });
    for (const child of [...host.children] as HTMLElement[]) {
      if (!seen.has(child.dataset.id!)) {
        this.rows.delete(child.dataset.id!);
        child.remove();
      }
    }
  }

  // ---------------------------------------------------------------- right panel
  private right(): void {
    this.score();
    const s = this.store;
    const sel = s.selected;
    const decs = s.decisions;
    let dec: Decision | undefined;
    if (sel) for (let i = decs.length - 1; i >= 0; i--) if (decs[i].callsign === sel) { dec = decs[i]; break; }
    const who = sel && !dec ? sel : null;
    if (!sel) dec = decs[decs.length - 1];
    $("dec-title").textContent = sel ? `Decision · ${sel}` : "Latest decision";
    const key = `${sel}|${dec?.id}|${who}`;
    if (key !== this.lastDecKey) {
      this.lastDecKey = key;
      const body = $("decision-body");
      body.innerHTML = dec ? this.decisionHtml(dec) : `<div class="empty">${who ? `No decision recorded yet for ${esc(who)}.` : "Waiting for the tower's first decision…"}</div>`;
      body.firstElementChild?.classList.add("fresh");
    }
    // clearance timeline
    const cs = sel ?? dec?.callsign ?? null;
    $("tl-title").textContent = `Clearance timeline${cs ? " · " + cs : ""}`;
    const list = this.store.clearances.filter((c) => c.callsign === cs).slice(-3).reverse();
    const html = list.length ? list.map((c) => this.clearanceHtml(c)).join("") : `<div class="empty">No clearances issued yet.</div>`;
    if (html !== this.lastTl) {
      this.lastTl = html;
      $("timeline-body").innerHTML = html;
    }
  }

  private score(): void {
    const sc = this.store.score;
    if (!sc) return;
    const bad = (n: number) => (n > 0 ? "bad" : "ok");
    const html = [
      ["completed", sc.completed, "Completed", ""],
      ["avg", `${Math.round(sc.avg_delay_s)}s`, "Avg delay", ""],
      ["thr", sc.throughput_per_10min, "Per 10 min", ""],
      ["sep", sc.separation_losses, "Sep loss", bad(sc.separation_losses)],
      ["rwy", sc.runway_conflicts, "RWY conf", bad(sc.runway_conflicts)],
      ["rb", sc.readback_errors_accepted, "RB errors", bad(sc.readback_errors_accepted)],
    ].map(([, v, l, c]) => `<div class="stat ${c}"><b>${v}</b><span>${l}</span></div>`).join("");
    if (html !== this.lastScore) {
      this.lastScore = html;
      $("score").innerHTML = html;
    }
  }

  private decisionHtml(d: Decision): string {
    const list = (items: string[]) => items.map((x) => `<li>${esc(x)}</li>`).join("");
    return `<div>
      <div class="dec-head"><span class="act">${esc(d.action.replace(/_/g, " "))}</span><span class="who">${esc(d.callsign)}</span><span class="when">T${d.t < 0 ? "-" : "+"}${mmss(Math.abs(d.t))}</span></div>
      <div class="dec-sum">${esc(d.summary)}</div>
      ${d.facts?.length ? `<div class="dec-sec"><h4>Facts used</h4><ul>${list(d.facts)}</ul></div>` : ""}
      ${d.rules?.length ? `<div class="dec-sec rules"><h4>Rules weighed</h4><ul>${list(d.rules)}</ul></div>` : ""}
      ${d.alternatives?.length ? `<div class="dec-sec"><h4>Passed over</h4>${d.alternatives.map((a) => `<div class="alt"><b>${esc(a.option)}</b><span>${esc(a.reason)}</span></div>`).join("")}</div>` : ""}
      ${d.next_review_s ? `<div class="next">Next review in <b id="next-review" data-at="${d.t + d.next_review_s}">--</b></div>` : ""}
    </div>`;
  }

  private nextReview(now: number): void {
    const el = document.getElementById("next-review");
    if (!el) return;
    const left = Number(el.dataset.at) - this.store.simTime(now);
    el.textContent = left > 0 ? `${Math.ceil(left)} s` : "now";
  }

  private clearanceHtml(c: Clearance): string {
    const issued = true;
    const ack = c.state !== "issued" && c.ack_t !== null;
    const done = c.state === "complete";
    const rel = (t: number | null) => (t === null ? "" : `<em>${mmss(t)}</em>`);
    return `<div class="tl"><div class="tl-text">${esc(c.text)}</div><div class="steps">
      <div class="step ${issued ? "on" : ""}"><i></i>Issued ${rel(c.issued_t)}</div><div class="bar ${ack ? "on" : ""}"></div>
      <div class="step ${ack || done ? "on" : ""}"><i></i>Ack ${rel(c.ack_t)}</div><div class="bar ${done ? "on" : ""}"></div>
      <div class="step ${done ? "on" : ""}"><i></i>Complete ${rel(c.done_t)}</div></div></div>`;
  }

  // ---------------------------------------------------------------- voice transcript
  private voice(): void {
    const host = $("voice-list");
    const lines = this.store.voice.slice(-40);
    const sig = lines.map((l) => `${l.id}:${l.status}`).join();
    if (sig === this.lastVoiceSig) return;
    this.lastVoiceSig = sig;
    const stick = host.scrollHeight - host.scrollTop - host.clientHeight < 40;
    const ids = new Set(lines.map((l) => l.id));
    for (const [id, el] of this.voiceIds) if (!ids.has(id)) (el.remove(), this.voiceIds.delete(id));
    for (const l of lines) {
      let el = this.voiceIds.get(l.id);
      if (!el) {
        el = document.createElement("div");
        this.voiceIds.set(l.id, el);
        host.appendChild(el);
        el.innerHTML = this.voiceHtml(l);
      } else {
        const st = el.querySelector(".st") as HTMLElement;
        const want = this.statusLabel(l);
        if (st.textContent !== want.label) (st.textContent = want.label, (st.className = `st ${want.cls}`));
      }
      el.className = `vl ${l.speaker}${l.id === this.speakingId ? " speaking" : ""}`;
    }
    if (stick) host.scrollTop = host.scrollHeight;
  }

  private statusLabel(l: VoiceLine): { label: string; cls: string } {
    if (l.speaker === "agent") return { label: "", cls: "none" };
    return l.status === "ok" ? { label: "readback ok", cls: "" } : l.status === "corrected" ? { label: "corrected", cls: "corrected" } : { label: "pending", cls: "pending" };
  }

  private voiceHtml(l: VoiceLine): string {
    const st = this.statusLabel(l);
    return `<span class="ts">${mmss(l.t)}</span><span class="sp">${l.speaker === "agent" ? "ATC" : "PILOT"}</span><span class="tx">${esc(l.text)}</span><span class="st ${st.cls}">${st.label}</span>`;
  }

  // ---------------------------------------------------------------- runway Gantt
  private drawGantt(now: number): void {
    const cv = this.gantt;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth;
    const h = cv.clientHeight;
    if (!w || !h) return;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
    }
    const g = cv.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const t = this.store.simTime(now);
    const runways: Runway[] = this.store.runways.length ? this.store.runways : [];
    const labelW = 72;
    const axisH = 18;
    const x0 = labelW;
    const x1 = w - 4;
    const span = 360;
    const X = (ts: number) => x0 + ((ts - (t - 180)) / span) * (x1 - x0);
    const rows = Math.max(1, runways.length);
    const rowH = (h - axisH) / rows;
    g.font = "500 10px 'JetBrains Mono', monospace";
    g.textBaseline = "middle";
    // past shading and grid
    g.fillStyle = "rgba(255,255,255,0.03)";
    g.fillRect(x0, 0, X(t) - x0, h - axisH);
    g.strokeStyle = "rgba(255,255,255,0.07)";
    g.fillStyle = "#64748b";
    g.textAlign = "center";
    for (let m = -3; m <= 3; m++) {
      const x = X(t + m * 60);
      g.beginPath();
      g.moveTo(x, 0);
      g.lineTo(x, h - axisH);
      g.stroke();
      g.fillText(m === 0 ? "now" : `${m > 0 ? "+" : "-"}${Math.abs(m)}m`, Math.min(x1 - 12, Math.max(x0 + 10, x)), h - axisH / 2 + 1);
    }
    runways.forEach((r, i) => {
      const y = i * rowH;
      g.fillStyle = "rgba(255,255,255,0.035)";
      roundRect(g, x0, y + 3, x1 - x0, rowH - 6, 7);
      g.fill();
      g.textAlign = "left";
      g.fillStyle = r.status === "occupied" ? "#fca5a5" : r.status === "reserved" ? "#fcd34d" : "#86efac";
      g.font = "700 12px Inter, sans-serif";
      g.fillText(short(r.name), 4, y + rowH / 2 - 6);
      g.font = "500 9.5px Inter, sans-serif";
      g.fillStyle = "#64748b";
      g.fillText(r.status, 4, y + rowH / 2 + 8);
      g.font = "500 10px 'JetBrains Mono', monospace";
      for (const o of r.occupancy) {
        const a = Math.max(x0, X(o.start_s));
        const b = Math.min(x1, X(o.end_s));
        if (b <= a) continue;
        const occ = o.kind === "occupied";
        const col = occ ? "239,68,68" : "245,158,11";
        g.shadowColor = `rgba(${col},0.7)`;
        g.shadowBlur = occ ? 10 : 4;
        g.fillStyle = `rgba(${col},${occ ? 0.88 : 0.28})`;
        roundRect(g, a, y + 7, b - a, rowH - 14, 5);
        g.fill();
        g.shadowBlur = 0;
        if (!occ) {
          g.strokeStyle = `rgba(${col},0.9)`;
          g.setLineDash([4, 3]);
          g.stroke();
          g.setLineDash([]);
        }
        if (b - a > 54) {
          g.fillStyle = "#fff";
          g.textAlign = "left";
          g.fillText(o.callsign, a + 7, y + rowH / 2 + 1);
        }
      }
    });
    // now line
    g.strokeStyle = "rgba(255,255,255,0.75)";
    g.lineWidth = 1.5;
    g.beginPath();
    g.moveTo(X(t), 0);
    g.lineTo(X(t), h - axisH);
    g.stroke();
    g.lineWidth = 1;
    if (!runways.length) {
      g.fillStyle = "#64748b";
      g.textAlign = "center";
      g.fillText("waiting for runway data…", w / 2, h / 2);
    }
  }
}

function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + rr, y);
  g.arcTo(x + w, y, x + w, y + h, rr);
  g.arcTo(x + w, y + h, x, y + h, rr);
  g.arcTo(x, y + h, x, y, rr);
  g.arcTo(x, y, x + w, y, rr);
  g.closePath();
}
