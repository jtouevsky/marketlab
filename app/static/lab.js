// =========================================================
// lab.js — the Lab environment (the interface; the math lives in
// experiments.py, conditions.py, robust.py, challenge.py, dataquality.py).
// Tools around a result (Challenge, inspector, methodology, saving,
// Test elsewhere, sample preview) are in lab_tools.js.
//
//   renderLab(lab)          the sentence builder
//   runExperiment(lab)      POSTs the definition, plays the analysis
//                           sequence, then reveals the results
//   renderLabResult(lab)    headline, distribution, outlier sensitivity,
//                           robustness checks, comparison with normal,
//                           math mode, data quality, episode explorer
//
// lab.def    = ExperimentDefinition (plain JSON, see experiments.py)
// lab.result = last JSON from POST /api/lab/experiment
// lab.entry  = the Notebook entry this experiment was opened from (if any)
//
// The UI never computes statistics itself; it only displays them.
// =========================================================

// Filled from /api/lab/conditions at start-up; this copy covers the
// first paint (and works if that request fails).
let LAB_REGISTRY = {
  categories: [{ id: "PRICE", label: "Price", available: true }],
  conditions: [{ id: "price_move", category: "PRICE", label: "Price move", sentence: "{symbol} {direction} at least {threshold}% within {window}",
    params: {
      direction: { kind: "choice", options: ["falls", "rises"], default: "falls" },
      threshold: { kind: "number", min: 0.5, max: 95, default: 5, unit: "%", choices: [2, 3, 5, 7.5, 10, 15, 20, 30], step: 0.5 },
      window: { kind: "int", min: 1, max: 252, default: 5, unit: "trading days", choices: [1, 3, 5, 10, 21, 63], step: 1 },
    } }],
};
const HORIZON_SPEC = { kind: "int", min: 1, max: 252, unit: "trading days", step: 1, choices: [1, 5, 10, 21, 63, 126, 252] };
const CHOICE_HINTS = { 1: "1 day", 5: "1 week", 10: "2 weeks", 21: "1 month", 63: "1 quarter", 126: "6 months", 252: "1 year" };
const MAX_CONDITIONS = 6;

fetch("/api/lab/conditions").then((r) => r.json()).then((registry) => {
  if (registry && registry.conditions) LAB_REGISTRY = registry;
}).catch(() => {});

function defaultDefinition(symbol = "NVDA") {
  return {
    version: 1,
    instrument: { symbol, asset_class: "equity" },
    timeframe: "1d",
    conditions: [{ id: "c1", type: "price_move", params: { direction: "falls", threshold: 5, window: 5 } }],
    logic: null,   // null = AND over all conditions (the engine also accepts AND/OR trees)
    outcome: { type: "forward_return", horizon: 10 },
    period: { start: null, end: null },
    benchmark: "SPY",
    settings: { overlap: "non_overlapping", entry: "close", trim: 0.05 },
  };
}

// Older saved layouts stored flat parameters; turn them into a definition.
function definitionFromLegacy(p) {
  const d = defaultDefinition(p.ticker || "NVDA");
  d.conditions[0].params = { direction: p.direction || "falls", threshold: p.threshold || 5, window: p.window || 5 };
  d.outcome.horizon = p.forward || 10;
  return d;
}

const conditionSpec = (type) => LAB_REGISTRY.conditions.find((c) => c.id === type);

function paramSpec(lab, path) {
  if (path === "horizon") return HORIZON_SPEC;
  const [index, name] = path.split(".");
  const cond = lab.def.conditions[Number(index.slice(1))];
  return conditionSpec(cond?.type)?.params[name];
}
function getParam(lab, path) {
  if (path === "horizon") return lab.def.outcome.horizon;
  const [index, name] = path.split(".");
  return lab.def.conditions[Number(index.slice(1))].params[name];
}

// Plain-language version of one condition ("NVDA falls at least 5% within 5 trading days")
function conditionText(cond, symbol) {
  const spec = conditionSpec(cond.type);
  if (!spec) return cond.type;
  return spec.sentence.replace(/\{(\w+)\}/g, (_, name) => {
    if (name === "symbol") return symbol;
    const v = cond.params[name];
    const unit = spec.params[name]?.unit;
    return unit === "trading days" ? `${v} trading day${v === 1 ? "" : "s"}` : String(v);
  });
}


// =========================================================
// Quick-test templates: starting sentences for common swing questions.
// Each one is an ordinary definition; the sentence stays fully editable.
// Templates that need data MarketLab doesn't have historically are shown, disabled, with the reason.
// =========================================================
const Q = (conditions, horizon = 10, extra = {}) => ({ conditions: conditions.map((c, i) => ({ id: `c${i + 1}`, ...c })), outcome: { type: "forward_return", horizon }, logic: null, ...extra });
const QUICK_TEMPLATES = [
  { id: "pm-month", group: "Price move", title: "After a 15% monthly selloff…", text: "Down 15%+ over 21 trading days — what happened over the next 3 months?", def: Q([{ type: "price_move", params: { direction: "falls", threshold: 15, window: 21 } }], 63) },
  { id: "pm-week", group: "Price move", title: "After a sharp weekly drop…", text: "Down 10%+ in 5 days — what happened over the next 2 weeks?", def: Q([{ type: "price_move", params: { direction: "falls", threshold: 10, window: 5 } }]) },
  { id: "pm-dd", group: "Price move", title: "After a 30% drawdown…", text: "30%+ below its 1-year high — what happened over the next 3 months?", def: Q([{ type: "drawdown_from_high", params: { threshold: 30, lookback: 252 } }], 63) },
  { id: "pm-gapdown", group: "Price move", title: "After a gap down…", text: "Opens 5%+ below the previous close — what happened next?", def: Q([{ type: "gap", params: { direction: "falls", threshold: 5 } }], 10) },
  { id: "er-beatdrop", group: "Earnings", title: "After an earnings beat, but the stock drops…", text: "EPS beat, yet the stock falls 3%+ that session", def: Q([{ type: "earnings", params: { result: "an EPS beat", reaction: "falls", threshold: 3 } }], 21) },
  { id: "er-beat", group: "Earnings", title: "After an earnings beat and a rally…", text: "EPS beat and the stock rises 5%+ that session", def: Q([{ type: "earnings", params: { result: "an EPS beat", reaction: "rises", threshold: 5 } }], 21) },
  { id: "er-miss", group: "Earnings", title: "After an earnings miss…", text: "EPS miss and the stock falls 5%+ that session", def: Q([{ type: "earnings", params: { result: "an EPS miss", reaction: "falls", threshold: 5 } }], 21) },
  { id: "vol-unusual", group: "Volume", title: "After unusual volume…", text: "Volume 3× its 20-day average", def: Q([{ type: "relative_volume", params: { op: "above", multiple: 3, lookback: 20 } }]) },
  { id: "vol-up", group: "Volume", title: "After a high-volume up day…", text: "Volume 2× average while rising 3%+", def: Q([{ type: "relative_volume", params: { op: "above", multiple: 2, lookback: 20 } }, { type: "price_move", params: { direction: "rises", threshold: 3, window: 1 } }]) },
  { id: "vol-capit", group: "Volume", title: "After a capitulation day…", text: "Volume 3× average on a 5%+ drop", def: Q([{ type: "relative_volume", params: { op: "above", multiple: 3, lookback: 20 } }, { type: "price_move", params: { direction: "falls", threshold: 5, window: 1 } }]) },
  { id: "mo-high", group: "Momentum", title: "After a breakout to a 1-year high…", text: "Closes at a new 252-day high", def: Q([{ type: "new_extreme", params: { kind: "high", lookback: 252 } }], 21) },
  { id: "mo-200", group: "Momentum", title: "After reclaiming the 200-day…", text: "Crosses above its 200-day moving average", def: Q([{ type: "ma_cross", params: { op: "above", period: 200 } }], 63) },
  { id: "mo-trend", group: "Momentum", title: "After a strong 3-month run…", text: "Up 25%+ in 63 days, still above the 50-day", def: Q([{ type: "price_move", params: { direction: "rises", threshold: 25, window: 63 } }, { type: "ma_position", params: { op: "above", period: 50 } }], 21) },
  { id: "mo-dip", group: "Momentum", title: "After a dip in an uptrend…", text: "RSI below 35 while above the 200-day", def: Q([{ type: "rsi", params: { op: "below", level: 35, period: 14 } }, { type: "ma_position", params: { op: "above", period: 200 } }]) },
  { id: "fu-pe", group: "Valuation", title: "When P/E falls while revenue keeps growing…", text: "Cheaper on earnings, business still growing", def: null,
    note: "Not testable yet: it needs point-in-time fundamentals (what was known on each past date). Today's numbers would leak the future into the test." },
  { id: "fu-pullback", group: "Valuation", title: "When a fast grower pulls back 25%…", text: "Revenue growth 20%+ and a 25% drawdown", def: null,
    note: "Not testable yet for the same reason: historical revenue growth as it was reported at the time isn't in MarketLab's data." },
  { id: "ev-shock", group: "News / event", title: "After a major news-sized move…", text: "A 7%+ one-day move on 3× volume (an event proxy)", def: Q([{ type: "price_move", params: { direction: "rises", threshold: 7, window: 1 } }, { type: "relative_volume", params: { op: "above", multiple: 3, lookback: 20 } }]),
    note: "Uses price + volume as a stand-in: MarketLab has no point-in-time news archive, so it can't test the headlines themselves (partnerships, regulation)." },
  { id: "ev-drop", group: "News / event", title: "After a regulatory-style shock…", text: "A 7%+ one-day drop on 3× volume (an event proxy)", def: Q([{ type: "price_move", params: { direction: "falls", threshold: 7, window: 1 } }, { type: "relative_volume", params: { op: "above", multiple: 3, lookback: 20 } }]),
    note: "A proxy: big moves on heavy volume usually coincide with news, but the cause isn't checked." },
];
const QT_GROUPS = [...new Set(QUICK_TEMPLATES.map((t) => t.group))];

