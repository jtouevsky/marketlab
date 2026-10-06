// Invest UI regression test (browser). Optional: needs Node + Playwright and a running MarketLab.
//   python3 marketlab.py            (in one terminal)
//   npx playwright install chromium (once)  ·  node tests/browser/invest_ui.e2e.js
// Env: MARKETLAB_URL (default http://127.0.0.1:8050), CHROMIUM_PATH (optional browser binary).
// Checks: scenario typing, weight typing, focus preservation, tab lazy loading + pane caching, request
// de-duplication, stale-request rejection, analysis switching / duplicate / inactive unmounting, cached
// result reuse, theme change without rebuilds, and that no overlay steals keys from text fields.

const { chromium } = require("playwright");
const URL = process.env.MARKETLAB_URL || "http://127.0.0.1:8050";
let failures = 0;
const ok = (cond, label, extra = "") => { console.log(`${cond ? "PASS" : "FAIL"}  ${label}${extra ? "  — " + extra : ""}`); if (!cond) failures += 1; };

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${URL}/#invest`);
  await page.evaluate(() => localStorage.removeItem("marketlab.invest.v1"));
  await page.reload();
  await page.waitForSelector("#inv-compose textarea");
  const stat = (k) => page.evaluate((k) => INV.stats[k] || 0, k);

  // ---- analysis with custom weights
  await page.evaluate(() => investStartBasket(["NVDA", "AMD", "AVGO", "TSM", "QQQ"], "Regression"));
  await page.waitForSelector(".inv-progress.done", { timeout: 180000 });
  await page.click("[data-inv-toggle-weights]");
  const w = page.locator('#inv-weights [data-inv-w="NVDA"]');
  for (const v of ["0", "1", "10", "12.5", "33.33", "100"]) {
    await w.click(); await w.fill(""); await w.pressSequentially(v, { delay: 25 });
    const focused = await page.evaluate(() => document.activeElement?.dataset?.invW === "NVDA");
    ok((await w.inputValue()) === v && focused, `weight "${v}" typed, kept and focused`);
  }
  ok(/Total: 1\d\d/.test(await page.locator("#inv-total").innerText()), "total shown without rewriting values");
  await page.click("[data-inv-normalize]");
  ok(Math.abs(await page.evaluate(() => cur().holdings.reduce((s, h) => s + parseFloat(h.w), 0)) - 100) < 0.01, "Normalize sums to 100 only when pressed");

  // ---- scenario box: local draft, no work while typing
  const wi = page.locator("[data-inv-what-if]");
  await wi.scrollIntoViewIfNeeded();
  const req0 = await stat("req"), outRenders0 = await page.evaluate(() => INV.stats.renders.out || 0);
  await wi.click();
  const text = "AI capex slows and rates stay high";
  await wi.pressSequentially(text, { delay: 15 });
  ok((await wi.inputValue()) === text, "scenario text typed exactly (spaces included)");
  ok((await stat("req")) === req0, "typing a scenario sends no requests");
  ok((await page.evaluate(() => INV.stats.renders.out || 0)) === outRenders0, "typing a scenario re-renders nothing");
  await page.keyboard.press("Escape");
  ok((await wi.inputValue()) === text, "Esc keeps the scenario text");
  await page.click("[data-inv-adv] > summary");
  await page.waitForTimeout(200);
  ok((await page.locator("[data-inv-what-if]").inputValue()) === text, "opening Advanced keeps the scenario draft");
  await page.click("[data-inv-what-if-go]");
  await page.waitForFunction(() => document.querySelector("#inv-scen-out")?.textContent.length > 10, null, { timeout: 30000 });
  ok((await wi.inputValue()) === text, "Trace keeps the text and renders a result");
  const req1 = await stat("req");
  await page.click("[data-inv-recent=\"0\"]");
  ok((await stat("req")) === req1, "re-tracing the same scenario is served from cache");
  await page.click("[data-inv-what-if-clear]");
  ok((await wi.inputValue()) === "" && (await page.evaluate(() => document.activeElement.matches("[data-inv-what-if]"))), "× clears and keeps focus");
  await page.click("[data-inv-recent-edit=\"0\"]");
  ok((await wi.inputValue()) === text, "a recent scenario can be copied into the box to modify");

  // ---- a stage landing while typing must not touch the draft (re-analysis keeps the draft too)
  await page.click("[data-inv-refresh]");
  await page.waitForSelector(".inv-progress.done", { timeout: 180000 });
  ok((await page.locator("[data-inv-what-if]").inputValue()) === text, "re-analysis keeps the scenario draft");

  // ---- an overlay elsewhere must not steal keys from text fields (old Replay key handler)
  await page.evaluate(() => { const o = document.createElement("div"); o.className = "rp-replay"; o.style.cssText = "position:fixed;width:1px;height:1px;left:0;top:0"; document.body.appendChild(o); });
  const wi2 = page.locator("[data-inv-what-if]");
  await wi2.fill(""); await wi2.click(); await wi2.pressSequentially("a b", { delay: 20 });
  ok((await wi2.inputValue()) === "a b", "space still types while a Replay overlay exists");
  await page.evaluate(() => document.querySelector(".rp-replay")?.remove());

  // ---- tabs: lazy, cached panes, no requests on return
  await page.locator("#inv-lenses").scrollIntoViewIfNeeded();
  const builds0 = await stat("paneBuilds");
  for (const l of ["dependency", "thematic", "macro", "market"]) await page.click(`[data-inv-lens="${l}"]`);
  const builds1 = await stat("paneBuilds"), reqTabs = await stat("req");
  ok(builds1 - builds0 === 4, "each tab is built on first visit only", `${builds1 - builds0} builds`);
  for (const l of ["map", "dependency", "thematic", "macro", "market", "map"]) {
    const ms = await page.evaluate((l) => { const t = performance.now(); document.querySelector(`[data-inv-lens="${l}"]`).click(); return performance.now() - t; }, l);
    ok(ms < 30, `returning to ${l} is instant`, `${ms.toFixed(1)} ms`);
  }
  ok((await stat("paneBuilds")) === builds1, "returning to a tab rebuilds nothing");
  ok((await stat("req")) === reqTabs, "switching tabs sends no requests");
  await page.click('[data-inv-lens="market"]');
  await page.click('[data-inv-corr-win="3m"]');
  await page.waitForFunction(() => !!R().corr["3m"], null, { timeout: 30000 });
  const reqCorr = await stat("req");
  await page.click('[data-inv-corr-win="1y"]'); await page.click('[data-inv-corr-win="3m"]');
  ok((await stat("req")) === reqCorr, "a loaded correlation window is reused");
  const b2 = await stat("paneBuilds");
  await page.evaluate(() => applyTheme("dark")); await page.waitForTimeout(100); await page.evaluate(() => applyTheme("light"));
  ok((await stat("paneBuilds")) === b2, "theme change rebuilds no graphs");

  // ---- analyses: new / stale / switch / duplicate / unmount
  const firstId = await page.evaluate(() => WS.active);
  await page.click("[data-ws-new]");
  ok(await page.evaluate((id) => WS.active !== id && WS.items[id].holdings.length === 5, firstId), "New analysis leaves the first one intact");
  ok((await page.locator("#inv-out .inv-map").count()) === 0, "the inactive analysis's heavy views are unmounted");
  await page.evaluate(() => { cur().holdings = ["MSFT", "GOOGL"].map((t) => ({ ticker: t, name: "", w: "" })); renderAll(); });
  await page.click("[data-inv-go]");
  const secondId = await page.evaluate(() => WS.active);
  await page.click("[data-ws-menu]"); await page.click(`[data-ws-open="${firstId}"]`);
  const req2 = await stat("req");
  ok(await page.evaluate(() => !!document.querySelector(".inv-progress.done")), "switching back shows cached results immediately");
  await page.waitForFunction((id) => INV.results[id]?.st.full !== "loading", secondId, { timeout: 180000 });
  const shown = await page.evaluate(() => [...document.querySelectorAll(".inv-sum-holdings .mini-tk")].map((e) => e.textContent.split(" ")[0]).join());
  ok(shown === "NVDA,AMD,AVGO,TSM,QQQ", "a finishing background run never overwrites the visible analysis", shown);
  ok((await stat("req")) - req2 <= 2, "cached results reused (only the background run's own requests)");
  await page.click("[data-ws-dup]");
  ok(await page.evaluate(() => cur().name.endsWith("(copy)") && !!R()?.full), "Duplicate copies inputs and reuses results");

  ok(errors.length === 0, "no page errors", errors.join(" | "));
  await browser.close();
  console.log(failures ? `\n${failures} failure(s)` : "\nAll Invest UI checks passed");
  process.exit(failures ? 1 : 0);
})();
