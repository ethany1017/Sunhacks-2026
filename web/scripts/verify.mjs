import { chromium } from "playwright";
import { spawn } from "node:child_process";
const b = await chromium.launch({ channel: "chromium", args: ["--ignore-gpu-blocklist"] });
const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
const errs = [];
p.on("console", (m) => m.type() === "error" && errs.push(m.text()));
p.on("pageerror", (e) => errs.push(e.message));
await p.goto("http://localhost:5173/?degrade=0");
await p.waitForTimeout(7000);
const res = await p.evaluate(() => new Promise((resolve) => {
  const st = window.__store; const prev = new Map(); let n = 0, snaps = 0, hsnaps = 0, maxStep = 0, maxH = 0; let last = performance.now();
  const f = (now) => {
    const dt = (now - last) / 1000; last = now;
    for (const [id, t] of st.tracks) {
      const q = prev.get(id);
      if (q) {
        const step = Math.hypot((t.lon - q[0]) * 93000, (t.lat - q[1]) * 110574);
        const exp = (t.data.speed_mps * dt * st.speed) + 0.3;
        maxStep = Math.max(maxStep, step / exp);
        if (step > 3 * exp + 1) snaps++;
        const dh = Math.abs(((t.hdg - q[2] + 540) % 360) - 180);
        maxH = Math.max(maxH, dh); if (dh > 25) hsnaps++;
      }
      prev.set(id, [t.lon, t.lat, t.hdg]);
    }
    if (++n < 240) requestAnimationFrame(f); else resolve({ frames: n, snaps, hsnaps, maxStepRatio: +maxStep.toFixed(2), maxHeadingStepDeg: +maxH.toFixed(1) });
  };
  requestAnimationFrame(f);
}));
console.log("smoothness", JSON.stringify(res));
// keys
await p.keyboard.press("Space"); await p.waitForTimeout(600);
console.log("paused after Space:", await p.evaluate(() => window.__store.paused));
await p.keyboard.press("Space"); await p.waitForTimeout(400);
await p.keyboard.press("4"); await p.waitForTimeout(800);
console.log("speed after 4:", await p.evaluate(() => window.__store.speed));
const fps10 = await p.evaluate(() => new Promise((r) => { let n = 0; const t0 = performance.now(); const f = () => { n++; performance.now() - t0 < 3000 ? requestAnimationFrame(f) : r(n / 3); }; f(); }));
console.log("fps at 10x:", fps10.toFixed(1));
await p.keyboard.press("1"); await p.keyboard.press("r"); await p.waitForTimeout(1500);
console.log("after reset t:", await p.evaluate(() => window.__store.t));
// reconnect
const pid = (await import("node:child_process")).execSync("pgrep -f mock/server.mjs").toString().trim().split("\n");
pid.forEach((x) => process.kill(+x));
await p.waitForTimeout(3000);
console.log("reconnect badge shown:", await p.evaluate(() => document.getElementById("b-reconnect").classList.contains("show")));
spawn("node", ["mock/server.mjs"], { detached: true, stdio: "ignore" }).unref();
await p.waitForTimeout(12000);
console.log("reconnect badge after restart:", await p.evaluate(() => document.getElementById("b-reconnect").classList.contains("show")), "tracks:", await p.evaluate(() => window.__store.tracks.size));
console.log("errors:", errs.length, errs.slice(0, 5));
await b.close();