function quickTemplatesHtml(lab) {
  const group = lab.qtGroup || QT_GROUPS[0];
  return `<div class="qt-templates" data-qt-templates>
    <div class="qt-groups" role="tablist" aria-label="Template groups">${QT_GROUPS.map((g) => `<button role="tab" class="${g === group ? "on" : ""}" aria-selected="${g === group}" data-qt-group="${escapeHtml(g)}">${escapeHtml(g)}</button>`).join("")}</div>
    <div class="qt-cards">${QUICK_TEMPLATES.filter((t) => t.group === group).map((t) => `<button class="qt-card ${lab.qtTemplate === t.id ? "on" : ""}" ${t.def ? `data-qt-template="${t.id}"` : "disabled"} ${t.note ? `title="${escapeHtml(t.note)}"` : ""}>
      <b>${escapeHtml(t.title)}</b><span>${escapeHtml(t.text)}</span>${t.note ? `<small>${escapeHtml(t.note)}</small>` : ""}</button>`).join("")}</div></div>`;
}


// =========================================================
// The sentence builder
// =========================================================
function renderLab(lab) {
  if (lab.mode === "projects") { renderResearch(lab); return; }   // research.js
  if (lab.mode === "invest") { lab.mode = "idea"; switchEnv("invest"); }
  if (["idea", "trades"].includes(lab.mode)) { renderIdeaLab(lab); return; }   // idea.js
  const body = lab.el.querySelector(".window-body");
  body.innerHTML = `${labModeBar("quick")}
    <div class="lab-head">
      <div class="lab-kicker">Lab · Quick test</div>
      <h1>What usually happens after…?</h1>
      <div class="aside">“What usually happens after…?” for one stock. Start from a question below, then change any bubble.</div>
    </div>
    ${quickTemplatesHtml(lab)}
    <div data-origin></div>
    <div class="builder glass">
      <div class="sentence" data-sentence></div>
      <div data-tray></div>
      <div class="sample-preview" data-preview aria-live="polite"></div>
      <div class="builder-foot">
        <span class="restate" data-restate></span>
        <button class="run-btn" data-run data-tip="Run experiment (${navigator.platform.includes("Mac") ? "⌘" : "Ctrl"} + Enter)">Run experiment <span class="arrow">→</span></button>
      </div>
    </div>
    <div data-entry-bar></div>
    <nav class="crumbs" data-crumbs aria-label="Where you are in this experiment"></nav>
    <div data-output></div>`;
  drawSentence(lab);
  drawEntryBar(lab);
  drawOrigin(lab);
  updateLabStatus(lab, null);
  schedulePreview(lab, 0);

  body.querySelector("[data-run]").addEventListener("click", () => runExperiment(lab));
  if (!lab.bound) {   // the body element persists across re-renders: attach once
    body.addEventListener("keydown", (event) => labKeys(lab, event));
    body.addEventListener("click", (event) => labClick(lab, event));
    body.addEventListener("wheel", (event) => labWheel(lab, event), { passive: false });
    lab.bound = true;
  }
  if (lab.result) renderLabResult(lab);
}

const CHEV = '<svg class="chev" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function pillHtml(lab, path, spec, value, glued = "") {
  if (spec.kind === "choice") {
    const label = path.endsWith(".direction") ? (value === "falls" ? "↘ falls" : "↗ rises") : escapeHtml(value);
    const dir = path.endsWith(".direction") ? ` dir-${value}` : "";
    const next = spec.options[(spec.options.indexOf(value) + 1) % spec.options.length];
    return `<button class="pill${dir}" data-pill="${path}" data-tip="Click to switch to “${escapeHtml(next)}”" aria-label="${escapeHtml(value)}, press to switch">${label}</button>`;
  }
  const days = spec.unit === "trading days" ? ` <span class="unit">${value === 1 ? "trading day" : "trading days"}</span>` : "";
  return `<button class="pill" data-pill="${path}" data-tip="Click for choices · ↑ ↓ or scroll to adjust" aria-label="${escapeHtml(`${value} ${spec.unit || ""}`)}">${value}${glued}${days}${CHEV}</button>`;
}

function conditionHtml(lab, cond, index) {
  const spec = conditionSpec(cond.type);
  if (!spec) return `<span class="word">[unknown condition ${escapeHtml(cond.type)}]</span>`;
  const template = spec.sentence.replace(/^\{symbol\}\s*/, "");
  const parts = template.split(/(\{\w+\})/);
  let html = "";
  for (let k = 0; k < parts.length; k++) {
    const m = parts[k].match(/^\{(\w+)\}$/);
    if (!m) { if (parts[k].trim()) html += `<span class="word">${escapeHtml(parts[k].trim())}</span> `; continue; }
    // A unit written right after a value ("%", "×", "-day") belongs inside its pill
    let glued = "";
    const unit = parts[k + 1] && parts[k + 1].match(/^([%×]|-[a-z]+)/);
    if (unit) { glued = unit[0]; parts[k + 1] = parts[k + 1].slice(glued.length); }
    html += `${pillHtml(lab, `c${index}.${m[1]}`, spec.params[m[1]], cond.params[m[1]], glued)} `;
  }
  return html.trim();
}

// Small fixed-size controls at the end of each condition line (no layout shift on hover)
function conditionControls(lab, index) {
  const n = lab.def.conditions.length;
  const b = (action, iconName, tip, disabled = false) =>
    `<button class="cond-ctl" data-cond-action="${action}" data-index="${index}" data-tip="${tip}" aria-label="${tip}" ${disabled ? "disabled" : ""}>${icon(iconName, 13)}</button>`;
  return `<span class="cond-ctls">
    ${b("up", "chevron-up", "Move up", index === 0)}${b("down", "chevron-down", "Move down", index === n - 1)}
    ${b("duplicate", "duplicate", "Duplicate", n >= MAX_CONDITIONS)}${b("remove", "close", "Remove", n === 1)}</span>`;
}

function drawSentence(lab) {
  const d = lab.def;
  const sentence = lab.el.querySelector("[data-sentence]");
  // One line per condition: WHEN … / AND … / AND … / WHAT HAPPENS over the next …?
  const ticker = `<span class="ticker-group"><label class="pill" data-pill="ticker" data-tip="Type any ticker, then press Enter"><input value="${escapeHtml(d.instrument.symbol)}" maxlength="10" spellcheck="false" aria-label="Ticker"></label><button class="mini-link" data-open-research data-tip="Open ${escapeHtml(d.instrument.symbol)} in Research (your experiment stays here)" aria-label="Open in Research">${icon("external", 12)}</button></span>`;
  const lines = d.conditions.map((c, i) => `
    <div class="s-line${lab.enterIndex === i ? " enter" : ""}" data-line="${i}">
      <span class="kw">${i ? "and" : "When"}</span>
      <span class="s-body">${i === 0 ? `${ticker} ` : ""}${conditionHtml(lab, c, i)}</span>
      ${conditionControls(lab, i)}
    </div>`).join("");
  lab.enterIndex = null;
  sentence.innerHTML = `${lines}
    <div class="s-line s-then"><span class="kw">then</span><span class="s-body"><span class="word">what happens over the next</span>
      <span class="nowrap">${pillHtml(lab, "horizon", HORIZON_SPEC, d.outcome.horizon)}<span class="word">?</span></span></span></div>
    <div class="s-tools">${d.conditions.length < MAX_CONDITIONS ? `<button class="add-cond" data-add-cond aria-haspopup="true">${icon("plus", 14)}Add condition</button>` : `<span class="na">Up to ${MAX_CONDITIONS} conditions</span>`}</div>`;

  const input = sentence.querySelector('[data-pill="ticker"] input');
  const fit = () => { input.style.width = `${Math.max(3, input.value.length) + 0.9}ch`; };
  fit();
  input.addEventListener("input", fit);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); input.blur(); runExperiment(lab); }
  });
  input.addEventListener("change", () => setLabParam(lab, "ticker", input.value.trim().toUpperCase() || d.instrument.symbol));

  const n = d.conditions.length;
  lab.el.querySelector("[data-restate]").textContent =
    `Finds every day when ${n === 1 ? "this was" : `all ${n} conditions were`} true (using only data known by that day's close), ` +
    `then measures the next ${d.outcome.horizon} trading days. Overlapping episodes count once.`;
}

