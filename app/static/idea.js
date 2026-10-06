// =========================================================
// idea.js — the Lab's default: DESCRIBE AN IDEA → MarketLab understands it → tests it → results.
//
//   Trade idea      text → interpretation card (only HIGH-impact questions; assumptions listed)
//                   → match ladder (Exact / Very similar / Broader, every relaxation spelled out)
//                   → the normal results (key numbers first, deep statistics under Advanced)
//   Logged trades   "I went long NQ at 10:14 after…" → the thesis is tested the same way and the
//                   real outcome is placed inside the historical distribution
//   Investment      see invest.js
//
// Under the hood every idea is an ordinary Research Project (same engine, same files), so
// [Advanced] simply opens it in the full editor.
// =========================================================

const IDEA = { project: null, ladder: null, result: null, busy: null, error: null, editing: false, recent: null,
               logged: null, parsed: null, logForm: {}, logEdited: new Set(), needs: null, showAlternatives: false };
const IDEA_PLACEHOLDER = "Describe your trade or market idea…\n\nOn NQ, I'm looking for a bullish 4H FVG as the HTF draw on liquidity. I want sell-side liquidity such as the London low swept first with bullish SMT divergence against ES. During NY morning, after bullish displacement and a market structure break, if a 1m FVG forms with the HTF direction and price retraces to its 50% midpoint, enter long. Stop below the sweep low, take 50% at 2R and target major buy-side liquidity for the rest, no entries after 11:00 ET.";
const LOG_PLACEHOLDER = "Describe the trade you took…\n\nI went long NQ at 10:14 on Sep 30 after the London low was swept and a 1m bullish FVG formed. Entry 29160, stop 28954, out at 29500.";

function renderIdeaLab(lab) {
  const body = lab.el.querySelector(".window-body");
  if (!lab.ideaBound) {
    body.addEventListener("click", ideaClick);
    body.addEventListener("input", ideaInput);
    body.addEventListener("change", ideaChange);
    document.addEventListener("toggle", (e) => {
      if (e.target.matches && e.target.matches("[data-rp-adv]")) RP.advancedOpen = e.target.open;
      if (e.target.matches && e.target.matches("[data-idea-assumptions]")) IDEA.showAssumptions = e.target.open;
    }, true);
    lab.ideaBound = true;
  }
  lab.rendered = false;          // the Quick-test builder re-renders itself when its tab is chosen again
  drawIdea({ keepScroll: false });
  rpMeta().then(() => { if (!IDEA.recent) loadRecentIdeas(); }).catch(() => {});
  writeHash();
}

function ideaMode() { return labState.mode; }

function drawIdea({ keepScroll = true } = {}) {
  if (!["idea", "trades"].includes(labState.mode)) return;
  const body = labState.el.querySelector(".window-body");
  const scroll = body.scrollTop;
  const drafts = [...body.querySelectorAll("[data-idea-text], [data-log-text]")].map((el) => [el.dataset.ideaText !== undefined ? "idea" : "log", el.value]);
  const mode = ideaMode();
  body.innerHTML = labModeBar(mode) + (mode === "trades" ? loggedHtml() : tradeIdeaHtml());
  for (const [k, v] of drafts) {
    const el = body.querySelector(k === "idea" ? "[data-idea-text]" : "[data-log-text]");
    if (el && !el.value) el.value = v;
  }
  if (keepScroll) body.scrollTop = scroll;
}

