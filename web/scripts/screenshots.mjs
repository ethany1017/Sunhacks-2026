// Usage: node scripts/screenshots.mjs [url]  (needs mock/real API on :8000 and `npm run dev` on :5173)
import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(here, "../../docs/screenshots");
const url = process.argv[2] || "http://localhost:5173/?degrade=0";
const API = process.env.API || "http://localhost:8000";

const browser = await chromium.launch({ channel: "chromium", args: ["--ignore-gpu-blocklist"] });
const sizes = [[1920, 1080, "dashboard-1920.png"], [1280, 720, "dashboard-1280.png"]];
const errors = [];
let fpsReport = {};
for (const [w, h, name] of sizes) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(`[${w}] ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`[${w}] pageerror ${e.message}`));
  await page.goto(url);
  await page.waitForTimeout(8000);
  await page.screenshot({ path: path.join(out, name) });
  const fps = await page.evaluate(() => new Promise((res) => {
    let n = 0; const t0 = performance.now();
    const f = () => { n++; if (performance.now() - t0 < 4000) requestAnimationFrame(f); else res({ fps: n / 4, tracks: document.querySelectorAll(".row").length }); };
    requestAnimationFrame(f);
  }));
  fpsReport[w] = fps;
  if (w === 1920) {
    await page.locator("#list-dep .row").nth(2).click().catch(() => {});
    await page.waitForTimeout(2800);
    await page.screenshot({ path: path.join(out, "dashboard-selected-1920.png") });
    await page.locator("#list-dep .row.sel").click().catch(() => {});
  }
  await ctx.close();
}
await browser.close();
console.log("FPS", JSON.stringify(fpsReport));
console.log("console errors:", errors.length);
errors.slice(0, 10).forEach((e) => console.log(" ", e));