function setLabParam(lab, path, value) {
  if (path === "ticker") {
    if (lab.def.instrument.symbol === value) return;
    lab.def.instrument.symbol = value;
  } else if (path === "horizon") {
    if (lab.def.outcome.horizon === value) return;
    lab.def.outcome.horizon = value;
  } else {
    const [index, name] = path.split(".");
    const params = lab.def.conditions[Number(index.slice(1))].params;
    if (params[name] === value) return;
    params[name] = value;
  }
  const focused = document.activeElement?.closest?.("[data-pill]")?.dataset.pill;
  drawSentence(lab);
  if (focused && focused !== "ticker") lab.el.querySelector(`[data-pill="${focused}"]`)?.focus();
  saveLabState();
  schedulePreview(lab);
  lab.challenge = null;
  // After a first run, edits re-run automatically (debounced); the first run is always deliberate.
  if (lab.result) {
    clearTimeout(lab.timer);
    lab.timer = setTimeout(() => runExperiment(lab, { quick: true }), 450);
  }
}

function closeTray(lab) {
  lab.el.querySelector("[data-tray]").innerHTML = "";
  lab.el.querySelectorAll(".pill.open, .add-cond.open").forEach((p) => p.classList.remove("open"));
}

function labClick(lab, event) {
  if (lab.mode !== "quick") return;
  const t = event.target;
  const tpl = t.closest("[data-qt-template]");
  if (tpl) {
    const T = QUICK_TEMPLATES.find((x) => x.id === tpl.dataset.qtTemplate);
    if (T && T.def) { lab.qtTemplate = T.id; openLab({ ticker: lab.def.instrument.symbol, preset: T.def }); }
    return;
  }
  const tplGroup = t.closest("[data-qt-group]");
  if (tplGroup) { lab.qtGroup = tplGroup.dataset.qtGroup; const el = lab.el.querySelector("[data-qt-templates]"); if (el) el.outerHTML = quickTemplatesHtml(lab); return; }
  const pill = t.closest("[data-pill]");
  const choice = t.closest("[data-choice]");
  const tray = lab.el.querySelector("[data-tray]");

  if (choice) { setLabParam(lab, choice.dataset.key, Number(choice.dataset.choice)); closeTray(lab); return; }
  if (t.closest("[data-add-cond]")) { openConditionPicker(lab, t.closest("[data-add-cond]")); return; }
  if (t.closest("[data-pick-cond]")) {
    const type = t.closest("[data-pick-cond]").dataset.pickCond;
    const spec = conditionSpec(type);
    const params = Object.fromEntries(Object.entries(spec.params).map(([k, v]) => [k, v.default]));
    lab.def.conditions.push({ id: newConditionId(lab), type, params });
    lab.enterIndex = lab.def.conditions.length - 1;
    conditionsChanged(lab);
    return;
  }
  const ctl = t.closest("[data-cond-action]");
  if (ctl && !ctl.disabled) {
    const i = Number(ctl.dataset.index), list = lab.def.conditions;
    const action = ctl.dataset.condAction;
    if (action === "remove") list.splice(i, 1);
    if (action === "duplicate") { list.splice(i + 1, 0, { ...JSON.parse(JSON.stringify(list[i])), id: newConditionId(lab) }); lab.enterIndex = i + 1; }
    if (action === "up" && i > 0) [list[i - 1], list[i]] = [list[i], list[i - 1]];
    if (action === "down" && i < list.length - 1) [list[i + 1], list[i]] = [list[i], list[i + 1]];
    conditionsChanged(lab);
    const target = action === "up" ? i - 1 : action === "down" ? i + 1 : null;
    if (target !== null) lab.el.querySelector(`[data-line="${target}"] [data-cond-action="${action}"]`)?.focus();
    return;
  }
  if (t.closest("[data-open-research]")) { openResearch(lab.def.instrument.symbol); return; }
  if (t.closest("[data-restore-previous]")) { restorePrevious(lab); return; }
  if (t.closest("[data-challenge]")) { runChallenge(lab); return; }
  if (t.closest("[data-methodology]")) { openMethodology(lab); return; }
  if (t.closest("[data-crumb]")) { goCrumb(lab, t.closest("[data-crumb]").dataset.crumb); return; }
  if (t.closest("[data-inspect]")) { inspectEpisode(lab, Number(t.closest("[data-inspect]").dataset.inspect)); return; }
  if (t.closest("[data-calc]")) { const d = t.closest("[data-calc]").nextElementSibling; d.hidden = !d.hidden; return; }
  if (t.closest("[data-sort]")) { lab.sort = t.closest("[data-sort]").dataset.sort; lab.showAll = false; renderExplorer(lab); return; }
  if (t.closest("[data-show-all]")) { lab.showAll = true; renderExplorer(lab); return; }
  if (t.closest("[data-flagged-only]")) { lab.flaggedOnly = !lab.flaggedOnly; renderExplorer(lab); return; }
  if (t.closest("[data-math]")) { lab.math = !lab.math; renderLabResult(lab, { keepScroll: true }); return; }
  if (t.closest("[data-quality-all]")) { lab.qualityAll = !lab.qualityAll; renderQuality(lab); return; }
  if (t.closest("[data-save]")) { openSaveForm(lab); return; }
  if (t.closest("[data-elsewhere-run]")) { runElsewhere(lab); return; }
  if (t.closest("[data-peer]")) { togglePeer(lab, t.closest("[data-peer]")); return; }
  if (t.closest("[data-elsewhere]")) { openElsewhere(lab); return; }
  if (t.closest("[data-suggest]")) {
    const s = JSON.parse(t.closest("[data-suggest]").dataset.suggest);
    if (s.horizon) lab.def.outcome.horizon = s.horizon;
    if (s.path) setLabParam(lab, s.path, s.value);
    drawSentence(lab); runExperiment(lab);
    return;
  }
  if (!pill) return;

  const path = pill.dataset.pill;
  if (path === "ticker") return;   // typing happens in the input
  const spec = paramSpec(lab, path);
  if (spec.kind === "choice") {
    const value = getParam(lab, path);
    setLabParam(lab, path, spec.options[(spec.options.indexOf(value) + 1) % spec.options.length]);
    return;
  }
  // Numbers: open the choice tray under the sentence (click again to close)
  const wasOpen = pill.classList.contains("open");
  closeTray(lab);
  if (wasOpen) return;
  pill.classList.add("open");
  const current = getParam(lab, path);
  const pct = spec.unit === "%";
  tray.innerHTML = `
    <div class="choice-tray glass">
      ${spec.choices.map((value) => `
        <button class="choice ${value === current ? "on" : ""}" data-choice="${value}" data-key="${path}" ${spec.unit === "trading days" && CHOICE_HINTS[value] ? `data-tip="${CHOICE_HINTS[value]}"` : ""}>${value}${pct ? "%" : spec.unit === "×" ? "×" : ""}</button>`).join("")}
      <span class="tray-label">or type</span>
      <label class="pill" style="font-size:15px;height:40px"><input class="num" data-custom="${path}" value="${current}" inputmode="decimal"><span class="unit">${escapeHtml(pct ? "%" : spec.unit === "trading days" ? "days" : spec.unit || "")}</span></label>
      <span class="tray-label range">${spec.min}–${spec.max}</span>
    </div>`;
  const custom = tray.querySelector("[data-custom]");
  custom.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const value = clampParam(spec, Number(custom.value));
    if (Number.isFinite(value)) { setLabParam(lab, path, value); closeTray(lab); }
  });
}

