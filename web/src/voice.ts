import type { VoiceLine } from "./types";

const DIGITS = ["zero", "one", "two", "tree", "four", "fife", "six", "seven", "eight", "niner"];
const spokenText = (t: string) => t.replace(/\d+/g, (m) => [...m].map((d) => DIGITS[+d]).join(" "));

const AGENT_PREF = ["Daniel", "Alex", "Google UK English Male", "Arthur", "Fred", "Microsoft Guy", "Microsoft David", "Oliver"];
const PILOT_PREF = ["Samantha", "Karen", "Google US English", "Microsoft Aria", "Microsoft Zira", "Moira", "Tessa", "Victoria"];

/** Speaks transcript lines with two distinct browser voices and a radio squelch. */
export class Voice {
  enabled = true;
  unlocked = false;
  onSpeaking: (id: string | null) => void = () => {};
  onChange: () => void = () => {};
  private queue: VoiceLine[] = [];
  private busy = false;
  private ctx: AudioContext | null = null;
  private voices: SpeechSynthesisVoice[] = [];

  constructor() {
    if ("speechSynthesis" in window) {
      this.voices = speechSynthesis.getVoices();
      speechSynthesis.onvoiceschanged = () => (this.voices = speechSynthesis.getVoices());
    }
  }

  get supported(): boolean {
    return "speechSynthesis" in window;
  }

  unlock(): void {
    if (this.unlocked) return;
    this.unlocked = true;
    try {
      const AC = window.AudioContext ?? (window as any).webkitAudioContext;
      this.ctx = new AC();
      void this.ctx.resume();
    } catch {
      this.ctx = null;
    }
    if (this.supported) {
      const u = new SpeechSynthesisUtterance(" ");
      u.volume = 0;
      speechSynthesis.speak(u);
    }
    this.onChange();
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
    if (!on) {
      this.queue = [];
      if (this.supported) speechSynthesis.cancel();
      this.busy = false;
      this.onSpeaking(null);
    }
    this.onChange();
  }

  push(line: VoiceLine): void {
    if (!this.enabled || !this.unlocked || !this.supported) return;
    this.queue.push(line);
    while (this.queue.length > 3) this.queue.shift();
    this.pump();
  }

  private pick(prefs: string[], avoid?: SpeechSynthesisVoice): SpeechSynthesisVoice | undefined {
    const en = this.voices.filter((v) => v.lang.toLowerCase().startsWith("en") && v !== avoid);
    for (const p of prefs) {
      const f = en.find((v) => v.name.includes(p));
      if (f) return f;
    }
    return en[0];
  }

  private squelch(ms: number, gain = 0.07): void {
    const ctx = this.ctx;
    if (!ctx) return;
    const len = Math.floor(ctx.sampleRate * (ms / 1000));
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 300;
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 3400;
    const g = ctx.createGain();
    g.gain.value = gain;
    src.connect(hp).connect(lp).connect(g).connect(ctx.destination);
    src.start();
  }

  private pump(): void {
    if (this.busy) return;
    const line = this.queue.shift();
    if (!line) return;
    this.busy = true;
    const agent = line.speaker === "agent";
    const vA = this.pick(AGENT_PREF);
    const v = agent ? vA : this.pick(PILOT_PREF, vA);
    const u = new SpeechSynthesisUtterance(spokenText(line.text));
    if (v) {
      u.voice = v;
      u.lang = v.lang;
    }
    u.pitch = agent ? 0.82 : 1.32;
    u.rate = agent ? 0.95 : 1.14;
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      this.squelch(70, 0.05);
      this.onSpeaking(null);
      setTimeout(() => {
        this.busy = false;
        this.pump();
      }, 180);
    };
    u.onend = done;
    u.onerror = done;
    this.squelch(110, 0.08);
    setTimeout(() => {
      if (!this.enabled) return done();
      this.onSpeaking(line.id);
      speechSynthesis.speak(u);
      setTimeout(done, 12000);
    }, 120);
  }
}