// ---------------------------------------------------------------- trade idea
function tradeIdeaHtml() {
  const p = IDEA.project && IDEA.project.kind !== "trade_check" ? IDEA.project : null;
  return `
    <div class="lab-head idea-head">
      <div class="lab-kicker">Lab</div>
      <h1>Test a trade idea</h1>
      <div class="aside">Describe the setup in one paragraph. MarketLab works out the rules, tests them on history and shows what happened — no forms.</div>
    </div>
    <div class="idea-box glass">
      <textarea class="idea-input" data-idea-text rows="6" maxlength="12000" placeholder="${escapeHtml(IDEA_PLACEHOLDER)}">${escapeHtml(p && !IDEA.editing ? (p.formalization?.text || "") : "")}</textarea>
      <div class="idea-foot">
        <label class="cap-field"><span>Market</span><select data-idea-inst>${["NQ", "MNQ"].map((s) => `<option ${(p?.instrument?.symbol || "NQ") === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <span class="rp-fine">Intraday futures setups. A question about a stock? Try <button class="link-btn" data-lab-mode="quick">Quick test</button>.</span>
        <span class="rp-spacer"></span>
        <button class="run-btn ${IDEA.busy ? "loading" : ""}" data-idea-go ${IDEA.busy ? "disabled" : ""}>Test this idea <span class="arrow">→</span></button>
      </div>
    </div>
    ${IDEA.error ? `<p class="rp-error">${escapeHtml(IDEA.error)}</p>` : ""}
    ${p ? ideaFlowHtml(p) : ""}
    ${recentIdeasHtml(false)}`;
}

function ideaFlowHtml(p) {
  return `${interpretationHtml(p)}
    <div data-idea-ladder>${IDEA.busy === "test" ? busyHtml("Testing the exact rules, then very similar and broader versions… the first test of a market downloads its data (about 20–40 seconds).") : IDEA.ladder ? ladderHtml(IDEA.ladder) : ""}</div>
    <div data-idea-result>${IDEA.busy === "run" ? busyHtml("Testing every matching setup…") : IDEA.result ? resultHtml(IDEA.result) : ""}</div>`;
}

function busyHtml(text) {
  return `<div class="idea-busy"><span class="idea-spinner" aria-hidden="true"></span>${escapeHtml(text)}</div>`;
}

function interpretationHtml(p) {
  const f = p.formalization;
  if (!f || !f.card) return "";
  const questions = (f.ambiguities || []).filter((a) => !a.answer);
  const assumptions = (f.ambiguities || []).filter((a) => a.auto);
  const empty = !(p.hypothesis.conditions || []).length;
  const tag = { core: "core", secondary: "secondary", execution: "execution" };
  const tested = !!IDEA.ladder;
  return `<section class="idea-card glass" aria-label="How MarketLab understood the idea">
    <header class="idea-card-head"><span class="label">${tested ? "Tested as" : "MarketLab understood"}</span><h2>${escapeHtml(f.card.headline)}</h2>
      <span class="rp-spacer"></span>
      <button class="pill-btn" data-idea-edit>${icon("edit", 13)}Edit</button>
      <button class="pill-btn" data-idea-advanced data-tip="Every definition, condition, timeframe, SMT setting, entry and stop rule in the full editor">${icon("method", 13)}Advanced</button></header>
    ${empty ? `<p class="rp-warn">${icon("warn", 13)} No intraday setup MarketLab can detect was found. Mention concepts like an FVG, a liquidity sweep, displacement, a structure break, SMT or VWAP — or use Quick test for stock questions.</p>` : ""}
    <div class="idea-sections">${f.card.sections.map((sec) => `<div class="idea-sec">
      <span class="idea-sec-title">${escapeHtml(sec.title)}</span>
      ${sec.items.length ? sec.items.map((it) => `<span class="idea-item ${it.untestable ? "untestable" : ""}">${escapeHtml(it.text)}${it.importance && it.importance !== "execution" ? `<em class="imp-${tag[it.importance] || "core"}" data-tip="${it.importance === "core" ? "Core thesis: never relaxed" : "Secondary: may be relaxed in Similar / Broader tests, always visibly"}">${it.importance}</em>` : ""}</span>`).join("")
        : `<span class="idea-item muted">${["Exit", "Stop", "Entry"].includes(sec.title) ? "needs an answer" : "—"}</span>`}
    </div>`).join("")}</div>
    ${questions.length ? `<div class="idea-questions"><div class="label">${questions.length === 1 ? "One thing MarketLab can't safely assume" : `${questions.length} things MarketLab can't safely assume`}</div>
      ${questions.map((a) => `<div class="idea-q"><p>${escapeHtml(a.question)}</p>${a.why ? `<p class="rp-fine">${escapeHtml(a.why)}</p>` : ""}
        <div class="dmt-options">${a.options.map((o) => `<button class="dmt-opt" data-idea-answer="${escapeHtml(a.id)}" data-value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</button>`).join("")}</div></div>`).join("")}</div>` : ""}
    ${assumptions.length || (f.notes || []).length ? `<details class="idea-assume-more" ${IDEA.showAssumptions ? "open" : ""} data-idea-assumptions><summary>Assumptions used (${assumptions.length + (f.notes || []).length}) <span class="rp-fine">change any of them and the test reruns</span></summary>
      <div class="idea-assume">${assumptions.map((a) => assumptionChip(a)).join("")}</div>${(f.notes || []).map((n) => `<p class="rp-fine">${escapeHtml(n)}</p>`).join("")}</details>` : ""}
    ${!tested && !questions.length && !empty ? `<footer class="idea-card-foot"><button class="run-btn ${IDEA.busy ? "loading" : ""}" data-idea-run ${IDEA.busy ? "disabled" : ""}>Run test <span class="arrow">→</span></button></footer>` : ""}
    ${IDEA.editing ? editInterpretationHtml(p) : ""}
  </section>`;
}

function assumptionChip(a) {
  return `<label class="idea-chip" data-tip="${escapeHtml(a.question)}"><span>${escapeHtml(a.label)}</span>
    <select data-idea-assume="${escapeHtml(a.id)}">${a.options.map((o) => `<option value="${escapeHtml(o.value)}" ${o.value === a.answer ? "selected" : ""}>${escapeHtml(o.label)}${o.value === a.suggested ? " (MarketLab default)" : ""}</option>`).join("")}</select></label>`;
}

function editInterpretationHtml(p) {
  const f = p.formalization;
  return `<div class="idea-edit">
    <p class="rp-fine">Change the wording and re-interpret (the original description is kept), or change any default above. For the exact definitions, use Advanced.</p>
    <textarea class="idea-input small" data-idea-rewrite rows="4">${escapeHtml(f.text || "")}</textarea>
    <button class="pill-btn primary-action" data-idea-reinterpret>Re-interpret</button> <button class="link-btn" data-idea-edit-close>Close</button></div>`;
}

// ---------------------------------------------------------------- the match ladder
function ladderHtml(L) {
  const d = L.diagnosis || {};
  const current = IDEA.result?.tier?.relax_ids?.join(",") ?? null;
  const names = { exact: "EXACT", similar: "VERY SIMILAR", broader: "BROADER" };
  const tierCard = (t) => {
    const n = t.setups;
    const on = current !== null && (t.relax_ids || []).join(",") === current;
    return `<div class="ladder-tier t-${t.tier} ${on ? "on" : ""}">
      <div class="ladder-top"><span class="rp-tier-tag">${names[t.tier]}</span>${on ? `<span class="ladder-showing">showing</span>` : ""}</div>
      <div class="ladder-nums"><span><b class="ladder-n">${n == null ? "—" : n}</b><small>${n == null ? "can't test" : "setups"}</small></span>
        ${t.trades != null ? `<span><b class="ladder-n">${t.trades}</b><small>trades</small></span>` : ""}</div>
      <p class="ladder-sum">${escapeHtml(t.summary || "")}</p>
      ${t.relaxations.length ? `<ul class="ladder-relax">${t.relaxations.map((r) => `<li class="c-${r.class || "secondary"}"><span>${escapeHtml(r.original)}</span><i>→</i><b>${escapeHtml(r.relaxed)}</b></li>`).join("")}</ul>` : ""}
      ${n && !on ? `<button class="pill-btn" data-idea-tier="${t.tier}" data-relax="${escapeHtml((t.relax_ids || []).join(","))}">Show ${t.tier === "exact" ? "exact" : t.tier === "similar" ? "very similar" : "broader"} results</button>` : ""}
    </div>`;
  };
  const sim = L.tiers.find((t) => t.tier === "similar");
  const exact = L.tiers.find((t) => t.tier === "exact");
  const cannot = d.verdict === "DATA" || d.verdict === "CONCEPT";
  return `<section class="idea-ladder">
    <div class="ladder-head"><h3>How closely history matched</h3><span class="rp-fine">${escapeHtml(d.history || "")}. Core rules are never relaxed; every change is listed. Nothing is tuned for profit.</span></div>
    ${exact && (!exact.setups || (exact.trades != null && exact.trades < (L.auto?.min_trades || 5))) && d.headline ? `<div class="ladder-why v-${(d.verdict || "pattern").toLowerCase()}">
      <span class="ladder-verdict">${cannot ? "We can't test this exactly" : exact.setups ? "Too few exact trades" : "No exact matches exist in this history"}</span><b>${escapeHtml(d.headline)}</b>
      <ul>${(d.reasons || []).map((r) => `<li><span class="why-kind k-${r.kind}">${{ data: "DATA", concept: "CONCEPT", pattern: "RULES", history: "HISTORY" }[r.kind] || r.kind}</span>${escapeHtml(r.text)}</li>`).join("")}</ul></div>` : ""}
    ${L.auto?.reason && L.auto.tier !== "exact" ? `<p class="ladder-auto">${icon("info", 13)} ${escapeHtml(L.auto.reason)}</p>` : ""}
    <div class="ladder-tiers">${L.tiers.map(tierCard).join("")}</div>
    ${sim && (sim.alternatives || []).length ? `<details class="ladder-alts"><summary>Other relaxations MarketLab counted (${sim.alternatives.length})</summary>
      <ul>${sim.alternatives.map((a) => `<li><b>${a.setups ?? "—"}</b> setups if <span>${escapeHtml(a.original)}</span> → ${escapeHtml(a.relaxed)}</li>`).join("")}</ul>
      <p class="rp-fine">Choices are made by sample size (setups found), never by returns.</p></details>` : ""}
  </section>`;
}

// ---------------------------------------------------------------- logged trades
function loggedHtml() {
  const p = IDEA.project && IDEA.project.kind === "trade_check" ? IDEA.project : null;
  const f = IDEA.logForm;
  const parsed = IDEA.parsed || {};
  const val = (k) => f[k] ?? parsed[k] ?? "";
  const need = new Set(IDEA.needs || []);
  const input = (k, label, type = "text", attrs = "", unit = "") => `<label class="cap-field ${need.has(k) ? "need" : ""}"><span>${label}</span>
    <input type="${type}" data-log-field="${k}" value="${escapeHtml(String(val(k)))}" ${attrs}>${unit ? `<small>${unit}</small>` : ""}</label>`;
  const a = p?.actual_trade;
  return `
    <div class="lab-head idea-head">
      <div class="lab-kicker">Lab · Log a trade</div>
      <h1>Was this trade actually good?</h1>
      <div class="aside">Describe a trade you really took. MarketLab tests the reasoning behind it on history and shows where your result falls among comparable setups.</div>
    </div>
    ${p ? "" : `<div class="idea-box glass">
      <textarea class="idea-input" data-log-text rows="5" maxlength="12000" placeholder="${escapeHtml(LOG_PLACEHOLDER)}"></textarea>
      ${IDEA.parsed ? `<div class="log-understood"><span class="label">Understood</span>${[
        parsed.direction && `<span class="rp-chip">${parsed.direction}</span>`, parsed.date && `<span class="rp-chip">${parsed.date}</span>`,
        parsed.time && `<span class="rp-chip">${parsed.time} ET</span>`, parsed.result_r != null && `<span class="rp-chip">${parsed.result_r >= 0 ? "+" : ""}${parsed.result_r}R</span>`,
        parsed.entry && `<span class="rp-chip">entry ${parsed.entry}</span>`, parsed.stop && `<span class="rp-chip">stop ${parsed.stop}</span>`,
        parsed.exit && `<span class="rp-chip">exit ${parsed.exit}</span>`].filter(Boolean).join("") || `<span class="rp-fine">nothing yet</span>`}</div>` : ""}
      <details class="log-details" ${need.size ? "open" : ""}><summary>Trade details ${need.size ? `<b class="warn">— please add: ${[...need].join(", ")}</b>` : `<span class="rp-fine">optional — filled in from your text</span>`}</summary>
        <div class="cap-row">
          ${input("date", "Date", "date")}${input("time", "Time", "time", "", "ET")}
          <label class="cap-field ${need.has("direction") ? "need" : ""}"><span>Direction</span><select data-log-field="direction"><option value="">—</option>${["long", "short"].map((d) => `<option value="${d}" ${val("direction") === d ? "selected" : ""}>${d === "long" ? "Long" : "Short"}</option>`).join("")}</select></label>
          ${input("entry", "Entry", "number", 'step="0.25" placeholder="—"')}${input("stop", "Stop", "number", 'step="0.25" placeholder="—"')}${input("exit", "Exit", "number", 'step="0.25" placeholder="—"')}
          ${input("result_r", "Result", "number", 'step="0.1" placeholder="—"', "R")}${input("pnl", "PnL", "number", 'step="1" placeholder="—"', "$")}
        </div></details>
      <div class="idea-foot"><label class="cap-field"><span>Market</span><select data-log-inst><option>NQ</option><option>MNQ</option></select></label><span class="rp-spacer"></span>
        <button class="run-btn ${IDEA.busy === "log" ? "loading" : ""}" data-log-go ${IDEA.busy ? "disabled" : ""}>Compare with history <span class="arrow">→</span></button></div>
    </div>`}
    ${IDEA.error ? `<p class="rp-error">${escapeHtml(IDEA.error)}</p>` : ""}
    ${p ? `<div class="log-summary glass-flat"><span class="label">Your trade</span>
        <b>${escapeHtml(p.instrument.symbol)} ${escapeHtml(a?.direction || "")} · ${escapeHtml(a?.date || "")} ${escapeHtml(a?.time || "")} ET</b>
        ${a?.result_r != null ? `<span class="rp-r ${a.result_r >= 0 ? "up" : "down"}">${a.result_r >= 0 ? "+" : ""}${a.result_r.toFixed(2)}R</span>` : ""}
        <span class="rp-spacer"></span><button class="link-btn" data-log-new>${icon("plus", 12)}Log another trade</button></div>
      <blockquote class="rp-quote small">${escapeHtml((p.trade_description || {}).original || "")}</blockquote>
      ${ideaFlowHtml(p)}` : ""}
    ${recentIdeasHtml(true)}`;
}

// ---------------------------------------------------------------- recent
function recentIdeasHtml(logged) {
  const list = (IDEA.recent || []).filter((x) => logged ? x.kind === "trade_check" : x.kind !== "trade_check" && x.mode === "quick").slice(0, 8);
  if (!list.length) return "";
  return `<section class="idea-recent"><h4 class="rp-h4">${logged ? "Logged trades" : "Recent ideas"}</h4><div class="idea-recent-grid">${list.map((x) => {
    const a = x.actual_trade;
    const luck = x.last_luck;
    return `<button class="idea-recent-card glass-flat" data-idea-open="${escapeHtml(x.id)}">
      <span class="idea-recent-top">${statusChip(x.status)}<b>${escapeHtml(x.instrument.symbol)}</b>${a ? `<span class="rp-r ${a.result_r >= 0 ? "up" : "down"}">${a.result_r >= 0 ? "+" : ""}${Number(a.result_r).toFixed(2)}R</span>` : ""}</span>
      <span class="idea-recent-text">${escapeHtml(x.description || x.observation || x.name)}</span>
      <span class="rp-fine">${a ? `${escapeHtml(a.date)} ${escapeHtml(a.time)} ET` : escapeHtml(fmtDateTime(x.updated_at))}${luck && luck.n ? ` · beats ${Math.round(100 * luck.percentile)}% of ${luck.n} similar` : ""}</span></button>`;
  }).join("")}</div></section>`;
}

async function loadRecentIdeas() {
  try { IDEA.recent = (await fetchJson("/api/research/projects")).projects; } catch (e) { IDEA.recent = []; }
  drawIdea();
}

// ---------------------------------------------------------------- actions
async function ideaStep(kind, fn) {
  IDEA.busy = kind; IDEA.error = null;
  drawIdea();
  try { return await fn(); } catch (error) { IDEA.error = error.message; return null; } finally { IDEA.busy = null; drawIdea(); }
}

function setIdeaProject(p) {
  IDEA.project = p;
  RP.project = p;
  if (RP.meta) RP.view = "project";
}

// Paragraph → results: one server call builds the Exact / Very similar / Broader ladder and runs the most
// specific tier with a usable sample (labelled, every relaxation listed).
async function runIdeaTest() {
  const p = IDEA.project;
  if (!p) return;
  IDEA.ladder = null; IDEA.result = null;
  const out = await ideaStep("test", () => postJson(`/api/research/projects/${p.id}/test`, {}));
  if (!out) return;
  IDEA.ladder = out.ladder;
  IDEA.result = out.result;
  if (out.result) { RP.result = out.result; RP.dataset = out.result.dataset; }
  const fresh = await fetchJson(`/api/research/projects/${p.id}`).catch(() => null);
  if (fresh) setIdeaProject(fresh);
  drawIdea();
  labState.el.querySelector(".idea-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
}
const runLadder = runIdeaTest;

function readyToTest(p) {
  const f = p && p.formalization;
  return f && f.status && f.status.ready && !(f.ambiguities || []).some((a) => !a.answer) && (p.hypothesis.conditions || []).length;
}

async function runTier(tier, relaxIds) {
  const p = IDEA.project;
  const r = await ideaStep("run", () => postJson(`/api/research/projects/${p.id}/run`, { tier, relax_ids: relaxIds }));
  if (!r) return;
  IDEA.result = r;
  RP.result = r;
  RP.dataset = r.dataset;
  const fresh = await fetchJson(`/api/research/projects/${p.id}`).catch(() => null);
  if (fresh) setIdeaProject(fresh);
  drawIdea();
  labState.el.querySelector("[data-idea-result]")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function openIdea(id) {
  const p = await fetchJson(`/api/research/projects/${encodeURIComponent(id)}`).catch((e) => { showToast(e.message); return null; });
  if (!p) return;
  Object.assign(IDEA, { ladder: null, result: null, editing: false, error: null, needs: null });
  setIdeaProject(p);
  if (p.kind === "trade_check" && labState.mode !== "trades") labState.mode = "trades";
  if (p.kind !== "trade_check" && labState.mode === "trades") labState.mode = "idea";
  drawIdea({ keepScroll: false });
  writeHash();
}

async function ideaClick(event) {
  if (!["idea", "trades"].includes(labState.mode)) return;
  const t = event.target;
  const el = (sel) => t.closest(sel);
  let hit;
  if (el("[data-idea-go]")) {
    const text = labState.el.querySelector("[data-idea-text]").value.trim();
    if (!text) { showToast("Describe the idea first."); return; }
    const instrument = labState.el.querySelector("[data-idea-inst]").value;
    const same = IDEA.project && IDEA.project.kind !== "trade_check";
    const p = await ideaStep("interpret", async () => {
      const proj = same ? IDEA.project : await postJson("/api/research/projects", { name: text.slice(0, 70), instrument });
      return postJson(`/api/research/projects/${proj.id}/describe`, { text });
    });
    if (p) {
      Object.assign(IDEA, { ladder: null, result: null, editing: false }); setIdeaProject(p); IDEA.recent = null;
      if (readyToTest(p)) return runIdeaTest();
      drawIdea();
    }
    return;
  }
  if ((hit = el("[data-idea-answer]"))) {
    const p = await ideaStep("answer", () => postJson(`/api/research/projects/${IDEA.project.id}/answer`, { answers: { [hit.dataset.ideaAnswer]: hit.dataset.value } }));
    if (p) { setIdeaProject(p); IDEA.ladder = null; IDEA.result = null; if (readyToTest(p)) return runIdeaTest(); drawIdea(); }
    return;
  }
  if (el("[data-idea-run]")) return runLadder();
  if ((hit = el("[data-idea-tier]"))) return runTier(hit.dataset.ideaTier, hit.dataset.relax ? hit.dataset.relax.split(",") : []);
  if (el("[data-idea-exact-only]")) { IDEA.result = null; IDEA.ladder = { ...IDEA.ladder, tiers: IDEA.ladder.tiers.filter((t) => t.tier === "exact") }; drawIdea(); return; }
  if (el("[data-idea-edit]")) { IDEA.editing = !IDEA.editing; drawIdea(); return; }
  if (el("[data-idea-edit-close]")) { IDEA.editing = false; drawIdea(); return; }
  if (el("[data-idea-reinterpret]")) {
    const text = labState.el.querySelector("[data-idea-rewrite]").value.trim();
    const p = await ideaStep("interpret", () => postJson(`/api/research/projects/${IDEA.project.id}/describe`, { text }));
    if (p) { setIdeaProject(p); Object.assign(IDEA, { editing: false, ladder: null, result: null }); if (readyToTest(p)) return runIdeaTest(); drawIdea(); }
    return;
  }
  if (el("[data-idea-advanced]")) {
    labState.mode = "projects";
    resetProjectState();
    RP.project = IDEA.project; RP.view = "project";
    if (RP.project.mode !== "quick") await postJson(`/api/research/projects/${RP.project.id}/mode`, { mode: "quick" }).catch(() => null);
    renderResearch(labState);
    loadDataset();
    return;
  }
  if ((hit = el("[data-idea-open]"))) return openIdea(hit.dataset.ideaOpen);
  if (el("[data-log-new]")) { Object.assign(IDEA, { project: null, ladder: null, result: null, parsed: null, logForm: {}, needs: null }); drawIdea({ keepScroll: false }); return; }
  if (el("[data-log-go]")) {
    const text = labState.el.querySelector("[data-log-text]").value.trim();
    if (!text) { showToast("Describe the trade first."); return; }
    const body = { text, instrument: labState.el.querySelector("[data-log-inst]").value };
    labState.el.querySelectorAll("[data-log-field]").forEach((x) => { if (x.value !== "" && IDEA.logEdited.has(x.dataset.logField)) body[x.dataset.logField] = x.value; });
    const out = await ideaStep("log", () => postJson("/api/research/log-trade", body));
    if (!out) return;
    if (out.needs) { IDEA.needs = out.needs; IDEA.parsed = out.extracted; drawIdea(); return; }
    Object.assign(IDEA, { needs: null, ladder: null, result: null, recent: null });
    setIdeaProject(out);
    drawIdea({ keepScroll: false });
    if (readyToTest(out)) runIdeaTest();
    return;
  }
  // results inside the idea view reuse the Research-project result controls
  if ((hit = el("[data-rp-replay]"))) return openReplay(Number(hit.dataset.rpReplay));
  if ((hit = el("[data-rp-save-trade]"))) return saveTradeAsExample(Number(hit.dataset.rpSaveTrade));
  if (el("[data-rp-more-trades]")) { RP.showAllTrades = true; drawIdea(); return; }
  if ((hit = el("[data-dmt-browse]"))) { DMT.browse = hit.dataset.dmtBrowse; drawIdea(); return; }
  if ((hit = el("[data-dmt-why]"))) {
    const k = Number(hit.dataset.dmtWhy);
    if (DMT.openWhy.has(k)) DMT.openWhy.delete(k); else DMT.openWhy.add(k);
    drawIdea(); return;
  }
  if ((hit = el("[data-rp-combo]"))) {
    const [entry, stop, exit] = hit.dataset.rpCombo.split("|");
    const r = await ideaStep("run", () => postJson(`/api/research/projects/${IDEA.project.id}/run`,
      { combo: { entry, stop, exit }, tier: IDEA.result?.tier?.name, relax_ids: IDEA.result?.tier?.relax_ids || [] }));
    if (r) { IDEA.result = r; RP.result = r; drawIdea(); }
    return;
  }
  if (el("[data-dmt-add-note]")) {
    const input = labState.el.querySelector("[data-dmt-quicknote]");
    if (input && input.value.trim()) {
      await postJson(`/api/research/projects/${IDEA.project.id}/items/notes`, { text: input.value }).then(() => showToast("Saved to the idea's notes")).catch((e) => showToast(e.message));
      input.value = "";
    }
    return;
  }
  if ((hit = el("[data-dmt-ai]")) && hit.dataset.dmtAi === "result") {
    try { DMT.ai.result = await postJson(`/api/research/projects/${IDEA.project.id}/ai`, { action: "result" }); drawIdea(); } catch (e) { showToast(e.message); }
  }
}

let ideaParseTimer = null;
function ideaInput(event) {
  if (!["idea", "trades"].includes(labState.mode)) return;
  const t = event.target;
  if (t.matches("[data-log-text]")) {
    clearTimeout(ideaParseTimer);
    ideaParseTimer = setTimeout(async () => {
      try { IDEA.parsed = await postJson("/api/research/parse-trade", { text: t.value }); } catch (e) { return; }
      const box = labState.el.querySelector(".log-understood");
      const keepFocus = document.activeElement === t;
      const pos = t.selectionStart;
      drawIdea();
      if (keepFocus) { const n = labState.el.querySelector("[data-log-text]"); n.focus(); n.setSelectionRange(pos, pos); }
      void box;
    }, 500);
  }
  if (t.matches("[data-log-field]")) { IDEA.logEdited.add(t.dataset.logField); IDEA.logForm[t.dataset.logField] = t.value; }
}

async function ideaChange(event) {
  if (!["idea", "trades"].includes(labState.mode)) return;
  const t = event.target;
  if (t.matches("[data-idea-assume]")) {
    IDEA.showAssumptions = true;
    const p = await ideaStep("answer", () => postJson(`/api/research/projects/${IDEA.project.id}/answer`, { answers: { [t.dataset.ideaAssume]: t.value } }));
    if (p) { setIdeaProject(p); IDEA.ladder = null; IDEA.result = null; if (readyToTest(p)) return runIdeaTest(); drawIdea(); }
  }
  if (t.matches("[data-log-field]")) { IDEA.logEdited.add(t.dataset.logField); IDEA.logForm[t.dataset.logField] = t.value; }
}