// The floating "+ Add condition" selector: categories, with the ones not built yet shown as such
function openConditionPicker(lab, button) {
  const tray = lab.el.querySelector("[data-tray]");
  const wasOpen = button.classList.contains("open");
  closeTray(lab);
  if (wasOpen) return;
  button.classList.add("open");
  const used = new Set(lab.def.conditions.map((c) => c.type));
  tray.innerHTML = `
    <div class="cond-picker glass">
      ${LAB_REGISTRY.categories.map((cat) => {
        const items = LAB_REGISTRY.conditions.filter((c) => c.category === cat.id);
        if (!cat.available || !items.length) {
          return `<div class="cp-cat off"><span class="cp-label">${escapeHtml(cat.label)}</span><span class="cp-later">later</span></div>`;
        }
        return `<div class="cp-cat"><span class="cp-label">${escapeHtml(cat.label)}</span>
          ${items.map((c) => `<button class="cp-item" data-pick-cond="${c.id}" ${used.has(c.id) && c.id === "price_move" ? 'data-tip="Already used; adding it again lets you combine two different moves"' : ""}>
            <b>${escapeHtml(c.label)}</b><span>${escapeHtml(conditionText({ type: c.id, params: Object.fromEntries(Object.entries(c.params).map(([k, v]) => [k, v.default])) }, lab.def.instrument.symbol))}</span></button>`).join("")}</div>`;
      }).join("")}
      <p class="cp-note">Conditions combine with AND. Each one you add usually shrinks the number of past episodes, so watch the sample size.</p>
    </div>`;
}

// Keyboard: ↑/→ and ↓/← nudge a focused number pill; Escape closes trays.
function labKeys(lab, event) {
  if (lab.mode !== "quick") return;
  if (event.key === "Escape") { closeTray(lab); return; }
  if (event.key === "Enter" && event.target.matches(".event[data-inspect]")) { inspectEpisode(lab, Number(event.target.dataset.inspect)); return; }
  const pill = event.target.closest("button[data-pill]");
  if (!pill) return;
  const spec = paramSpec(lab, pill.dataset.pill);
  if (!spec || spec.kind === "choice") return;
  const up = event.key === "ArrowUp" || event.key === "ArrowRight";
  const down = event.key === "ArrowDown" || event.key === "ArrowLeft";
  if (!up && !down) return;
  event.preventDefault();
  setLabParam(lab, pill.dataset.pill, clampParam(spec, getParam(lab, pill.dataset.pill) + (up ? 1 : -1) * spec.step));
}

function labWheel(lab, event) {
  if (lab.mode !== "quick") return;
  const pill = event.target.closest("button[data-pill]");
  if (!pill) return;
  const spec = paramSpec(lab, pill.dataset.pill);
  if (!spec || spec.kind === "choice") return;
  event.preventDefault();
  setLabParam(lab, pill.dataset.pill, clampParam(spec, getParam(lab, pill.dataset.pill) + (event.deltaY < 0 ? spec.step : -spec.step)));
}

function clampParam(spec, value) {
  if (!Number.isFinite(value)) return NaN;
  const step = spec.step || 1;
  const rounded = spec.kind === "int" ? Math.round(value) : Math.round(value / step) * step;
  return Math.min(spec.max, Math.max(spec.min, +rounded.toFixed(4)));
}


// =========================================================
// Running: a short, honest analysis sequence
// =========================================================
async function postJson(url, body, method = "POST") {
  let response;
  try {
    response = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (error) {
    throw new Error("Can't reach the MarketLab server. Is it still running in Terminal?");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

async function runExperiment(lab, { quick = false } = {}) {
  const d = lab.def;
  const tickerInput = lab.el.querySelector('[data-pill="ticker"] input');
  if (tickerInput) d.instrument.symbol = tickerInput.value.trim().toUpperCase() || d.instrument.symbol;
  closeTray(lab);
  const output = lab.el.querySelector("[data-output]");
  const button = lab.el.querySelector("[data-run]");
  const runId = (lab.runId || 0) + 1;
  lab.runId = runId;

  button.disabled = true;
  button.classList.add("loading");
  output.innerHTML = `<div class="sequence"><div class="seq-line active">Loading ${escapeHtml(d.instrument.symbol)}'s full price history and checking it…</div></div>`;
  updateLabStatus(lab, "loading");

  let result;
  try {
    result = await postJson("/api/lab/experiment", d);
  } catch (error) {
    if (lab.runId !== runId) return;
    button.disabled = false;
    button.classList.remove("loading");
    output.innerHTML = `<div class="warning" style="margin-top:28px">${escapeHtml(error.message)}</div>`;
    updateLabStatus(lab, "error", error.message);
    return;
  }
  if (lab.runId !== runId) return;   // a newer run started meanwhile

  const data = result.data;
  const n = result.stats ? result.stats.n : 0;
  const q = data.quality.summary;
  const steps = [
    `Loaded ${data.trading_days.toLocaleString("en-US")} trading days (${fmtDay(data.first_date)} – ${fmtDay(data.last_date)})`,
    `Data checks: ${q.likely_error ? `${q.likely_error} likely error${q.likely_error === 1 ? "" : "s"} corrected` : "no likely errors"}, ${q.warning} warning${q.warning === 1 ? "" : "s"}, ${q.info} note${q.info === 1 ? "" : "s"}`,
    `Found ${result.qualifying_days.toLocaleString("en-US")} day${result.qualifying_days === 1 ? "" : "s"} where ${d.conditions.length === 1 ? "the condition was" : `all ${d.conditions.length} conditions were`} true`,
    `Collapsed overlapping days into ${n.toLocaleString("en-US")} separate episode${n === 1 ? "" : "s"}`,
    `Calculated the next ${d.outcome.horizon} trading days after each episode`,
    `Compared with ${(result.baseline?.n || 0).toLocaleString("en-US")} ordinary ${d.outcome.horizon}-day periods`,
    `Ran robustness checks: trimmed and winsorized means, bootstrap intervals, outlier sensitivity`,
  ];
  const sequence = output.querySelector(".sequence");
  // The work is already done on the server; the steps only describe it. Keep them brief.
  const delay = quick ? 0 : 110;
  sequence.innerHTML = "";
  for (const step of steps) {
    if (quick) break;
    sequence.insertAdjacentHTML("beforeend", `<div class="seq-line done">${escapeHtml(step)}</div>`);
    await new Promise((resolve) => setTimeout(resolve, delay));
    if (lab.runId !== runId) return;
  }
  await new Promise((resolve) => setTimeout(resolve, quick ? 0 : 140));
  if (lab.runId !== runId) return;

  button.disabled = false;
  button.classList.remove("loading");
  lab.result = result;
  lab.showAll = false;
  lab.elsewhere = null;
  lab.challenge = null;
  lab.crumb = "result";
  renderLabResult(lab);
  updateLabStatus(lab, "ok");
}

function updateLabStatus(lab, state, message) {
  const bar = lab.el.querySelector(".window-status");
  const r = lab.result;
  const chip = state ? `<span class="src-chip ${state}" data-tip="${escapeHtml(message || "Daily closes adjusted for splits and dividends, checked by MarketLab's data-quality rules")}">Price history · Yahoo</span>` : "";
  const fresh = r ? `<span class="freshness">data through ${fmtDay(r.data.last_date)}</span>` : "";
  bar.innerHTML = chip + '<span class="src-chip off" data-tip="The Lab uses no AI. Every number is arithmetic on historical prices.">No AI</span>' + fresh;
}


// =========================================================
// Results
// =========================================================
function renderLabResult(lab, { keepScroll = false } = {}) {
  const r = lab.result;
  const output = lab.el.querySelector("[data-output]");
  const body = lab.el.querySelector(".window-body");
  const scroll = body.scrollTop;
  const d = r.definition;
  const s = r.stats;
  const b = r.baseline;
  const H = d.outcome.horizon;
  const whenText = d.conditions.map((c) => conditionText(c, r.ticker)).join(" and ");

  const warnings = r.warnings.map((w) => `<div class="warning">${escapeHtml(w)}</div>`).join("");
  if (!s) {
    const first = d.conditions[0];
    const suggestions = [];
    if (first.type === "price_move") {
      const t = Math.max(0.5, first.params.threshold / 2);
      suggestions.push(`<button class="pill-btn" data-suggest='${JSON.stringify({ path: "c0.threshold", value: t })}'>Try ${t}%</button>`);
      const w = Math.min(252, first.params.window * 2);
      suggestions.push(`<button class="pill-btn" data-suggest='${JSON.stringify({ path: "c0.window", value: w })}'>Try ${w} days</button>`);
    }
    output.innerHTML = `
      <div class="result">
        <div class="headline">No completed episodes when ${escapeHtml(whenText)}.</div>
        ${warnings}
        <p class="note">Loosen a condition or remove one: every extra condition makes matches rarer.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">${suggestions.join("")}</div>
      </div>`;
    drawCrumbs(lab);
    return;
  }

  const pendingCount = r.events.filter((e) => e.status === "pending").length;
  const flagged = r.events.filter((e) => e.flags).length;
  const rb = r.robust;

  output.innerHTML = `
    <div class="result">
      <div class="headline" id="lab-result">
        In <b>${s.n}</b> past episode${s.n === 1 ? "" : "s"} when ${escapeHtml(whenText)},
        the next ${H} days returned <span class="${signClass(s.mean)}">${fmtPct(s.mean, true, 2)}</span> <em>on average</em>;
        the median was <span class="${signClass(s.median)}">${fmtPct(s.median, true, 2)}</span>.
      </div>
      ${warnings}

      <div class="hero-stats">
        <div class="hero-stat">
          <span class="label">${term("lab_mean", "Average forward return")}</span>
          <div class="value ${signClass(s.mean)}">${fmtPct(s.mean, true, 2)}</div>
          <div class="ci"><span class="keep sample-badge ${s.n < 30 ? "small" : ""}">${term("lab_n", "sample")} n = <b>${s.n}</b></span>
            ${s.ci_low !== null ? ` · ${term("lab_ci", "95% interval")} <span class="keep"><b>${fmtPct(s.ci_low, true, 2)}</b> to <b>${fmtPct(s.ci_high, true, 2)}</b></span>` : ""}</div>
        </div>
        <div>
          <div class="stat-grid">
            ${statCell("lab_median", "Median", signed(fmtPct(s.median, true, 2), s.median))}
            ${statCell("lab_positive", "Positive", `${fmtPct(s.positive_rate, false, 0)}<small>${s.positives} of ${s.n}</small>`)}
            ${statCell("lab_std", "Std dev", s.std !== null ? fmtPct(s.std, false, 2) : NA)}
            ${statCell(null, "Best", `<span class="up">${fmtPct(s.max, true, 1)}</span><small>${fmtDay(s.best.date)}</small>`)}
            ${statCell(null, "Worst", `<span class="down">${fmtPct(s.min, true, 1)}</span><small>${fmtDay(s.worst.date)}</small>`)}
            ${statCell(null, "Negative", `${fmtPct(s.negative_rate, false, 0)}<small>${s.negatives} of ${s.n}</small>`)}
          </div>
          <div class="ratio-bar"><span class="pos" style="flex:${s.positives}"></span><span class="neg" style="flex:${Math.max(s.negatives, 0)}"></span></div>
          <div class="ratio-legend"><span>${s.positives} gained</span><span>${s.negatives} lost${s.flat ? ` · ${s.flat} flat` : ""}</span></div>
        </div>
      </div>

      <div class="result-actions">
        <button class="action-btn primary-action glass-flat" data-challenge>${icon("challenge", 16)}Challenge this result</button>
        <button class="action-btn glass-flat" data-save>${icon("save", 16)}Save to notebook</button>
        <button class="action-btn glass-flat" data-elsewhere>${icon("duplicate", 16)}Test elsewhere</button>
        <button class="link-btn" data-open-research>${icon("research", 14)}Open ${escapeHtml(r.ticker)} research</button>
        <button class="link-btn" data-methodology>${icon("method", 14)}Methodology</button>
      </div>
      <div data-save-form></div>
      <div data-elsewhere-panel></div>

      ${section("A", "Every outcome, one dot each", "click a dot to inspect it", `
        <div class="dist-wrap"><div class="dist" data-dist></div></div>
        <div class="dist-legend">
          <span><i class="k up"></i>gained</span><span><i class="k down"></i>lost</span>
          <span><i class="k line"></i>average after condition</span><span><i class="k dash"></i>normal ${H}-day average</span>
          ${s.ci_low !== null ? '<span><i class="k band"></i>95% interval for the average</span>' : ""}
        </div>
        <p class="note">Each dot is one episode, placed by what happened over the following ${H} trading days.
          A tight cluster means consistent outcomes; a long tail means a few episodes carry the average.</p>`)}

      ${section("B", "Compared with normal", `${b.n.toLocaleString("en-US")} ordinary periods`, compareHtml(r))}

      <div data-challenge-out></div>

      <details class="disclose" ${lab.open?.robust ? "open" : ""} data-disclose="robust">
        <summary><span class="idx">C</span><h3>Robustness</h3><span class="source">outliers, concentration, checks</span></summary>
        ${section("", "How much do outliers matter?", "same data, different averages", sensitivityHtml(r))}
        ${section("", "Where the gains come from", "concentration", contributionHtml(r))}
        ${section("", "Factual checks", "things that could make this result wrong", checksHtml(r))}
      </details>

      <details class="disclose" ${lab.open?.deep ? "open" : ""} data-disclose="deep">
        <summary><span class="idx">D</span><h3>Deep statistics</h3><span class="source">formulas · data checks · every episode</span></summary>
        <div style="margin-top:14px">${mathHtml(r)}</div>
        ${section("", "Data checks", qualitySubtitle(r), '<div data-quality></div>')}
        ${section("", "Every episode", `${s.n} completed${pendingCount ? ` · ${pendingCount} in progress` : ""}${flagged ? ` · ${flagged} flagged` : ""} · click a row to inspect`, `
          <div class="explorer-tools">
            <span class="label">Sort</span>
            ${[["newest", "Newest"], ["oldest", "Oldest"], ["best", "Best"], ["worst", "Worst"]].map(([key, label]) =>
              `<button class="pill-btn ${(lab.sort || "newest") === key ? "active" : ""}" data-sort="${key}">${label}</button>`).join("")}
            ${flagged ? `<button class="pill-btn ${lab.flaggedOnly ? "active" : ""}" data-flagged-only>${icon("warn", 13)} Flagged only</button>` : ""}
          </div>
          <div data-explorer></div>`)}
      </details>

      <p class="data-foot">
        Data: ${escapeHtml(r.data.source)}; tested ${fmtDay(r.data.tested_from)} – ${fmtDay(r.data.tested_to)}.
        ${r.qualifying_days.toLocaleString("en-US")} qualifying days → ${s.n} non-overlapping episodes. Past frequencies are not predictions.
        <button class="link-btn" data-methodology>How this was calculated</button>
      </p>
    </div>`;

  output.querySelectorAll("[data-disclose]").forEach((el) => el.addEventListener("toggle", () => {
    lab.open = { ...(lab.open || {}), [el.dataset.disclose]: el.open };
    if (el.open && el.dataset.disclose === "deep") { renderQuality(lab); renderExplorer(lab); }
  }));
  drawDistribution(lab);
  if (lab.open?.deep) { renderQuality(lab); renderExplorer(lab); }
  if (lab.challenge) renderChallenge(lab);
  drawCrumbs(lab);
  if (keepScroll) body.scrollTop = scroll;
  observeDistribution(lab);
}

function contributionHtml(r) {
  const c = r.robust.contribution;
  if (!c.gains.length) return '<p class="slot-msg">Too few positive outcomes to measure concentration.</p>';
  const bars = c.gains.map((g) => `
    <div class="contrib-row"><span>Top ${g.k} gain${g.k === 1 ? "" : "s"}</span>
      <span class="contrib-track"><i style="width:${(g.share * 100).toFixed(1)}%"></i></span><b>${fmtPct(g.share, false, 0)}</b></div>`).join("");
  const top3 = c.gains.find((g) => g.k === 3);
  const reading = top3 && top3.share >= 0.5
    ? `The three largest gains supply ${fmtPct(top3.share, false, 0)} of all the gains combined: the result depends heavily on a few episodes.`
    : top3 ? `The three largest gains supply ${fmtPct(top3.share, false, 0)} of all the gains combined.` : "";
  return `${bars}<p class="note">Share of the sum of all ${c.n_gains} positive outcomes contributed by the largest ones. ${escapeHtml(reading)}
    (Shares of the net total aren't shown: the net can be close to zero or change sign, which makes percentages of it meaningless.)</p>`;
}

function statCell(key, label, valueHtml) {
  return `<div class="stat-cell"><span class="label">${key ? term(key, label) : label}</span><div class="v">${valueHtml}</div></div>`;
}

// ---------- Outlier sensitivity ----------
function sensitivityHtml(r) {
  const s = r.stats, rb = r.robust;
  const ext = rb.most_extreme;
  const rows = [
    { label: "Average · all episodes", key: "lab_mean", value: s.mean, sub: `n = ${s.n}`, ci: rb.bootstrap_mean_ci, ciLabel: "bootstrap" },
    { label: "Average without the most extreme", value: rb.mean_without_extreme, sub: `drops ${fmtDay(ext.date)} (${stripTags(fmtPct(ext.value, true, 1))})` },
    { label: "Median", key: "lab_median", value: s.median, sub: "middle outcome", ci: rb.bootstrap_median_ci, ciLabel: "bootstrap" },
    { label: `Trimmed mean · ${fmtPct(rb.trim_fraction, false, 0)}`, key: "trimmed_mean", value: rb.trimmed_mean, sub: rb.trimmed_mean === null ? "sample too small" : `${rb.trimmed_each_side} removed from each end` },
    { label: `Winsorized mean · ${fmtPct(rb.trim_fraction, false, 0)}`, key: "winsorized_mean", value: rb.winsorized_mean, sub: rb.winsorized_mean === null ? "sample too small" : `${rb.winsorized_each_side} pulled in at each end` },
  ];
  const vals = rows.flatMap((x) => [x.value, ...(x.ci || [])]).filter(isNum);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = hi - lo || 0.01;
  const pos = (v) => ((v - lo) / span) * 100;

  const mean = s.mean;
  const change = isNum(rb.mean_without_extreme) ? rb.mean_without_extreme - mean : 0;
  const material = Math.abs(change) > Math.max(0.25 * Math.abs(mean), 0.002);
  const trimmedFlip = isNum(rb.trimmed_mean) && (rb.trimmed_mean > 0) !== (mean > 0);
  let verdict;
  if (material) {
    verdict = `One extreme episode (${fmtDay(ext.date)}, ${fmtPct(ext.value, true, 1)}) materially ${change < 0 ? "raises" : "lowers"} the ordinary average: without it, the average is ${fmtPct(rb.mean_without_extreme, true, 2)} instead of ${fmtPct(mean, true, 2)}.`;
  } else {
    verdict = "No single episode changes the average much.";
  }
  if (trimmedFlip) verdict += ` After trimming the extremes, the average changes sign (${fmtPct(rb.trimmed_mean, true, 2)}).`;
  else if (isNum(rb.trimmed_mean) && Math.abs(rb.trimmed_mean - mean) > Math.max(0.3 * Math.abs(mean), 0.003)) {
    verdict += ` The trimmed mean (${fmtPct(rb.trimmed_mean, true, 2)}) is far from the ordinary average: the tails are doing a lot of the work.`;
  }
  if (isNum(rb.largest_gain_share) && rb.largest_gain_share >= 0.25 && mean > 0) {
    verdict += ` The largest single gain is ${fmtPct(rb.largest_gain_share, false, 0)} of all gains combined.`;
  }

  return `
    <p class="verdict" style="margin-top:0">${escapeHtml(verdict)}</p>
    <div class="sens">
      ${rows.map((x) => `
        <div class="sens-row">
          <div class="sens-label">${x.key ? term(x.key, x.label) : escapeHtml(x.label)}<small>${escapeHtml(x.sub)}</small></div>
          <div class="sens-value ${signClass(x.value)}">${isNum(x.value) ? fmtPct(x.value, true, 2) : NA}${x.ci && x.ci[0] !== null ? `<small>${x.ciLabel} ${fmtPct(x.ci[0], true, 1)} to ${fmtPct(x.ci[1], true, 1)}</small>` : ""}</div>
          <div class="sens-track">
            <span class="zero" style="left:${pos(0)}%"></span>
            ${x.ci && x.ci[0] !== null ? `<span class="ci" style="left:${pos(x.ci[0])}%;width:${Math.max(0.5, pos(x.ci[1]) - pos(x.ci[0]))}%"></span>` : ""}
            ${isNum(x.value) ? `<span class="dot ${signClass(x.value)}" style="left:${pos(x.value)}%"></span>` : ""}
          </div>
        </div>`).join("")}
    </div>
    <p class="note">Every episode stays in the data; these are different ways of averaging the same ${s.n} outcomes.
      ${term("bootstrap", "Bootstrap intervals")} estimate how uncertain the historical average and median are by re-sampling the observed episodes ${r.robust.bootstrap_samples.toLocaleString("en-US")} times.
      They describe uncertainty about the past sample, not a range for future returns.</p>`;
}

// ---------- Robustness checks (factual; no score) ----------
function checksHtml(r) {
  if (!r.checks.length) return '<p class="slot-msg">Not enough episodes to check.</p>';
  const mark = (ok) => ok === true ? `<span class="chk ok">${icon("check", 14)}</span>` : ok === false ? `<span class="chk bad">${icon("warn", 14)}</span>` : `<span class="chk info">${icon("info", 14)}</span>`;
  return `<ul class="checks">${r.checks.map((c) => `<li>${mark(c.ok)}<span>${escapeHtml(c.text)}</span></li>`).join("")}</ul>
    <p class="note">${icon("check", 12)} means the result survived that check; ${icon("warn", 12)} means it's a reason for doubt. There's no overall score: one serious ⚠ can matter more than several ✓.
      Not yet checked: transaction costs, other market regimes, neighbouring parameters, out-of-sample periods.</p>`;
}

function compareHtml(r) {
  const s = r.stats, b = r.baseline;
  const H = r.definition.outcome.horizon;
  const diff = r.difference;
  let verdict = "";
  if (r.baseline_inside_ci === true) {
    verdict = `The normal ${H}-day average (${fmtPct(b.mean, true, 2)}) sits <b>inside</b> the 95% interval for the after-condition average
      (${fmtPct(s.ci_low, true, 2)} to ${fmtPct(s.ci_high, true, 2)}). With this many episodes, the difference can't be told apart from ordinary variation.`;
  } else if (r.baseline_inside_ci === false) {
    verdict = `The normal ${H}-day average (${fmtPct(b.mean, true, 2)}) sits <b>outside</b> the 95% interval for the after-condition average
      (${fmtPct(s.ci_low, true, 2)} to ${fmtPct(s.ci_high, true, 2)}). That is a reason to look closer, not proof: test enough ideas on the same history and some will look like this by chance.`;
  }
  return `
    <div class="versus">
      <div class="vs-side">
        <span class="label">After the condition</span>
        <div class="value ${signClass(s.mean)}">${fmtPct(s.mean, true, 2)}</div>
        <div class="sub">average · median ${stripTags(fmtPct(s.median, true, 2))} · ${fmtPct(s.positive_rate, false, 0)} positive · n = ${s.n}</div>
      </div>
      <div class="vs-orb glass-flat">
        <div class="value ${signClass(diff)}">${diff >= 0 ? "+" : "−"}${Math.abs(diff * 100).toFixed(2)}</div>
        <span class="label">pts difference</span>
      </div>
      <div class="vs-side right">
        <span class="label">Any ${H}-day period</span>
        <div class="value ${signClass(b.mean)}">${fmtPct(b.mean, true, 2)}</div>
        <div class="sub">average · median ${stripTags(fmtPct(b.median, true, 2))} · ${fmtPct(b.positive_rate, false, 0)} positive · ${b.n.toLocaleString("en-US")} periods</div>
      </div>
    </div>
    ${s.mean > 0 && diff < 0 ? `<div class="warning">Positive on average (${fmtPct(s.mean, true, 2)}), but <b>below</b> a normal ${H}-day period (${fmtPct(b.mean, true, 2)}). Doing nothing special did better in this history.</div>` : ""}
    <p class="verdict">${verdict}</p>
    <dl class="facts compact-facts">
      <div class="fact"><dt class="label">Difference in averages</dt><dd class="${signClass(diff)}">${diff >= 0 ? "+" : "−"}${Math.abs(diff * 100).toFixed(2)} pts</dd></div>
      <div class="fact"><dt class="label">Difference in medians</dt><dd class="${signClass(r.median_difference)}">${isNum(r.median_difference) ? `${r.median_difference >= 0 ? "+" : "−"}${Math.abs(r.median_difference * 100).toFixed(2)} pts` : NA}</dd></div>
      ${r.benchmark ? `<div class="fact"><dt class="label">${escapeHtml(r.benchmark.symbol)} over the same ${r.benchmark.n} windows</dt><dd class="${signClass(r.benchmark.mean)}">${fmtPct(r.benchmark.mean, true, 2)} avg</dd></div>` : ""}
    </dl>
    <p class="note">"Normal" = the ${H}-day return starting from every trading day in the same years. Those periods overlap,
      so they describe what's typical rather than forming independent samples.${r.benchmark ? ` The ${escapeHtml(r.benchmark.symbol)} line shows what the broad market did during exactly the same episode windows.` : ""}</p>`;
}


// ---------- Math mode ----------
function subscript(number) {
  return String(number).replace(/\d/g, (digit) => "₀₁₂₃₄₅₆₇₈₉"[digit]);
}

function mathHtml(r) {
  const s = r.stats, b = r.baseline, rb = r.robust, d = r.definition;
  const H = d.outcome.horizon;
  const example = [...r.events].reverse().find((e) => e.status === "complete");
  const pct = (v, dd = 2) => stripTags(fmtPct(v, true, dd));
  const pm = d.conditions.find((c) => c.type === "price_move");
  const trigger = pm
    ? `<div class="formula">M(t) = P<sub>t</sub> / P<sub>t−${pm.params.window}</sub> − 1 ${pm.params.direction === "falls" ? "≤ −" : "≥ +"}${pm.params.threshold}%</div>`
    : '<div class="formula">C₁(t) ∧ C₂(t) ∧ …</div>';
  return `
    <div class="math">
      <div class="math-block">
        <h4>When does an episode start?</h4>
        ${trigger}
        <p>${d.conditions.length > 1 ? `Day t qualifies when all ${d.conditions.length} conditions are true at its close. ` : ""}P<sub>t</sub> is the adjusted close on trading day t.
          Each condition only uses data up to day t, so nothing from the future leaks in. After an episode, the next can't start until its ${H}-day window has ended.</p>
      </div>
      <div class="math-block">
        <h4>What we measure after it</h4>
        <div class="formula">r<sub>i</sub> = P<sub>t+${H}</sub> / P<sub>t</sub> − 1</div>
        ${example ? `<div class="plug">e.g. ${fmtDay(example.trigger_date)}: ${example.entry_price.toFixed(2)} → ${example.exit_price.toFixed(2)} (${fmtDay(example.exit_date)}) = ${pct(example.forward_return)}</div>` : ""}
        <p>Prices are adjusted for splits and dividends, so older prices look smaller than what was quoted at the time; the percentages are unaffected.</p>
      </div>
      <div class="math-block">
        <h4>Average return</h4>
        <div class="formula">r̄ = (1/n) Σ r<sub>i</sub></div>
        <div class="plug">r̄ = (r₁ + r₂ + … + r${subscript(s.n)}) / ${s.n} = ${pct(s.mean)}</div>
        <p>Every outcome counts equally, so one huge outcome can move it a lot.</p>
      </div>
      <div class="math-block">
        <h4>Trimmed and winsorized means</h4>
        <div class="formula">sort r, drop k = ⌊${rb.trim_fraction}·n⌋ from each end</div>
        <div class="plug">k = ${rb.trimmed_each_side ?? 0} → trimmed ${isNum(rb.trimmed_mean) ? pct(rb.trimmed_mean) : "n/a"} · winsorized ${isNum(rb.winsorized_mean) ? pct(rb.winsorized_mean) : "n/a"}</div>
        <p>Trimming removes the k most extreme outcomes on each side before averaging; winsorizing replaces them with the nearest remaining value instead. With 10 or more episodes, at least one is trimmed per side.</p>
      </div>
      ${s.std !== null ? `
      <div class="math-block">
        <h4>95% interval (t-distribution)</h4>
        <div class="formula">r̄ ± t · s / √n</div>
        <div class="plug">${pct(s.mean)} ± ${s.t_crit.toFixed(2)} × ${stripTags(fmtPct(s.std, false, 2))} / √${s.n} → [${pct(s.ci_low)}, ${pct(s.ci_high)}]</div>
        <p class="not"><b>It does not mean</b> 95% of future episodes land in this range. It assumes outcomes are roughly bell-shaped, which fat-tailed returns often aren't; the bootstrap interval below doesn't.</p>
      </div>
      <div class="math-block">
        <h4>Bootstrap interval</h4>
        <div class="formula">repeat ${rb.bootstrap_samples}×: draw n outcomes with replacement, recompute</div>
        <div class="plug">mean [${pct(rb.bootstrap_mean_ci[0])}, ${pct(rb.bootstrap_mean_ci[1])}] · median [${pct(rb.bootstrap_median_ci[0])}, ${pct(rb.bootstrap_median_ci[1])}]</div>
        <p>The middle 95% of the re-computed statistics. Fixed random seed, so the same data always gives the same interval.</p>
      </div>` : ""}
      <div class="math-block">
        <h4>Difference from normal</h4>
        <div class="formula">Δ = r̄<sub>after</sub> − r̄<sub>normal</sub></div>
        <div class="plug">${pct(s.mean)} − ${pct(b.mean)} = ${r.difference >= 0 ? "+" : "−"}${Math.abs(r.difference * 100).toFixed(2)} pts</div>
        <p>r̄<sub>normal</sub> averages the ${H}-day return from every trading day in the same history (${b.n.toLocaleString("en-US")} periods).</p>
      </div>
    </div>`;
}


// ---------- Data quality (inspectable) ----------
function qualitySubtitle(r) {
  const q = r.data.quality.summary;
  return `${q.likely_error} likely error${q.likely_error === 1 ? "" : "s"} · ${q.warning} warning${q.warning === 1 ? "" : "s"} · ${q.info} note${q.info === 1 ? "" : "s"}`;
}

function renderQuality(lab) {
  const holder = lab.el.querySelector("[data-quality]");
  const r = lab.result;
  if (!holder || !r) return;
  const q = r.data.quality;
  const label = { info: "Info", warning: "Warning", likely_error: "Likely error" };
  const checks = q.checks || [];
  const issues = q.issues || [];
  const order = { likely_error: 0, warning: 1, info: 2 };
  const sorted = [...issues].sort((a, b) => order[a.severity] - order[b.severity] || String(b.date).localeCompare(String(a.date)));
  const shown = lab.qualityAll ? sorted : sorted.slice(0, 8);
  holder.innerHTML = `
    <p class="aside" style="margin-bottom:12px">Before any statistic, every price series is checked. Strong evidence of bad data (an unadjusted split, a one-day bad print) is corrected;
      real extreme moves are <b>kept</b> and noted, because deleting real crashes would make history look safer than it was.</p>
    ${checks.length ? `<div class="check-chips">${checks.map((c) => `<span class="chip-flat" data-tip="${escapeHtml(c.description || "")}">${escapeHtml(c.label || c.id || c)}</span>`).join("")}</div>` : ""}
    ${issues.length ? `
      <div class="issues">${shown.map((x) => `
        <div class="issue ${x.severity}">
          <span class="sev ${x.severity}">${label[x.severity] || x.severity}</span>
          <span class="date">${x.date ? fmtDay(x.date) : "—"}</span>
          <span class="detail">${escapeHtml(x.detail)}</span>
          <span class="act">${escapeHtml(x.action)}</span>
        </div>`).join("")}</div>
      ${sorted.length > 8 ? `<button class="pill-btn" style="margin-top:12px" data-quality-all>${lab.qualityAll ? "Show fewer" : `Show all ${sorted.length}`}</button>` : ""}`
    : '<p class="slot-msg">No issues found in the tested range.</p>'}
    <p class="note">Showing issues inside the tested range (${fmtDay(r.data.tested_from)} – ${fmtDay(r.data.tested_to)}). Episodes whose window contains a flagged day are marked ${icon("warn", 12)} in the list below.</p>`;
}


// ---------- Distribution: a dot plot, one dot per episode ----------
function drawDistribution(lab) {
  const r = lab.result;
  const holder = lab.el.querySelector("[data-dist]");
  if (!holder || !r || !r.stats) return;
  const H = r.definition.outcome.horizon;
  const pm = r.definition.conditions.find((c) => c.type === "price_move");

  const events = r.events.map((e, index) => ({ ...e, index })).filter((e) => e.status === "complete");
  const values = events.map((e) => e.forward_return);
  const sorted = [...values].sort((a, b) => a - b);
  const quantile = (q) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))];
  const s = r.stats, b = r.baseline;

  let lo = values.length < 40 ? sorted[0] : quantile(0.02);
  let hi = values.length < 40 ? sorted[sorted.length - 1] : quantile(0.98);
  const marks = [0, s.mean, b.mean, s.ci_low, s.ci_high].filter(isNum);
  lo = Math.min(lo, ...marks); hi = Math.max(hi, ...marks);
  const pad = (hi - lo) * 0.06 || 0.01;
  lo -= pad; hi += pad;

  const width = Math.max(300, holder.clientWidth || 600);
  const margin = { left: 34, right: 34 };
  const plotW = width - margin.left - margin.right;
  const x = (v) => margin.left + ((v - lo) / (hi - lo)) * plotW;

  const binCount = Math.max(14, Math.min(64, Math.round(plotW / 13)));
  const binWidth = (hi - lo) / binCount;
  const stacks = new Map();
  for (const e of events) {
    let bin;
    if (e.forward_return < lo) bin = -1;
    else if (e.forward_return > hi) bin = binCount;
    else bin = Math.min(binCount - 1, Math.floor((e.forward_return - lo) / binWidth));
    if (!stacks.has(bin)) stacks.set(bin, []);
    stacks.get(bin).push(e);
  }
  const tallest = Math.max(...[...stacks.values()].map((list) => list.length));
  const binPx = plotW / binCount;
  const radius = Math.max(2.6, Math.min(6, binPx / 2 - 1, 180 / (2 * tallest)));
  const plotH = Math.max(150, tallest * radius * 2 + 10);
  const top = 34;
  const baseY = top + plotH;
  const height = baseY + 30;
  const binX = (bin) => bin === -1 ? margin.left - 16 : bin === binCount ? width - margin.right + 16 : margin.left + (bin + 0.5) * binPx;

  const span = (hi - lo) * 100;
  const step = [0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200].find((st) => span / st <= 7) || 500;
  const ticks = [];
  for (let t = Math.ceil(lo * 100 / step) * step; t <= hi * 100 + 1e-9; t += step) ticks.push(t);

  let svg = `<svg viewBox="0 0 ${width} ${height}" height="${height}" role="img" aria-label="Distribution of ${events.length} forward returns">`;
  if (isNum(s.ci_low)) svg += `<rect class="ci-band" x="${x(s.ci_low)}" y="${top}" width="${Math.max(1, x(s.ci_high) - x(s.ci_low))}" height="${plotH}" rx="3"/>`;
  svg += '<g class="axis">';
  for (const t of ticks) {
    svg += `<line x1="${x(t / 100)}" x2="${x(t / 100)}" y1="${top}" y2="${baseY}"/>`;
    svg += `<text x="${x(t / 100)}" y="${baseY + 18}" text-anchor="middle">${t > 0 ? "+" : t < 0 ? "−" : ""}${Math.abs(t)}%</text>`;
  }
  svg += `<line x1="${margin.left}" x2="${width - margin.right}" y1="${baseY}" y2="${baseY}"/></g>`;
  svg += `<line class="zero" x1="${x(0)}" x2="${x(0)}" y1="${top - 6}" y2="${baseY}"/>`;

  for (const [bin, list] of stacks) {
    list.sort((a, b2) => a.forward_return - b2.forward_return);
    list.forEach((e, k) => {
      const cls = e.forward_return > 0 ? "up" : e.forward_return < 0 ? "down" : "flat";
      const trig = pm && isNum(e.trigger_return) ? `<span>Trigger</span><span>${stripTags(fmtPct(e.trigger_return, true, 1))} in ${pm.params.window}d</span>` : "";
      const tip = `<b>${fmtDay(e.trigger_date)}</b><div class="tt-grid">${trig}` +
        `<span>Next ${H}d</span><span>${stripTags(fmtPct(e.forward_return, true, 2))}</span>` +
        `<span>Price</span><span>${e.entry_price.toFixed(2)} → ${e.exit_price.toFixed(2)}</span><span>Ended</span><span>${fmtDay(e.exit_date)}</span>` +
        `${isNum(e.benchmark_return) ? `<span>${escapeHtml(r.benchmark?.symbol || "SPY")}</span><span>${stripTags(fmtPct(e.benchmark_return, true, 2))}</span>` : ""}` +
        `${e.flags ? `<span>Flag</span><span>${escapeHtml(e.flags.join("; "))}</span>` : ""}</div><span class="hint">Click to inspect</span>`;
      svg += `<circle class="dot ${cls}${e.flags ? " flagged" : ""}" data-event="${e.index}" data-inspect="${e.index}" cx="${binX(bin)}" cy="${baseY - radius - 1 - k * radius * 2}" r="${radius}" data-tiphtml="${escapeHtml(tip)}"/>`;
    });
  }
  const left = stacks.get(-1), right = stacks.get(binCount);
  if (left) svg += `<text class="overflow" x="${margin.left - 16}" y="${top - 12}" text-anchor="middle">◂ ${left.length}</text>`;
  if (right) svg += `<text class="overflow" x="${width - margin.right + 16}" y="${top - 12}" text-anchor="middle">${right.length} ▸</text>`;

  const meanX = x(s.mean), baseX = x(b.mean);
  const close = Math.abs(meanX - baseX) < 90;
  svg += `<line class="marker-line base" x1="${baseX}" x2="${baseX}" y1="${top - 4}" y2="${baseY}"/>`;
  svg += `<line class="marker-line mean" x1="${meanX}" x2="${meanX}" y1="${top - 18}" y2="${baseY}"/>`;
  const meanAnchor = close && meanX < baseX ? "end" : close ? "start" : "middle";
  const baseAnchor = close && meanX < baseX ? "start" : close ? "end" : "middle";
  svg += `<text class="marker-label mean" x="${meanX + (meanAnchor === "start" ? 4 : meanAnchor === "end" ? -4 : 0)}" y="${top - 22}" text-anchor="${meanAnchor}">avg ${stripTags(fmtPct(s.mean, true, 1))}</text>`;
  svg += `<text class="marker-label base" x="${baseX + (baseAnchor === "start" ? 4 : baseAnchor === "end" ? -4 : 0)}" y="${top - 8}" text-anchor="${baseAnchor}">normal ${stripTags(fmtPct(b.mean, true, 1))}</text>`;
  svg += "</svg>";
  holder.innerHTML = svg;
  if (left || right) {
    holder.insertAdjacentHTML("beforeend", `<p class="note" style="margin-top:4px">Edge columns hold outliers beyond the axis (${left ? `${left.length} below ${stripTags(fmtPct(lo, true, 0))}` : ""}${left && right ? ", " : ""}${right ? `${right.length} above ${stripTags(fmtPct(hi, true, 0))}` : ""}). They're still in every statistic.</p>`);
  }
  holder.onmouseover = (event) => {
    const dot = event.target.closest(".dot");
    lab.el.querySelectorAll(".event.hl").forEach((row) => row.classList.remove("hl"));
    if (dot) lab.el.querySelector(`.event[data-event="${dot.dataset.event}"]`)?.classList.add("hl");
  };
}

function observeDistribution(lab) {
  if (lab.observer) lab.observer.disconnect();
  const holder = lab.el.querySelector("[data-dist]");
  if (!holder || typeof ResizeObserver === "undefined") return;
  let lastWidth = holder.clientWidth;
  lab.observer = new ResizeObserver(() => {
    if (Math.abs(holder.clientWidth - lastWidth) < 8) return;
    lastWidth = holder.clientWidth;
    clearTimeout(lab.resizeTimer);
    lab.resizeTimer = setTimeout(() => drawDistribution(lab), 120);
  });
  lab.observer.observe(holder);
}


// ---------- Event explorer ----------
function renderExplorer(lab) {
  const r = lab.result;
  const holder = lab.el.querySelector("[data-explorer]");
  if (!holder || !r) return;
  lab.el.querySelectorAll("[data-sort]").forEach((b) => b.classList.toggle("active", b.dataset.sort === (lab.sort || "newest")));
  lab.el.querySelector("[data-flagged-only]")?.classList.toggle("active", !!lab.flaggedOnly);
  const H = r.definition.outcome.horizon;
  const pm = r.definition.conditions.find((c) => c.type === "price_move");

  let events = r.events.map((e, index) => ({ ...e, index }));
  if (lab.flaggedOnly) events = events.filter((e) => e.flags);
  const sortBy = lab.sort || "newest";
  const byOutcome = (e) => (e.forward_return === null ? -Infinity : e.forward_return);
  const sorters = {
    newest: (a, b) => b.trigger_date.localeCompare(a.trigger_date),
    oldest: (a, b) => a.trigger_date.localeCompare(b.trigger_date),
    best: (a, b) => byOutcome(b) - byOutcome(a),
    worst: (a, b) => byOutcome(a) - byOutcome(b),
  };
  events.sort(sorters[sortBy]);
  const shown = lab.showAll ? events : events.slice(0, 40);
  const maxTrigger = Math.max(...events.map((e) => Math.abs(e.trigger_return || 0)), 0.0001);
  const maxForward = Math.max(...events.map((e) => Math.abs(e.forward_return || 0)), 0.0001);
  const bar = (value, max) => `<i class="${value > 0 ? "up" : "down"}" style="width:${Math.max(3, (Math.abs(value) / max) * 64)}px"></i>`;

  holder.innerHTML = `
    <div class="events-head label"><span>Trigger day</span><span>${pm ? `Move over ${pm.params.window}d` : "Close"}</span><span class="arrow"></span><span>Next ${H}d</span><span class="exit">Ended</span></div>
    <div class="events">
      ${shown.map((e) => `
        <div class="event ${e.status}" data-event="${e.index}" ${e.status === "complete" ? `data-inspect="${e.index}" tabindex="0" role="button"` : ""}>
          <span class="date">${fmtDay(e.trigger_date)}${e.flags ? ` <span class="flag" data-tip="${escapeHtml(`Data note inside this window: ${e.flags.join("; ")}`)}">${icon("warn", 12)}</span>` : ""}</span>
          ${isNum(e.trigger_return)
            ? `<span class="bar">${bar(e.trigger_return, maxTrigger)}<span class="${signClass(e.trigger_return)}">${fmtPct(e.trigger_return, true, 1)}</span></span>`
            : `<span>${fmtPrice(e.entry_price)}</span>`}
          <span class="arrow">→</span>
          ${e.status === "complete"
            ? `<span class="bar">${bar(e.forward_return, maxForward)}<span class="${signClass(e.forward_return)}">${fmtPct(e.forward_return, true, 1)}</span></span>
               <span class="exit">${fmtDay(e.exit_date)}</span>`
            : `<span class="na">in progress (${e.days_elapsed} of ${H} days)</span><span class="exit">excluded</span>`}
        </div>`).join("")}
    </div>
    ${!lab.showAll && events.length > shown.length ? `<div style="margin-top:14px"><button class="pill-btn" data-show-all>Show all ${events.length}</button></div>` : ""}`;
}


