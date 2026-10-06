// =========================================================
// invest.js — INVEST: "What do I own (or want to own), and what am I actually exposed to?"
//
// ANALYSES (workspaces)
//   Several separate analyses ("My AI basket", "Quantum thesis"…), each a small record of INPUTS only
//   (name, tags, notes, thesis text, holdings + weight drafts, horizon) autosaved to localStorage.
//   Results are never persisted: they're kept in memory per analysis and recomputed on demand.
//   Only the ACTIVE analysis renders anything heavy.
//
// RENDERING (why it stays fast)
//   The page is three independent regions — #inv-top (switcher), #inv-compose (inputs), #inv-out (results).
//   Typing never re-renders anything: inputs update state in place; parsing updates only the small
//   "detected" row; weights update only the total. Results render only the active tab, and each tab's
//   HTML is memoised until its data or its own UI state changes.
//
// ANALYSIS (progressive, cancellable)
//   Analyze → /quick (names, sectors, price correlation: seconds) → /analyze (filings: dependencies,
//   themes, macro, map). Each stage renders as it lands; each request carries a run id and an
//   AbortController, so an old response can never overwrite a newer analysis.
// Research, not recommendations.
// =========================================================

const INV = { meta: null, rendered: false, seq: 0, ctrl: {}, results: {}, lens: "map", focus: null, scenario: null, custom: null,
              advancedOpen: false, mapFilter: "all", highlight: null, openSrc: new Set(), depCat: "all", depHolding: null,
              showGeneral: false, goto: null, corrWindow: "1y", pair: null, summaryOpen: false, menuOpen: false, showArchived: false,
              confirmDelete: null, weightsOpen: false, notesOpen: false, chipOpen: null, compare: false, compareSel: new Set(),
              parseSeq: 0, traceSeq: 0, whatIf: {}, parseCache: new Map(), searchCache: new Map(), detect: null, suggest: [], suggestIdx: 0, stats: { renders: {}, req: 0, quickMs: null, fullMs: null } };
const INV_PLACEHOLDER = "Type tickers or company names — “Nvidia, AMD, TSM and QQQ” — or describe a thesis: “I want exposure to quantum computing over five years without depending on one company.”";
const KIND_CLASS = { theme: "k-theme", customer: "k-dep", supplier: "k-dep", partner: "k-dep", geography: "k-geo", policy: "k-policy",
                     industry: "k-sector", sector: "k-sector", financial: "k-fin", macro: "k-fin", measured: "k-measured" };
const KIND_NAME = { theme: "Theme", customer: "Customers", supplier: "Supplier", partner: "Partner", geography: "Geography", policy: "Policy",
                    industry: "Industry", sector: "Sector", financial: "Financial trait", macro: "Macro", measured: "Measured" };
const INV_TAGS = ["Current portfolio", "Watchlist", "Idea", "Long-term"];
const HORIZON_SHORT = { "3m": "3M", "6m": "6M", "12m": "12M", "24m": "24M", "5y": "5Y", custom: "Custom" };
const WS_KEY = "marketlab.invest.v1";

const pctv = (v, d = 0) => v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
const money = (v, cur = "USD") => v == null ? "—" : `${v < 0 ? "−" : ""}${cur && cur !== "USD" ? cur + " " : "$"}${(Math.abs(v) / 1e9 >= 1 ? (Math.abs(v) / 1e9).toFixed(1) + "B" : (Math.abs(v) / 1e6).toFixed(0) + "M")}`;

function invRoot() { return document.querySelector("#invest-env .env-body"); }
function $inv(sel) { return invRoot()?.querySelector(sel); }
function bump(region) { INV.stats.renders[region] = (INV.stats.renders[region] || 0) + 1; debugPanel(); }

// ---------------------------------------------------------------- workspace store (inputs only)
const WS = loadWorkspaces();

function newId() { return `an_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`; }
function blankAnalysis(name = "Untitled analysis") {
  const now = new Date().toISOString();
  return { id: newId(), name, tags: [], notes: "", text: "", holdings: [], horizon: "12m", months: 36, cash: "", dismissed: [],
           createdAt: now, updatedAt: now, analyzedAt: null, archived: false, summary: null };
}
function loadWorkspaces() {
  let data = null;
  try { data = JSON.parse(localStorage.getItem(WS_KEY) || "null"); } catch (e) { data = null; }
  if (!data || !data.items || !Object.keys(data.items).length) {
    const first = blankAnalysis("My first analysis");
    data = { active: first.id, items: { [first.id]: first }, migrated: false };
  }
  if (!data.items[data.active]) data.active = Object.keys(data.items)[0];
  return data;
}
let wsSaveTimer = null;
function saveWorkspaces({ touch = true } = {}) {
  if (touch) cur().updatedAt = new Date().toISOString();
  clearTimeout(wsSaveTimer);
  wsSaveTimer = setTimeout(() => { try { localStorage.setItem(WS_KEY, JSON.stringify(WS)); } catch (e) { /* storage full / private mode */ } }, 250);
}
function cur() { return WS.items[WS.active]; }
function R() { return INV.results[WS.active] || null; }
function analysesSorted() {
  return Object.values(WS.items).filter((a) => INV.showArchived || !a.archived).sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
}

// Earlier versions saved theses on the server: bring them in once as separate analyses.
async function migrateServerTheses() {
  if (WS.migrated) return;
  WS.migrated = true;
  try {
    const list = (await fetchJson("/api/invest/theses")).theses || [];
    for (const t of list) {
      if (Object.values(WS.items).some((a) => a.legacyId === t.id)) continue;
      const a = blankAnalysis(t.name || "Saved thesis");
      Object.assign(a, { legacyId: t.id, text: t.text || "", horizon: t.horizon || "12m", months: t.months || 36,
                         holdings: (t.holdings || []).map((h) => ({ ticker: h.ticker, name: "", w: h.weight == null ? "" : String(h.weight) })),
                         cash: t.cash ? String(t.cash) : "", tags: ["Idea"], updatedAt: t.updated_at || a.updatedAt });
      WS.items[a.id] = a;
    }
  } catch (e) { /* offline: nothing to migrate */ }
  saveWorkspaces({ touch: false });
  renderTop();
}

// ---------------------------------------------------------------- inputs → request
function weightOf(h) { const v = parseFloat(String(h.w ?? "").replace(",", ".")); return Number.isFinite(v) ? v : null; }
function cashOf(a) { const v = parseFloat(String(a.cash ?? "").replace(",", ".")); return Number.isFinite(v) ? v : 0; }
function inputsKey(a) {
  return JSON.stringify([a.holdings.map((h) => [h.ticker, weightOf(h)]), cashOf(a), a.horizon, a.horizon === "custom" ? a.months : 0, (a.text || "").trim()]);
}
function requestBody(a) {
  return { text: a.text, horizon: a.horizon, months: a.months, cash: cashOf(a),
           holdings: a.holdings.map((h) => ({ ticker: h.ticker, weight: weightOf(h) })) };
}
function weightTotal(a) {
  const n = a.holdings.length;
  if (!n) return null;
  const set = a.holdings.map(weightOf);
  if (set.every((v) => v == null)) return null;            // all blank = equal weights, nothing to total
  return set.reduce((s, v) => s + (v || 0), 0) + cashOf(a);
}

// ---------------------------------------------------------------- page shell
async function invMeta() {
  if (!INV.meta) INV.meta = await fetchJson("/api/invest/meta").catch(() => ({ horizons: {}, themes: {}, scenarios: [] }));
  return INV.meta;
}

function renderInvest() {
  const root = invRoot();
  if (!root) return;
  if (!INV.rendered) {
    root.innerHTML = `<div id="inv-top"></div><div id="inv-compose"></div><div id="inv-out"></div><div id="inv-debug" hidden></div>`;
    root.addEventListener("click", investClick);
    root.addEventListener("input", investInput);
    root.addEventListener("change", investChange);
    root.addEventListener("keydown", investKeys);
    root.addEventListener("focusout", investBlur);
    root.addEventListener("toggle", (e) => {
      if (e.target.matches?.("[data-inv-adv]")) { INV.advancedOpen = e.target.open; if (e.target.open) renderAdvanced(); }
      if (e.target.matches?.("[data-inv-sum]")) INV.summaryOpen = e.target.open;
    }, true);
    INV.rendered = true;
    invMeta();
    migrateServerTheses();
    renderAll();
    return;
  }
}

function renderAll() { renderTop(); renderCompose(); renderOut(); }

// ---------------------------------------------------------------- top: analysis switcher
function fmtAgo(iso) {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} h ago` : new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function topMeta(a) { return `${a.holdings.length} holding${a.holdings.length === 1 ? "" : "s"} · analysed ${escapeHtml(fmtAgo(a.analyzedAt))}`; }
function updateTopMeta() { const el = $inv("#inv-ws-meta"); if (el) el.innerHTML = topMeta(cur()); }

function renderTop() {
  const el = $inv("#inv-top");
  if (!el) return;
  bump("top");
  const a = cur();
  const list = analysesSorted();
  const recent = list.filter((x) => x.id !== a.id).slice(0, 4);
  el.innerHTML = `
    <div class="lab-head inv-head">
      <div class="lab-kicker">Invest</div>
      <h1>What am I really exposed to?</h1>
    </div>
    <div class="inv-ws glass">
      <div class="inv-ws-current">
        <button class="inv-ws-switch" data-ws-menu aria-haspopup="listbox" aria-expanded="${INV.menuOpen}">
          <span class="inv-ws-name">${escapeHtml(a.name)}</span>${icon("chevron-down", 12)}</button>
        <span class="rp-fine" id="inv-ws-meta">${topMeta(a)}</span>
      </div>
      <div class="inv-ws-actions">
        <button class="pill-btn" data-ws-new>${icon("plus", 13)}New analysis</button>
        <button class="small-btn" data-ws-dup title="Duplicate this analysis">Duplicate</button>
        <button class="small-btn" data-ws-compare title="Compare analyses side by side">Compare</button>
        <button class="small-btn" data-ws-archive title="${a.archived ? "Restore" : "Archive (hide from the list)"}">${a.archived ? "Restore" : "Archive"}</button>
        <button class="small-btn ${INV.confirmDelete === a.id ? "danger" : ""}" data-ws-delete>${INV.confirmDelete === a.id ? "Click again to delete" : "Delete"}</button>
      </div>
      ${INV.menuOpen ? `<div class="inv-ws-menu" role="listbox">
        ${list.map((x) => `<button role="option" aria-selected="${x.id === a.id}" class="inv-ws-item ${x.id === a.id ? "on" : ""}" data-ws-open="${x.id}">
          <b>${escapeHtml(x.name)}</b><span>${escapeHtml(x.holdings.map((h) => h.ticker).slice(0, 6).join(" · ") || "no holdings yet")}${x.archived ? " · archived" : ""}</span>
          <small>${x.tags.map(escapeHtml).join(" · ")}${x.tags.length ? " · " : ""}updated ${escapeHtml(fmtAgo(x.updatedAt))}</small></button>`).join("")}
        <button class="link-btn" data-ws-archived>${INV.showArchived ? "Hide archived" : "Show archived"}</button></div>` : ""}
      ${recent.length ? `<div class="inv-ws-recent"><span class="label">Recent</span>${recent.map((x) => `<button class="inv-ws-chip" data-ws-open="${x.id}">${escapeHtml(x.name)}</button>`).join("")}</div>` : ""}
    </div>`;
}

// ---------------------------------------------------------------- compose: inputs (never re-rendered while typing)
function renderCompose() {
  const el = $inv("#inv-compose");
  if (!el) return;
  bump("compose");
  const a = cur();
  el.innerHTML = `
    <section class="inv-composer glass">
      <div class="inv-name-row">
        <input class="inv-name" data-ws-name value="${escapeHtml(a.name)}" maxlength="80" aria-label="Analysis name" spellcheck="false">
        <div class="inv-tags">${INV_TAGS.map((t) => `<button class="chip ${a.tags.includes(t) ? "on" : ""}" data-ws-tag="${escapeHtml(t)}" aria-pressed="${a.tags.includes(t)}">${escapeHtml(t)}</button>`).join("")}</div>
      </div>
      <textarea class="idea-input inv-text" data-inv-text rows="3" maxlength="6000" placeholder="${escapeHtml(INV_PLACEHOLDER)}">${escapeHtml(a.text)}</textarea>
      <div id="inv-detected" class="inv-detected" aria-live="polite">${detectedHtml()}</div>
      <div id="inv-chips">${chipsHtml()}</div>
      <div id="inv-weights">${INV.weightsOpen ? weightsHtml() : ""}</div>
      ${INV.notesOpen ? `<textarea class="idea-input inv-notes" data-ws-notes rows="3" maxlength="4000" placeholder="Notes for this analysis…">${escapeHtml(a.notes || "")}</textarea>` : ""}
      <div class="inv-foot">
        <span id="inv-hz" class="inv-hz">${horizonHtml()}</span>
        <button class="link-btn" data-inv-toggle-weights aria-expanded="${INV.weightsOpen}">${INV.weightsOpen ? "Hide weights" : "Weights"}</button>
        <button class="link-btn" data-inv-toggle-notes aria-expanded="${INV.notesOpen}">${INV.notesOpen ? "Hide notes" : a.notes ? "Notes ●" : "Notes"}</button>
        <span class="rp-spacer"></span>
        <span id="inv-dirty" class="rp-fine">${dirtyText()}</span>
        <button class="run-btn" data-inv-go ${a.holdings.length ? "" : "disabled"}>Analyze <span class="arrow">→</span></button>
      </div>
    </section>`;
}

function horizonHtml() {
  const a = cur();
  return `<span class="seg inv-horizon" role="group" aria-label="Horizon">${["3m", "6m", "12m", "24m", "5y", "custom"].map((k) => `<button class="${a.horizon === k ? "on" : ""}" data-inv-horizon="${k}" aria-pressed="${a.horizon === k}">${HORIZON_SHORT[k]}</button>`).join("")}</span>
    ${a.horizon === "custom" ? `<label class="cap-field"><span>Months</span><input type="text" inputmode="numeric" data-inv-months value="${escapeHtml(String(a.months))}" size="4"></label>` : ""}`;
}
function renderHorizon() { const el = $inv("#inv-hz"); if (el) el.innerHTML = horizonHtml(); }

function dirtyText() {
  const r = R();
  if (!r) return "";
  return r.key !== inputsKey(cur()) ? "Inputs changed — Analyze to update" : "";
}
function updateDirty() { const el = $inv("#inv-dirty"); if (el) el.textContent = dirtyText(); const go = $inv("[data-inv-go]"); if (go) go.disabled = !cur().holdings.length; }

function detectedHtml() {
  const d = INV.detect;
  if (!d) return "";
  const a = cur();
  const parts = [];
  if (d.suggestions?.length) parts.push(d.suggestions.filter((s) => !a.holdings.some((h) => h.ticker === s.ticker) && !a.dismissed.includes(s.ticker)).map((s) =>
    `<span class="inv-ask">${escapeHtml(s.question)} <button class="link-btn" data-inv-accept="${escapeHtml(s.ticker)}">Add</button><button class="link-btn" data-inv-dismiss="${escapeHtml(s.ticker)}">No</button></span>`).join(""));
  if (d.themes?.length) parts.push(`<span class="rp-fine">Themes: ${d.themes.map((t) => `<b>${escapeHtml(t.label)}</b>`).join(", ")}</span>`);
  if (d.candidates?.length) parts.push(`<span class="inv-cands"><span class="rp-fine">Candidates to research (not recommendations):</span>${d.candidates.slice(0, 8).map((t) =>
    a.holdings.some((h) => h.ticker === t) ? "" : `<button class="inv-cand" data-inv-add="${t}">${icon("plus", 11)}${t}</button>`).join("")}</span>`);
  if (d.ignored?.length) parts.push(`<span class="rp-fine">Not treated as tickers: ${d.ignored.slice(0, 8).map(escapeHtml).join(", ")}</span>`);
  return parts.filter(Boolean).join("");
}
function renderDetected() { const el = $inv("#inv-detected"); if (el) { el.innerHTML = detectedHtml(); bump("detected"); } }

function chipsHtml() {
  const a = cur();
  const q = R()?.quick;
  const info = (t) => q?.holdings.find((h) => h.ticker === t);
  const open = a.holdings.find((h) => h.ticker === INV.chipOpen);
  return `<div class="inv-bubbles" aria-label="Holdings">
      ${a.holdings.length ? "" : `<span class="rp-fine inv-hint">Detected holdings appear here — or add them directly:</span>`}
      ${a.holdings.map((h) => `<span class="tk-bubble ${h.detected ? "detected" : ""} ${INV.chipOpen === h.ticker ? "open" : ""}">
        <button class="tk-main" data-inv-chip="${escapeHtml(h.ticker)}" title="${escapeHtml(h.name || info(h.ticker)?.name || "")}"><b>${escapeHtml(h.ticker)}</b><small>${weightOf(h) != null ? `${weightOf(h)}%` : "auto"}</small></button>
        <button class="tk-x" data-inv-remove="${escapeHtml(h.ticker)}" aria-label="Remove ${escapeHtml(h.ticker)}">${icon("close", 10)}</button></span>`).join("")}
      <span class="tk-add"><input data-inv-new maxlength="40" placeholder="+ Add holding" aria-label="Add a holding by ticker or company name" spellcheck="false" autocomplete="off">
        <div id="inv-suggest" class="inv-suggest" role="listbox" hidden></div></span>
    </div>
    ${open ? `<div class="inv-chip-detail glass-flat"><b>${escapeHtml(open.ticker)}</b> <span>${escapeHtml(open.name || info(open.ticker)?.name || "")}</span>
      ${info(open.ticker) ? `<span class="rp-fine">${escapeHtml([info(open.ticker).sector, info(open.ticker).industry].filter(Boolean).join(" · "))}${info(open.ticker).ret_12m != null ? ` · 12m ${pctv(info(open.ticker).ret_12m)}` : ""}</span>` : ""}
      <label class="cap-field"><span>Weight</span><input type="text" inputmode="decimal" data-inv-w="${escapeHtml(open.ticker)}" value="${escapeHtml(String(open.w ?? ""))}" placeholder="auto" size="5"><small>%</small></label>
      <button class="link-btn" data-inv-research="${escapeHtml(open.ticker)}">${icon("research", 12)}Research</button>
      <button class="link-btn" data-inv-remove="${escapeHtml(open.ticker)}">Remove</button></div>` : ""}`;
}
// Re-rendering a small region must never steal focus, text or the caret from an input the user is in
function keepFocus(region, draw) {
  const act = document.activeElement;
  const inside = act && region.contains(act) && act.matches("input, textarea");
  const sel = inside ? [...act.attributes].find((x) => x.name.startsWith("data-"))?.name : null;
  const state = inside ? { value: act.value, start: act.selectionStart, end: act.selectionEnd, key: sel ? `[${sel}${act.getAttribute(sel) ? `="${CSS.escape(act.getAttribute(sel))}"` : ""}]` : null } : null;
  draw();
  if (!state?.key) return;
  const again = region.querySelector(state.key);
  if (!again) return;
  again.value = state.value;
  again.focus();
  try { again.setSelectionRange(state.start, state.end); } catch (e) { /* not a text input */ }
}
function renderChips() { const el = $inv("#inv-chips"); if (el) { keepFocus(el, () => { el.innerHTML = chipsHtml(); }); bump("chips"); } updateDirty();  updateTopMeta(); }

function weightsHtml() {
  const a = cur();
  const total = weightTotal(a);
  return `<div class="inv-weights glass-flat">
    <div class="inv-weights-grid">${a.holdings.map((h) => `<label class="cap-field"><span>${escapeHtml(h.ticker)}</span>
      <input type="text" inputmode="decimal" data-inv-w="${escapeHtml(h.ticker)}" value="${escapeHtml(String(h.w ?? ""))}" placeholder="auto" size="5"><small>%</small></label>`).join("")}
      <label class="cap-field"><span>Cash</span><input type="text" inputmode="decimal" data-inv-cash value="${escapeHtml(String(a.cash ?? ""))}" placeholder="0" size="5"><small>%</small></label></div>
    <div class="inv-weights-foot"><span id="inv-total" class="inv-total">${totalText(total)}</span>
      <button class="pill-btn" data-inv-equal>Equal weight</button><button class="pill-btn" data-inv-normalize>Normalize to 100%</button>
      <span class="rp-fine">Blank = shares what's left equally. Values are only rescaled when you press Normalize.</span></div></div>`;
}
function totalText(total) {
  if (total == null) return "Equal weights";
  const ok = Math.abs(total - 100) < 0.05;
  return `<b class="${ok ? "" : "warn"}">Total: ${+total.toFixed(2)}%</b>${ok ? "" : " — analysis scales to 100%"}`;
}
function renderWeights() { const el = $inv("#inv-weights"); if (el) { keepFocus(el, () => { el.innerHTML = INV.weightsOpen ? weightsHtml() : ""; }); bump("weights"); } }

// ---------------------------------------------------------------- out: results (summary → active tab → the rest)
function renderOut() {
  const el = $inv("#inv-out");
  if (!el) return;
  bump("out");
  if (INV.compare) { el.innerHTML = analysesCompareHtml(); return; }
  const r = R();
  const a = cur();
  if (!r) { el.innerHTML = a.holdings.length ? `<p class="inv-empty">Ready when you are — press <b>Analyze</b>. Nothing heavy runs until you do.</p>` : investEmptyHtml(); return; }
  el.innerHTML = `<div id="inv-progress">${progressHtml(r)}</div><div id="inv-summary-slot">${summaryHtml(r)}</div>
    <section class="inv-lenses" id="inv-lenses"><div id="inv-lens-bar"></div><div id="inv-panes"></div></section>
    <div id="inv-rest">${restHtml(r)}</div>`;
  drawLens();
}

setInterval(() => {
  if (!INV.rendered || document.hidden || !invRoot()?.offsetParent) return;
  updateTopMeta();
  const ago = $inv("#inv-ago"); if (ago) ago.textContent = `Updated ${fmtAgo(cur().analyzedAt)}`;
}, 20000);

function progressHtml(r) {
  const step = (label, st) => `<span class="inv-step s-${st}">${st === "ok" ? "✓" : st === "error" ? "!" : `<span class="idea-spinner"></span>`} ${label}</span>`;
  if (r.st.quick === "ok" && r.st.full === "ok") return `<div class="inv-progress done"><span class="rp-fine" id="inv-ago">Updated ${escapeHtml(fmtAgo(cur().analyzedAt))}</span>
    <button class="link-btn" data-inv-refresh title="Fetch prices and filings again">Refresh data</button></div>`;
  return `<div class="inv-progress">${step("Holdings & prices", r.st.quick)}${step("Price correlation", r.st.quick)}${step("Filings: dependencies, themes, macro", r.st.full)}
    ${r.err.quick ? `<span class="rp-warn">${escapeHtml(r.err.quick)}</span>` : ""}${r.err.full ? `<span class="rp-warn">Filings analysis: ${escapeHtml(r.err.full)}</span>` : ""}</div>`;
}

function summaryHtml(r) {
  const a = r.full, q = r.quick;
  const base = a || q;
  if (!base) return r.st.quick === "error" ? `<p class="inv-empty">${escapeHtml(r.err.quick || "Could not load these holdings.")}</p>` : skeleton("Loading holdings and prices…", 3);
  const n = base.holdings.length;
  const o = a?.overview || {};
  const headline = a && o.largest && o.largest_count >= 2 && o.largest_count >= n / 2
    ? `Your holdings look diversified by ticker, but ${o.largest_count} of ${n} share exposure to ${escapeHtml(o.largest.split(" (")[0])}.` : "";
  const avg = (a?.corr || q?.corr)?.average;
  const facts = [
    a ? `<span class="inv-fact"><b>${o.shared_themes}</b> shared theme${o.shared_themes === 1 ? "" : "s"}</span>` : `<span class="inv-fact muted">themes loading…</span>`,
    a ? `<span class="inv-fact"><b>${o.shared_dependencies}</b> shared dependenc${o.shared_dependencies === 1 ? "y" : "ies"}</span>` : `<span class="inv-fact muted">dependencies loading…</span>`,
    avg != null ? `<span class="inv-fact"><b>${avg.toFixed(2)}</b> average correlation (1Y)</span>` : "",
    a && o.largest ? `<span class="inv-fact">Largest hidden concentration: <b>${escapeHtml(o.largest.split(" (")[0])}</b>${o.largest_count ? ` (${o.largest_count}/${n})` : ""}</span>` : "",
    q && !a ? `<span class="inv-fact">Sectors: ${Object.entries(q.sectors).slice(0, 3).map(([s, w]) => `<b>${escapeHtml(s)}</b> ${w.toFixed(0)}%`).join(" · ")}</span>` : "",
  ].filter(Boolean).join("");
  const problems = [...(q?.problems || []), ...(a?.problems || [])].filter((p, i, arr) => arr.indexOf(p) === i);
  return `<section class="inv-summary glass" aria-label="Summary">
      <div class="inv-sum-left"><b class="inv-sum-n">${n}</b><span>${n === 1 ? "investment" : "investments"}<small>${escapeHtml(a ? a.horizon.label : HORIZON_SHORT[cur().horizon])}${base.cash ? ` · ${base.cash.toFixed(0)}% cash` : ""}</small></span></div>
      <div class="inv-sum-right">
        ${headline ? `<p class="inv-headline">${headline}</p>` : ""}
        <div class="inv-sum-holdings">${base.holdings.map((h) => `<span class="mini-tk" title="${escapeHtml(h.name || "")}">${escapeHtml(h.ticker)} ${h.weight.toFixed(0)}%</span>`).join("")}</div>
        <div class="inv-facts">${facts}</div>
        ${a && a.summary.length ? `<details class="inv-sum-more" ${INV.summaryOpen ? "open" : ""} data-inv-sum><summary>${a.summary.length} findings</summary><ul>${a.summary.map((s) => `<li>${s.count != null ? `<span class="inv-ratio">${s.count}<i>/</i>${s.of}</span>` : `<span class="inv-ratio lt">${icon("invest", 14)}</span>`}<span>${escapeHtml(s.text)}</span></li>`).join("")}</ul></details>` : ""}
        ${problems.length ? `<p class="rp-warn">${icon("warn", 13)} ${escapeHtml(problems.join(" "))}</p>` : ""}
      </div></section>`;
}

function skeleton(label, lines = 3) {
  return `<div class="inv-skel" aria-busy="true"><span class="rp-fine">${escapeHtml(label)}</span>${"<i></i>".repeat(lines)}</div>`;
}

// Tabs render lazily, one at a time. Each tab keeps its own DOM pane once drawn: going back to a tab only
// un-hides it (no rebuild), and a pane is redrawn only when ITS data or ITS own UI state changed.
const LENS_TABS = [["map", "Exposure map"], ["dependency", "Dependencies"], ["thematic", "Themes"], ["macro", "Macro"], ["market", "Correlation"]];
function lensState(lens, r) {
  const src = [...INV.openSrc].sort();
  return JSON.stringify(lens === "map" ? [INV.focus, INV.mapFilter, INV.highlight, src]
    : lens === "dependency" ? [INV.depCat, INV.depHolding, INV.goto, INV.showGeneral, src]
    : lens === "market" ? [INV.corrWindow, INV.pair, Object.keys(r.corr).sort()]
    : [INV.goto, src]);
}

function lensBarHtml() {
  const lens = INV.lens;
  return `<div class="seg inv-lens-tabs" role="tablist" aria-label="Lens">${LENS_TABS.map(([k, label]) => `<button role="tab" class="${lens === k ? "on" : ""}" aria-selected="${lens === k}" data-inv-lens="${k}">${label}</button>`).join("")}</div>
    ${INV.highlight ? `<div class="inv-hl"><span>Showing <b>${escapeHtml(INV.highlight.label)}</b>: ${INV.highlight.tickers.map((t) => `<span class="mini-tk">${escapeHtml(t)}</span>`).join("")}</span><button class="link-btn" data-inv-hl-clear>Clear</button></div>` : ""}`;
}

function lensBodyHtml(r, base, lens) {
  if (!base) return skeleton("Loading…");
  if (base.holdings.length < 2) return `<p class="inv-empty">Add at least two investments to analyze shared exposures.</p>`;
  if (lens === "market") return corrTabHtml(r.full || { holdings: base.holdings, corr: base.corr });
  if (!r.full) return r.st.full === "error" ? `<p class="inv-empty">This tab needs the filings analysis, which failed: ${escapeHtml(r.err.full || "")} The Correlation tab still works.</p>`
    : skeleton({ map: "Building the exposure map from filings…", dependency: "Loading verified relationships from filings…", thematic: "Reading themes from filings…", macro: "Loading macro exposure…" }[lens], 4);
  try {
    return lens === "map" ? mapTabHtml(r.full) : lens === "dependency" ? depsTabHtml(r.full) : lens === "thematic" ? themesTabHtml(r.full) : macroTabHtml(r.full);
  } catch (error) {
    console.error(error);
    return `<p class="inv-empty">This view couldn't be drawn (${escapeHtml(error.message)}). The other tabs still work.</p>`;
  }
}

function drawLens() {
  const bar = $inv("#inv-lens-bar"), panes = $inv("#inv-panes");
  const r = R();
  if (!bar || !panes || !r) return;
  const t = performance.now();
  bar.innerHTML = lensBarHtml();
  const lens = INV.lens;
  const key = `${r.stamp}|${r.full ? "f" : "q"}|${lensState(lens, r)}`;
  let pane = panes.querySelector(`[data-pane="${lens}"]`);
  if (!pane) { pane = document.createElement("div"); pane.className = "inv-lens-body"; pane.dataset.pane = lens; panes.appendChild(pane); }
  if (pane.dataset.key !== key) { pane.innerHTML = lensBodyHtml(r, r.full || r.quick, lens); pane.dataset.key = key; INV.stats.paneBuilds = (INV.stats.paneBuilds || 0) + 1; }
  for (const p of panes.children) p.hidden = p !== pane;
  bump("lens");
  bindMapHover();
  INV.stats.lensMs = Math.round(performance.now() - t);
  if (INV.goto) requestAnimationFrame(() => pane.querySelector(".goto")?.scrollIntoView({ behavior: "smooth", block: "center" }));
}

function restHtml(r) {
  const a = r.full;
  if (!a) return r.st.full === "loading" ? skeleton("Thesis check, blind spots and scenarios load with the filings…", 2) : "";
  return `${thesisHtml(a)}
    ${a.blind_spots.length ? `<section class="inv-section"><div class="section-head"><h3>Blind spots</h3><span class="aside">What the basket doesn't cover, or covers twice — descriptive, not advice</span></div>
      <div class="inv-blind">${a.blind_spots.map((b) => `<div class="inv-blind-item glass-flat"><span class="inv-blind-ico k-${b.kind}"></span><p>${escapeHtml(b.text)}</p></div>`).join("")}</div></section>` : ""}
    <section class="inv-section" id="inv-scen-sec">${scenSectionHtml(a)}</section>
    <section class="inv-section"><div class="section-head"><h3>Investments</h3><span class="aside">${escapeHtml(a.emphasis.label)}</span></div>
      <div class="inv-holdings">${a.holdings.map((h) => holdingCardHtml(h, a)).join("")}</div></section>
    <details class="rp-advanced inv-advanced" data-inv-adv ${INV.advancedOpen ? "open" : ""}><summary>Advanced <span>correlation matrix · sensitivities · every factor with evidence · fund holdings · methodology</span></summary>
      <div id="inv-adv-body">${INV.advancedOpen ? investAdvancedHtml(a) : ""}</div></details>
    <p class="rp-fine inv-note">${escapeHtml(a.note)}</p>`;
}
function renderRest() { const el = $inv("#inv-rest"); const r = R(); if (el && r) { el.innerHTML = restHtml(r); bump("rest"); } }
// Opening "Advanced" fills only its own body — the scenario box above (and anything typed in it) is untouched
function renderAdvanced() { const el = $inv("#inv-adv-body"); const a = R()?.full; if (el && a) { el.innerHTML = INV.advancedOpen ? investAdvancedHtml(a) : ""; bump("advanced"); } }

// ---------------------------------------------------------------- scenarios: the "What if…" box keeps a local draft per analysis
function whatIfDraft() { return INV.whatIf[WS.active] || ""; }
function scenSectionHtml(a) {
  const draft = whatIfDraft();
  const recent = cur().recentScen || [];
  return `<div class="section-head"><h3>Scenarios</h3><span class="aside">Which investments an event reaches, and through which exposure — no return forecasts</span></div>
      <div class="inv-scen-list">${a.scenarios.map((s, i) => `<button class="inv-scen ${INV.scenario === i ? "on" : ""}" data-inv-scen="${i}">
        <span>${escapeHtml(s.label)}</span><b>${s.material_weight ? `${s.material_weight.toFixed(0)}%` : s.any_weight ? "minor" : "—"}</b></button>`).join("")}</div>
      <div class="inv-custom">
        <span class="inv-whatif-field"><input data-inv-what-if maxlength="300" autocomplete="off" spellcheck="false" value="${escapeHtml(draft)}" aria-label="Custom scenario"
          placeholder="What if… AI capex slows · rates stay high · China–Taiwan tensions rise · export rules tighten">
          <button class="inv-whatif-x" data-inv-what-if-clear aria-label="Clear scenario" ${draft ? "" : "hidden"}>${icon("close", 11)}</button></span>
        <button class="pill-btn" data-inv-what-if-go>Trace</button><span id="inv-trace-status" class="rp-fine" aria-live="polite"></span></div>
      <div id="inv-scen-recent" class="inv-scen-recent">${recentScenHtml(recent)}</div>
      <div id="inv-scen-out">${INV.custom ? scenarioHtml(INV.custom) : INV.scenario != null ? scenarioHtml(a.scenarios[INV.scenario]) : ""}</div>`;
}
function recentScenHtml(recent) {
  if (!recent.length) return "";
  return `<span class="label">Recent</span>${recent.map((t, i) => `<span class="inv-recent-scen"><button class="link-btn" data-inv-recent="${i}" title="Trace again">${escapeHtml(t)}</button><button class="inv-recent-edit" data-inv-recent-edit="${i}" title="Copy into the box to modify" aria-label="Edit a copy">${icon("edit", 11)}</button></span>`).join("")}`;
}

function investEmptyHtml() {
  const examples = [["NVDA,AMD,AVGO,TSM,QQQ", "AI hardware + Nasdaq"], ["IONQ,RGTI,QBTS,IBM", "Quantum computing"], ["AAPL,MSFT,GOOGL,AMZN,META", "Big tech"], ["LLY,NVO,UNH,JNJ", "Healthcare"]];
  return `<section class="inv-examples"><span class="label">Or start a new analysis from an example</span><div>${examples.map(([t, label]) =>
    `<button class="inv-example glass-flat" data-inv-example="${t}" data-label="${escapeHtml(label)}"><b>${escapeHtml(label)}</b><span>${t.split(",").join(" · ")}</span></button>`).join("")}</div></section>`;
}

// ---------------------------------------------------------------- compare (summary snapshots only)
function analysesCompareHtml() {
  const list = Object.values(WS.items).filter((x) => !x.archived);
  const chosen = list.filter((x) => INV.compareSel.has(x.id));
  return `<section class="inv-compare">
    <div class="section-head"><h3>Compare analyses</h3><span class="aside">Summary snapshots from each analysis's last run — analyse an idea first to include it</span>
      <button class="link-btn" data-ws-compare-close>Close</button></div>
    <div class="chips">${list.map((x) => `<button class="chip ${INV.compareSel.has(x.id) ? "on" : ""}" data-ws-compare-pick="${x.id}" ${x.summary ? "" : "disabled title=\"Not analysed yet\""}>${escapeHtml(x.name)}</button>`).join("")}</div>
    ${chosen.length ? `<div class="table-scroll"><table class="rp-trades inv-compare-table"><thead><tr><th></th>${chosen.map((x) => `<th>${escapeHtml(x.name)}</th>`).join("")}</tr></thead><tbody>
      <tr><th>Holdings</th>${chosen.map((x) => `<td>${escapeHtml((x.summary.holdings || []).join(" · "))}</td>`).join("")}</tr>
      <tr><th>Average correlation (1Y)</th>${chosen.map((x) => `<td>${x.summary.avg == null ? "—" : x.summary.avg.toFixed(2)}</td>`).join("")}</tr>
      <tr><th>Top hidden exposures</th>${chosen.map((x) => `<td>${(x.summary.exposures || []).map(escapeHtml).join("<br>") || "—"}</td>`).join("")}</tr>
      <tr><th>Major shared dependencies</th>${chosen.map((x) => `<td>${(x.summary.deps || []).map(escapeHtml).join("<br>") || "—"}</td>`).join("")}</tr>
      <tr><th>Shared themes</th>${chosen.map((x) => `<td>${(x.summary.themes || []).map(escapeHtml).join("<br>") || "—"}</td>`).join("")}</tr>
      <tr><th>Analysed</th>${chosen.map((x) => `<td>${escapeHtml(fmtAgo(x.analyzedAt))}</td>`).join("")}</tr>
    </tbody></table></div>` : `<p class="inv-empty">Pick two or more analysed ideas above.</p>`}</section>`;
}

// ---------------------------------------------------------------- shared helpers for the tabs
function evidenceHtml(list) {
  const ev = (list || []).filter((e) => e && e.text);
  if (!ev.length) return `<p class="rp-fine">No quoted evidence stored for this link.</p>`;
  return `<ul class="inv-evidence">${ev.map((e) => `<li><q>${escapeHtml(e.text)}</q>
    <span class="inv-prov">${escapeHtml(e.type || "Source")}${e.source ? ` · ${escapeHtml(e.source)}` : ""}${e.date && !(e.source || "").includes(e.date) ? ` · ${escapeHtml(e.date)}` : ""}${e.url ? ` · <a href="${escapeHtml(e.url)}" target="_blank" rel="noopener">open ↗</a>` : ""}</span></li>`).join("")}</ul>`;
}
const STRENGTH_LABEL = { disclosed: "disclosed number", emphasized: "emphasised", measured: "measured", classification: "classification", "look-through": "via fund holdings", mentioned: "mentioned" };
function splitLabel(label) {
  const m = /^(.*)\s+\(([^()]*)\)$/.exec(label || "");
  return m ? [m[1], m[2]] : [label, ""];
}
function strengthChip(t, strength) {
  return `<span class="tk-str s-${escapeHtml(strength)}" title="${escapeHtml(STRENGTH_LABEL[strength] || strength)}"><b>${escapeHtml(t)}</b><small>${escapeHtml(STRENGTH_LABEL[strength] || strength)}</small></span>`;
}
function showInMapBtn(label, tickers) {
  return `<button class="link-btn" data-inv-show-map="${escapeHtml(tickers.join(","))}" data-label="${escapeHtml(label)}">${icon("invest", 12)}Show in map</button>`;
}
function srcToggle(id, html) {
  const open = INV.openSrc.has(id);
  return `<button class="link-btn inv-src-btn" data-inv-src="${escapeHtml(id)}" aria-expanded="${open}">${open ? "Hide sources" : "Sources"}</button>${open ? `<div class="inv-src">${html}</div>` : ""}`;
}

// ---------------------------------------------------------------- MAP tab
function mapTabHtml(a) {
  const kinds = [["all", "All"], ["dep", "Dependencies"], ["theme", "Themes & sectors"], ["macro", "Macro & measured"]];
  return `<div class="inv-tab-head"><p class="rp-fine">Each line connects an investment to an exposure it shares with another. Solid = material (disclosed, emphasised, measured, classification or a fund's 5%+ position); dashed = only mentioned.</p>
      <div class="chips inv-filter">${kinds.map(([k, l]) => `<button class="chip ${INV.mapFilter === k ? "on" : ""}" data-inv-map-filter="${k}">${l}</button>`).join("")}</div></div>
    ${exposureMapHtml(a)}
    ${INV.focus ? factorDetailHtml(a, INV.focus) : ""}`;
}

function factorDetailHtml(a, key) {
  const f = a.factors.find((x) => x.key === key);
  const m = (a.lenses.macro || []).find((x) => x.key === key && x.kind === "measured");
  const close = `<button class="small-btn" data-inv-focus="${escapeHtml(key)}" aria-label="Close">${icon("close", 11)}</button>`;
  if (m) {
    return `<div class="inv-factor glass-flat"><header><b>${escapeHtml(m.label)}</b><span class="rp-fine">measured</span>${close}</header>
      <p class="rp-fine">Correlation of daily returns with ${escapeHtml(m.proxy)} over ${a.correlation.window_days} trading days (Yahoo Finance). Price sensitivity, not a business dependency.</p>
      <div class="inv-ev-row">${m.holders.map((h) => `<span class="rp-chip">${escapeHtml(h.ticker)} · ${h.value.toFixed(2)}</span>`).join("")}</div>
      <div class="inv-xlinks"><button class="pill-btn" data-inv-lens="macro">Open in Macro</button></div></div>`;
  }
  if (!f) return "";
  const target = ["supplier", "customer", "partner", "geography", "policy"].includes(f.kind) ? ["dependency", "Dependencies"]
    : ["theme", "industry", "sector"].includes(f.kind) ? ["thematic", "Themes"] : ["macro", "Macro"];
  return `<div class="inv-factor glass-flat"><header><b>${escapeHtml(f.label)}</b><span class="rp-fine">${f.material_weight.toFixed(0)}% of the basket materially</span>${close}</header>
    <div class="inv-ev-row">${f.holders.map((h) => strengthChip(h.ticker, h.strength)).join("")}</div>
    ${srcToggle(`f:${key}`, f.holders.map((h) => `<div class="inv-ev"><b>${escapeHtml(h.ticker)}</b>${evidenceHtml(h.evidence)}</div>`).join(""))}
    <div class="inv-xlinks"><button class="pill-btn" data-inv-lens="${target[0]}" data-inv-goto="${escapeHtml(key)}">Open in ${target[1]}</button></div></div>`;
}

// ---------------------------------------------------------------- DEPENDENCIES tab
function depsTabHtml(a) {
  const D = a.dependencies;
  if (!D || !D.available) {
    const why = Object.entries(D?.states || {}).map(([t, st]) => `${t}: ${st.message || st.state || "no data"}`).join(" · ");
    return `<p class="inv-empty">No verified dependency data found for these holdings from connected sources.${why ? `<br><small>${escapeHtml(why)}</small>` : ""}</p>`;
  }
  const cats = [...new Set(D.shared.map((d) => d.category))];
  const cat = INV.depCat && cats.includes(INV.depCat) ? INV.depCat : "all";
  const list = D.shared.filter((d) => (cat === "all" || d.category === cat) && (INV.showGeneral || !d.general));
  const general = D.shared.filter((d) => d.general).length;
  const syms = a.holdings.map((h) => h.ticker);
  const who = INV.depHolding && syms.includes(INV.depHolding) ? INV.depHolding : syms[0];
  const own = (D.per_holding[who] || []).filter((d) => INV.showGeneral || !d.general);
  const byCat = {};
  for (const d of own) (byCat[d.category] = byCat[d.category] || []).push(d);
  const st = D.states[who] || {};
  return `<div class="inv-tab-head"><p class="rp-fine">What these holdings rely on outside themselves — suppliers, customers, partners, geographies, government and regulation — quoted from each company's latest annual report. Shared dependencies first.</p>
      <div class="chips inv-filter"><button class="chip ${cat === "all" ? "on" : ""}" data-inv-dep-cat="all">All</button>${cats.map((c) => `<button class="chip ${cat === c ? "on" : ""}" data-inv-dep-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join("")}</div></div>
    <h4 class="inv-sub">Shared dependencies <small>${list.length}</small></h4>
    ${list.length ? `<div class="inv-deps">${list.map((d) => `<article class="inv-dep glass-flat ${INV.goto === d.key ? "goto" : ""}" id="dep-${escapeHtml(d.key.replace(/[^a-z0-9]/gi, "-"))}">
        <header><b>${escapeHtml(d.label)}</b><span class="inv-cat">${escapeHtml(d.category)}</span>${d.general ? `<span class="inv-cat muted">listed by most companies</span>` : ""}</header>
        <p class="inv-dep-why">${escapeHtml(d.why)}</p>
        <div class="inv-ev-row">${d.holders.map((h) => strengthChip(h.ticker, h.strength)).join("")}</div>
        <div class="inv-dep-foot">${srcToggle(`d:${d.key}`, d.holders.map((h) => `<div class="inv-ev"><b>${escapeHtml(h.ticker)}</b>${evidenceHtml(h.evidence)}</div>`).join(""))}
          ${showInMapBtn(d.label, d.holders.map((h) => h.ticker))}</div></article>`).join("")}</div>`
      : `<p class="inv-empty">No ${cat === "all" ? "" : escapeHtml(cat.toLowerCase()) + " "}dependency is shared by two or more of these holdings in their filings.</p>`}
    ${general ? `<button class="link-btn" data-inv-general>${INV.showGeneral ? "Hide" : "Show"} ${general} topic${general === 1 ? "" : "s"} most companies list (tax, regulation, currencies…)</button>` : ""}
    <h4 class="inv-sub">By holding</h4>
    <div class="seg inv-who">${syms.map((t) => `<button class="${t === who ? "on" : ""}" data-inv-dep-holding="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join("")}</div>
    ${own.length ? `<div class="inv-own">${Object.entries(byCat).map(([c, items]) => `<div class="inv-own-cat"><span class="label">${escapeHtml(c)}</span>
        ${items.map((d) => { const [main, rest] = splitLabel(d.label); return `<div class="inv-own-row"><span class="inv-arrow">→</span>
          <span class="inv-own-txt"><b>${escapeHtml(main)}</b>${rest ? ` <small>${escapeHtml(rest)}</small>` : ""}<em>${escapeHtml(STRENGTH_LABEL[d.strength] || d.strength)}</em></span>
          ${srcToggle(`o:${who}:${d.key}`, evidenceHtml(d.evidence))}</div>`; }).join("")}</div>`).join("")}</div>`
      : `<p class="inv-empty">${escapeHtml(st.message || `No verified dependencies found for ${who} in connected sources.`)}</p>`}
    <p class="rp-fine inv-method">${escapeHtml(D.note)}</p>`;
}

// ---------------------------------------------------------------- THEMES tab
function themesTabHtml(a) {
  const T = a.themes;
  if (!T || !T.themes.length) return `<p class="inv-empty">No theme, industry or sector information was found for these holdings.</p>`;
  const shared = T.themes.filter((t) => t.shared), single = T.themes.filter((t) => !t.shared);
  const lv = { high: "High", moderate: "Moderate", indirect: "Indirect" };
  const card = (t) => `<article class="inv-theme glass-flat ${t.kind === "theme" ? "k-theme" : "k-sector"} ${INV.goto === t.key ? "goto" : ""}">
      <header><span class="inv-theme-orb"></span><b>${escapeHtml(t.label)}</b><span class="inv-cat">${t.kind === "theme" ? "Theme" : t.kind === "industry" ? "Industry" : "Sector"}</span></header>
      ${t.concentration ? `<p class="inv-dep-why">${escapeHtml(t.concentration)}</p>` : ""}
      <div class="inv-theme-members">${t.members.map((m) => `<span class="tk-lv lv-${m.level}" title="${escapeHtml(T.rules[m.level] || "")}"><b>${escapeHtml(m.ticker)}</b><small>${lv[m.level]}${m.mentions ? ` · ${m.mentions}×` : ""}</small></span>`).join("")}</div>
      <div class="inv-dep-foot">${srcToggle(`t:${t.key}`, t.members.map((m) => `<div class="inv-ev"><b>${escapeHtml(m.ticker)}</b> <small>${lv[m.level]}</small>${evidenceHtml(m.evidence)}</div>`).join(""))}
        ${t.members.length >= 2 ? showInMapBtn(t.label, t.members.map((m) => m.ticker)) : ""}</div></article>`;
  return `<div class="inv-tab-head"><p class="rp-fine">The investment stories these holdings share — several different tickers can express the same thesis.</p>
      <div class="inv-rules">${Object.entries(T.rules).map(([k, r]) => `<span class="tk-lv lv-${k}"><small>${escapeHtml(r)}</small></span>`).join("")}</div></div>
    ${T.thesis_themes.length ? `<p class="rp-fine">Your thesis mentions: ${T.thesis_themes.map((t) => `<b>${escapeHtml(t.label)}</b>`).join(", ")}.</p>` : ""}
    <h4 class="inv-sub">Shared by two or more holdings <small>${shared.length}</small></h4>
    ${shared.length ? `<div class="inv-themes">${shared.map(card).join("")}</div>` : `<p class="inv-empty">No theme is expressed by two or more holdings — by this measure the themes are distinct.</p>`}
    ${single.length ? `<details class="inv-more-themes"><summary>Themes, industries and sectors held by only one investment (${single.length})</summary><div class="inv-themes">${single.map(card).join("")}</div></details>` : ""}
    <p class="rp-fine inv-method">${escapeHtml(T.note)}</p>`;
}

// ---------------------------------------------------------------- MACRO tab
function corrBar(v) {
  if (v == null) return `<span class="cbar na">—</span>`;
  const w = Math.min(50, Math.abs(v) * 50);
  return `<span class="cbar"><i class="${v >= 0 ? "pos" : "neg"}" style="${v >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`}"></i></span><b class="cval">${v.toFixed(2)}</b>`;
}

function macroTabHtml(a) {
  const M = a.macro;
  if (!M || !M.variables.length) return `<p class="inv-empty">No macro exposure could be established: filings didn't discuss macro variables and price history wasn't available.</p>`;
  return `<div class="inv-tab-head"><p class="rp-fine">Broad economic variables that could move several of these investments at once. <b>Fundamental</b> = from filings and reported financials. <b>Measured</b> = how prices moved with a market proxy. Correlation is not causation.</p></div>
    <div class="inv-macros">${M.variables.map((v) => {
      const m = v.measured;
      const per = m ? Object.entries(m.per_holding).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1])) : [];
      return `<article class="inv-macro glass-flat ${INV.goto === v.id ? "goto" : ""}">
        <header><b>${escapeHtml(v.label)}</b>${v.shared ? `<span class="inv-ratio">${v.count}<i>/</i>${a.holdings.length}</span>` : ""}</header>
        <div class="inv-macro-cols">
          <div><span class="label">Fundamental</span>
            ${v.fundamental.holders.length ? `<p class="inv-dep-why">${escapeHtml(v.fundamental.text)}</p>
              <div class="inv-ev-row">${v.fundamental.holders.map((h) => `<span class="mini-tk" title="${escapeHtml(h.basis.map((b) => b.text).join("; "))}">${escapeHtml(h.ticker)}</span>`).join("")}</div>
              ${srcToggle(`m:${v.id}`, v.fundamental.holders.map((h) => `<div class="inv-ev"><b>${escapeHtml(h.ticker)}</b> ${h.basis.map((b) => `<small>${escapeHtml(b.text)}</small>${evidenceHtml(b.evidence)}`).join("")}</div>`).join(""))}`
              : `<p class="rp-fine">No holding's filings or financials point to this variable.</p>`}</div>
          <div><span class="label">Measured</span>
            ${m ? `<p class="inv-dep-why">Basket vs ${escapeHtml(m.proxy_label || m.proxy)}: ${m.metric === "beta" && m.portfolio_beta != null ? `beta <b>${m.portfolio_beta.toFixed(2)}</b>` : m.portfolio_correlation != null ? `correlation <b>${m.portfolio_correlation.toFixed(2)}</b>` : "n/a"} · ${m.window_days} trading days</p>
              <div class="inv-bars">${per.map(([t, val]) => `<div class="inv-bar-row"><span>${escapeHtml(t)}</span>${m.metric === "beta" ? `<b class="cval">${val.toFixed(2)}</b>` : corrBar(val)}</div>`).join("")}</div>
              <p class="rp-fine">${m.most_sensitive.length ? `Most sensitive historically: <b>${m.most_sensitive.map(escapeHtml).join(", ")}</b>. ` : `No holding showed a meaningful sensitivity (${escapeHtml(m.threshold || "")}). `}${escapeHtml(m.note || "")}</p>
              ${m.most_sensitive.length >= 2 ? showInMapBtn(`${v.label} (most sensitive)`, m.most_sensitive) : ""}`
              : `<p class="rp-fine">No market proxy for this variable in MarketLab's data — fundamental exposure only.</p>`}</div>
        </div></article>`;
    }).join("")}</div>
    <p class="rp-fine inv-method">${escapeHtml(M.note)} Prices: Yahoo Finance daily closes.</p>`;
}

// ---------------------------------------------------------------- CORRELATION tab
const CORR_WINDOWS = [["1m", "1M"], ["3m", "3M"], ["6m", "6M"], ["1y", "1Y"], ["3y", "3Y"], ["max", "Max"]];

function corrColor(v) {
  if (v == null) return "transparent";
  const a = Math.min(0.9, Math.abs(v) * 0.9 + 0.05).toFixed(2);
  return v >= 0 ? `rgba(var(--cool), ${a})` : `rgba(var(--amber), ${a})`;
}

function corrTabHtml(a) {
  const win = INV.corrWindow;
  const c = (R() && R().corr[win]) || (win === "1y" ? a.corr : null);
  const seg = `<div class="seg inv-corr-win" role="group" aria-label="Lookback">${CORR_WINDOWS.map(([k, l]) => `<button class="${win === k ? "on" : ""}" data-inv-corr-win="${k}">${l}</button>`).join("")}</div>`;
  if (!c) return `<div class="inv-tab-head">${seg}</div><div class="idea-busy"><span class="idea-spinner"></span>Computing ${escapeHtml(win.toUpperCase())} correlations…</div>`;
  if (!c.enough) return `<div class="inv-tab-head">${seg}</div><p class="inv-empty">Not enough overlapping price history for this lookback.${c.missing.length ? ` No prices for ${c.missing.map(escapeHtml).join(", ")}.` : ""}</p>`;
  const syms = c.symbols;
  const get = (p, q) => p === q ? 1 : (c.matrix[`${p}|${q}`] ?? c.matrix[`${q}|${p}`]);
  const sel = INV.pair;
  const pairInfo = sel ? c.pairs.find((p) => (p.a === sel[0] && p.b === sel[1]) || (p.a === sel[1] && p.b === sel[0])) : null;
  const pairRow = (p) => `<button class="inv-pair-row" data-inv-pair="${escapeHtml(p.a)},${escapeHtml(p.b)}"><span>${escapeHtml(p.a)} · ${escapeHtml(p.b)}</span><b style="color:${p.correlation >= 0.6 ? "rgb(var(--cool))" : "inherit"}">${p.correlation.toFixed(2)}</b></button>`;
  return `<div class="inv-tab-head">${seg}<p class="rp-fine">${escapeHtml(c.source)} · ${escapeHtml(c.frequency)} · ${c.range.from ? `${escapeHtml(c.range.from)} → ${escapeHtml(c.range.to)} · ${c.range.days ? `${c.range.days} days common to all` : `up to ${c.range.max_pair_days} days per pair`}` : ""}${c.missing.length ? ` · no prices for ${c.missing.map(escapeHtml).join(", ")}` : ""}${(c.short_history || []).length ? ` · shorter history: ${c.short_history.map(escapeHtml).join(", ")} (pairs use the days they share)` : ""}</p></div>
    <div class="inv-corr-grid">
      <div class="inv-matrix-wrap glass-flat"><table class="inv-matrix big"><thead><tr><th></th>${syms.map((s) => `<th>${escapeHtml(s)}</th>`).join("")}</tr></thead><tbody>
        ${syms.map((p) => `<tr><th>${escapeHtml(p)}</th>${syms.map((q) => { const v = get(p, q); const on = sel && ((sel[0] === p && sel[1] === q) || (sel[0] === q && sel[1] === p));
          return p === q ? `<td class="diag">1</td>` : `<td class="${on ? "on" : ""}" style="background:${corrColor(v)}"><button data-inv-pair="${escapeHtml(p)},${escapeHtml(q)}" aria-label="${escapeHtml(p)} and ${escapeHtml(q)}: ${v == null ? "n/a" : v.toFixed(2)}">${v == null ? "—" : v.toFixed(2)}</button></td>`; }).join("")}</tr>`).join("")}</tbody></table>
        <div class="inv-legend"><span><i style="background:rgba(var(--cool),.85)"></i>moves together</span><span><i style="background:rgba(var(--amber),.85)"></i>moves opposite</span><span class="rp-fine">Click a cell for what the pair shares.</span></div></div>
      <div class="inv-corr-side">
        <div class="inv-stat"><span class="label">Average correlation</span><b>${c.average == null ? "—" : c.average.toFixed(2)}</b><small>${escapeHtml(win.toUpperCase())} · daily returns</small></div>
        <div class="inv-stat"><span class="label">Highest</span>${c.highest.slice(0, 3).map(pairRow).join("")}</div>
        <div class="inv-stat"><span class="label">Lowest</span>${c.lowest.slice(0, 3).map(pairRow).join("")}</div>
        <div class="inv-stat"><span class="label">Clusters (≥ 0.7)</span>${c.clusters.length ? c.clusters.map((g) => `<div class="inv-ev-row">${g.map((t) => `<span class="mini-tk">${escapeHtml(t)}</span>`).join("")}</div>`).join("") : `<small>No group of holdings moved together this closely.</small>`}</div>
      </div>
    </div>
    ${pairInfo ? `<div class="inv-factor glass-flat"><header><b>${escapeHtml(pairInfo.a)} · ${escapeHtml(pairInfo.b)}</b><span class="rp-fine">correlation ${pairInfo.correlation.toFixed(2)} over ${pairInfo.days} days</span>
        <button class="small-btn" data-inv-pair-clear aria-label="Close">${icon("close", 11)}</button></header>
        ${pairInfo.shared.length ? `<p class="inv-dep-why">They also share, according to their filings: ${pairInfo.shared.map((x) => `<b>${escapeHtml(x)}</b>`).join(", ")}.</p>`
          : `<p class="inv-dep-why">No shared dependency or theme found in their filings${pairInfo.correlation >= 0.6 ? " — they move together for market / style reasons" : ""}.</p>`}
        <div class="inv-xlinks">${showInMapBtn(`${pairInfo.a} · ${pairInfo.b}`, [pairInfo.a, pairInfo.b])}<button class="pill-btn" data-inv-lens="dependency">Open Dependencies</button></div></div>` : ""}
    ${a.correlation ? `<h4 class="inv-sub">Price correlation vs shared dependencies</h4>${correlationScatterHtml(a)}`
      : `<p class="rp-fine">The correlation-vs-dependency chart appears when the filings analysis finishes.</p>`}
    <p class="inv-note-strong">${escapeHtml(c.note)}</p>`;
}

// ---------------------------------------------------------------- exposure map (bubbles + clusters)
function mapFactors(a) {
  const fromGraph = a.graph.factors.map((f) => ({ key: f.key, label: f.label, kind: f.kind, weight: f.weight, links: f.links }));
  const measured = (a.lenses.macro || []).filter((m) => m.kind === "measured" && m.count >= 2)
    .map((m) => ({ key: m.key, label: m.label, kind: "measured", weight: m.weight, links: m.holders.map((h) => ({ ticker: h.ticker, strength: "measured" })) }));
  const group = { supplier: "dep", customer: "dep", partner: "dep", geography: "dep", policy: "dep", theme: "theme", industry: "theme", sector: "theme",
                  financial: "macro", macro: "macro", measured: "macro" };
  const all = [...fromGraph, ...measured].filter((f) => new Set(f.links.map((l) => l.ticker)).size >= 2)
    .filter((f) => INV.mapFilter === "all" || group[f.kind] === INV.mapFilter);
  return all.sort((x, y) => y.links.length - x.links.length || y.weight - x.weight).slice(0, 12);
}

function exposureMapHtml(a) {
  const hold = a.holdings;
  const facs = mapFactors(a);
  if (!facs.length) return `<p class="inv-empty">${INV.mapFilter === "all" ? "No exposure is shared by two or more of these investments in the data MarketLab has (filings, classifications, fund holdings, price behaviour)." : "Nothing of this kind is shared by two or more investments."}</p>`;
  const hl = INV.highlight ? new Set(INV.highlight.tickers) : null;
  // Investments float in a row of glass bubbles; shared exposures hang below as clusters, ordered so lines cross as little as
  // possible (each cluster sits under the average position of the investments it connects). Lines = who shares what.
  const W = 940, n = hold.length;
  const hx = {};
  hold.forEach((h, i) => { hx[h.ticker] = 80 + (W - 160) * (n === 1 ? 0.5 : i / (n - 1)); });
  const hy = 64;
  const placed = facs.map((f) => {
    const ts = [...new Set(f.links.map((l) => l.ticker))].filter((t) => hx[t] != null);
    return { ...f, n: ts.length, mean: ts.reduce((s, t) => s + hx[t], 0) / Math.max(1, ts.length) };
  }).sort((x, y) => x.mean - y.mean || y.n - x.n);
  const perRow = placed.length > 6 ? Math.ceil(placed.length / 2) : placed.length;
  const rows = [placed.slice(0, perRow), placed.slice(perRow)].filter((r) => r.length);
  // interleave so both rows span the full width
  const rowY = [272, 420];
  rows.forEach((row, ri) => row.forEach((f, i) => {
    const step = (W - 120) / row.length;
    f.x = 60 + step * (i + 0.5) + (ri === 1 ? step * 0.0 : 0);
    f.y = rowY[ri] + (i % 2 ? 18 : -18) * (row.length > 4 ? 1 : 0);
  }));
  const H = rows.length > 1 ? 540 : 390;
  const maxN = Math.max(...placed.map((f) => f.n));
  const r = (f) => 16 + 14 * (f.n / Math.max(maxN, 1));
  const focus = INV.focus;
  let edges = "";
  for (const f of placed) {
    for (const l of f.links) {
      const x0 = hx[l.ticker];
      if (x0 == null) continue;
      const y0 = hy + 34, y1 = f.y - r(f);
      edges += `<path d="M${x0.toFixed(1)},${y0} C${x0.toFixed(1)},${((y0 + y1) / 2).toFixed(1)} ${f.x.toFixed(1)},${((y0 + y1) / 2).toFixed(1)} ${f.x.toFixed(1)},${y1.toFixed(1)}"
        class="map-edge ${KIND_CLASS[f.kind] || "k-sector"} ${l.strength === "mentioned" ? "weak" : ""} ${focus === f.key ? "on" : ""} ${hl && !hl.has(l.ticker) ? "dim" : ""}" data-f="${escapeHtml(f.key)}" data-t="${escapeHtml(l.ticker)}"/>`;
    }
  }
  const wrap = (label) => {
    const words = label.replace(/\s*\(.*\)$/, "").split(" ");
    const lines = [""];
    for (const w of words) { if ((lines[lines.length - 1] + " " + w).trim().length > 17 && lines.length < 2) lines.push(w); else lines[lines.length - 1] = (lines[lines.length - 1] + " " + w).trim(); }
    if (lines[1] && lines[1].length > 18) lines[1] = lines[1].slice(0, 17) + "…";
    return lines;
  };
  const clusters = placed.map((f) => {
    const lines = wrap(f.label);
    const dim = hl && !f.links.some((l) => hl.has(l.ticker));
    return `<g class="map-cluster ${KIND_CLASS[f.kind] || "k-sector"} ${focus === f.key ? "on" : ""} ${dim ? "dim" : ""}" data-inv-focus="${escapeHtml(f.key)}" data-f="${escapeHtml(f.key)}" tabindex="0" role="button" aria-label="${escapeHtml(f.label)}: ${f.n} investments">
      <title>${escapeHtml(f.label)}</title>
      <circle class="halo" cx="${f.x.toFixed(1)}" cy="${f.y.toFixed(1)}" r="${(r(f) + 7).toFixed(1)}"/>
      <circle cx="${f.x.toFixed(1)}" cy="${f.y.toFixed(1)}" r="${r(f).toFixed(1)}"/>
      <text class="count" x="${f.x.toFixed(1)}" y="${(f.y + 5).toFixed(1)}" text-anchor="middle">${f.n}</text>
      ${lines.map((ln, k) => `<text class="lbl" x="${f.x.toFixed(1)}" y="${(f.y + r(f) + 18 + k * 14).toFixed(1)}" text-anchor="middle">${escapeHtml(ln)}</text>`).join("")}
    </g>`;
  }).join("");
  const bubbles = hold.map((h) => {
    const x = hx[h.ticker];
    return `<g class="map-ticker ${hl && !hl.has(h.ticker) ? "dim" : hl ? "hl" : ""}" data-t="${escapeHtml(h.ticker)}"><circle class="halo" cx="${x.toFixed(1)}" cy="${hy}" r="40"/><circle cx="${x.toFixed(1)}" cy="${hy}" r="32"/>
      <text class="tk" x="${x.toFixed(1)}" y="${hy + 1}" text-anchor="middle">${escapeHtml(h.ticker)}</text>
      <text class="wt" x="${x.toFixed(1)}" y="${hy + 15}" text-anchor="middle">${h.weight.toFixed(0)}%</text></g>`;
  }).join("");
  const kinds = [...new Set(placed.map((f) => KIND_CLASS[f.kind] || "k-sector"))];
  const kindLabel = { "k-theme": "Theme", "k-dep": "Supplier / customer", "k-geo": "Geography", "k-policy": "Policy", "k-sector": "Sector / industry", "k-fin": "Financial", "k-measured": "Measured price sensitivity" };
  return `<div class="inv-map glass-flat"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Investments and the exposures they share">
      <defs><radialGradient id="tkGlass" cx="35%" cy="28%" r="80%"><stop offset="0" class="tkg0"/><stop offset=".6" class="tkg1"/><stop offset="1" class="tkg2"/></radialGradient></defs>
      ${edges}${bubbles}${clusters}</svg>
    <div class="inv-legend">${kinds.map((k) => `<span class="${k}"><i></i>${kindLabel[k]}</span>`).join("")}<span class="dash"><i></i>mentioned only</span><span class="rp-fine">Hover to trace · click a cluster for the evidence. The number is how many investments share it.</span></div></div>`;
}

function correlationScatterHtml(a) {
  const c = a.correlation;
  if (!c.pairs.length) return `<p class="inv-empty">Add a second investment to compare pairs.</p>`;
  const W = 600, H = 340, L = 52, B = 40, T = 16, R = 16;
  const x = (v) => L + ((v + 0.2) / 1.2) * (W - L - R);
  const y = (v) => H - B - v * (H - B - T);
  const label = { same_bet: "Same bet", hidden: "Hidden overlap", market: "Moves together", independent: "Independent" };
  const labelled = [];
  const pts = c.pairs.filter((p) => p.correlation != null).sort((p, q) => q.overlap - p.overlap).map((p) => {
    const px = x(p.correlation), py = y(p.overlap);
    const show = !labelled.some(([lx, ly]) => Math.abs(lx - px) < 95 && Math.abs(ly - py) < 18);
    if (show) labelled.push([px, py]);
    return `<g class="sc-pt k-${p.kind}"><circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="6"/>
    ${show ? `<text x="${(px + 9).toFixed(1)}" y="${(py + 4).toFixed(1)}">${escapeHtml(p.a)}·${escapeHtml(p.b)}</text>` : ""}<title>${escapeHtml(p.a)} · ${escapeHtml(p.b)}: correlation ${p.correlation.toFixed(2)}, dependency overlap ${Math.round(p.overlap * 100)}% — ${label[p.kind]}</title></g>`;
  }).join("");
  return `<div class="inv-corr">
    <div class="inv-corr-plot glass-flat"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price correlation versus dependency overlap for each pair">
      <rect x="${x(0.6)}" y="${T}" width="${W - R - x(0.6)}" height="${y(0.3) - T}" class="q q-same"/><rect x="${L}" y="${T}" width="${x(0.6) - L}" height="${y(0.3) - T}" class="q q-hidden"/>
      <rect x="${x(0.6)}" y="${y(0.3)}" width="${W - R - x(0.6)}" height="${H - B - y(0.3)}" class="q q-market"/>
      <text class="q-lbl" x="${W - R - 8}" y="${T + 16}" text-anchor="end">Same bet</text><text class="q-lbl" x="${L + 8}" y="${T + 16}">Hidden overlap</text>
      <text class="q-lbl" x="${W - R - 8}" y="${H - B - 8}" text-anchor="end">Moves together</text><text class="q-lbl" x="${L + 8}" y="${H - B - 8}">Independent</text>
      <line x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" class="ax"/><line x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" class="ax"/>
      ${[0, 0.5, 1].map((v) => `<text class="tick" x="${x(v)}" y="${H - B + 16}" text-anchor="middle">${v}</text>`).join("")}
      ${[0, 0.5, 1].map((v) => `<text class="tick" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v * 100}%</text>`).join("")}
      <text class="ax-lbl" x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle">Price correlation (daily returns, ${c.window_days} days)</text>
      <text class="ax-lbl" transform="translate(14 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">Shared business dependencies</text>
      ${pts}</svg></div>
    <div class="inv-corr-side">
      <div class="inv-stat"><span class="label">Hidden-overlap pairs</span><b>${c.pairs.filter((p) => p.kind === "hidden").length}</b><small>look diversified on a chart, share business risk</small></div>
      <div class="inv-stat"><span class="label">Same-bet pairs</span><b>${c.pairs.filter((p) => p.kind === "same_bet").length}</b><small>move together and share dependencies</small></div>
    </div></div>`;
}

// ---------------------------------------------------------------- thesis
function thesisHtml(a) {
  const st = a.stress;
  const th = a.thesis;
  return `<section class="inv-section"><div class="section-head"><h3>Thesis check</h3><span class="aside">${th && th.themes.length ? escapeHtml(th.themes.map((t) => t.label).join(" · ")) + " · " : ""}${escapeHtml(a.horizon.label)} — grounded in filings and reported numbers</span></div>
    <div class="inv-stress">
      <div class="inv-col must"><h4>What must be true?</h4>${st.must_be_true.length ? st.must_be_true.map((m) => `<div class="inv-item glass-flat"><b>${escapeHtml(m.text)}</b>
        <span class="rp-fine">${escapeHtml(m.basis)}</span>${m.evidence.length ? `<details><summary>Evidence</summary><ul>${m.evidence.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul></details>` : ""}</div>`).join("")
        : `<p class="rp-fine">Describe the thesis (a theme and a horizon) to list what it assumes.</p>`}</div>
      <div class="inv-col break"><h4>What could break it?</h4>${st.could_break.length ? st.could_break.map((b) => `<div class="inv-item glass-flat"><b>${escapeHtml(b.label)}</b>
        <span class="rp-fine">${escapeHtml(b.text)} · ${b.holdings.map(escapeHtml).join(", ")}</span>${b.evidence.length ? `<details><summary>Evidence</summary><ul>${b.evidence.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul></details>` : ""}</div>`).join("")
        : `<p class="rp-fine">No specific vulnerabilities found in the connected data.</p>`}</div>
    </div></section>`;
}

function scenarioHtml(s) {
  if (!s) return "";
  return `<div class="inv-scen-out glass-flat"><b>${escapeHtml(s.label)}</b>
    <p class="rp-fine">${escapeHtml(s.why || "")}</p>
    ${s.exposed.length ? `<p>${s.material_weight ? `<b>${s.material_weight.toFixed(0)}%</b> of the basket is materially exposed` : "Only minor mentions"}${s.any_weight > s.material_weight ? ` (${s.any_weight.toFixed(0)}% including minor mentions)` : ""}.</p>
      <ul class="inv-path">${s.exposed.map((e) => `<li><span class="tk-bubble small"><b>${escapeHtml(e.ticker)}</b></span><span class="inv-path-arrow">via</span><span>${e.pathways.map((pw) => escapeHtml(pw.label)).join("; ")}</span>
        ${e.pathways[0]?.evidence?.[0] ? `<small>“${escapeHtml(e.pathways[0].evidence[0].text.slice(0, 220))}”</small>` : ""}</li>`).join("")}</ul>` : `<p>No investment is linked to this scenario through the exposures MarketLab tracks.</p>`}
    ${s.unexposed?.length ? `<p class="rp-fine">Not reached: ${s.unexposed.map(escapeHtml).join(", ")}.</p>` : ""}
    <p class="rp-fine">Exposure pathways only. MarketLab doesn't forecast returns for scenarios.</p></div>`;
}

function holdingCardHtml(h, a) {
  const f = h.fundamentals || {};
  const blocks = {
    momentum: ["Price", [["3 months", pctv(h.ret_3m)], ["12 months", pctv(h.ret_12m)], ["From 52-week high", pctv(h.from_high)]]],
    valuation: ["Valuation", [["P/E", h.pe ? h.pe.toFixed(1) : "—"], ["Forward P/E", h.forward_pe ? h.forward_pe.toFixed(1) : "—"], ["P/S", h.ps ? h.ps.toFixed(1) : "—"]]],
    catalysts: ["Catalysts", [["Next earnings", h.next_earnings || "—"]]],
    growth: ["Business", [["Revenue growth", pctv(h.revenue_growth)], ["Gross margin", h.gross_margin == null ? "—" : `${(h.gross_margin * 100).toFixed(0)}%`], ["Operating margin", h.operating_margin == null ? "—" : `${(h.operating_margin * 100).toFixed(0)}%`]]],
    balance: ["Balance sheet", [["Cash", money(f.cash, h.statement_currency)], ["Free cash flow", money(f.free_cash_flow, h.statement_currency)], ["Shares, 1 year", pctv(h.dilution_1y)]]],
    exposures: ["Dependencies", [["Specific, from filings", String(h.factor_count)]]],
    competition: ["Competition", [["Named competitors", (h.competitors || []).slice(0, 3).join(", ") || "—"]]],
  };
  const order = h.fund ? ["momentum"] : a.emphasis.order;
  return `<article class="inv-hold-card glass-flat"><header><span class="tk-bubble small"><b>${escapeHtml(h.ticker)}</b></span><span class="inv-hold-name">${escapeHtml(h.name || "")}</span><span class="rp-spacer"></span><span class="inv-w">${h.weight.toFixed(0)}%</span></header>
    <p class="rp-fine">${escapeHtml(h.fund ? `Fund${h.industry ? ` · ${h.industry}` : ""}` : [h.sector, h.industry].filter(Boolean).join(" · "))}</p>
    ${order.map((k) => blocks[k]).filter(Boolean).slice(0, 2).map(([title, rows]) => `<div class="inv-block"><span class="label">${title}</span>${rows.map(([l, v]) =>
      `<div class="inv-kv"><span>${escapeHtml(l)}</span><b>${escapeHtml(v)}</b></div>`).join("")}</div>`).join("")}
    ${h.fund && (h.fund_top || []).length ? `<div class="inv-block"><span class="label">Largest holdings</span><div class="inv-ev-row">${h.fund_top.slice(0, 6).map((x) => `<span class="mini-tk">${escapeHtml(x.ticker)} ${(x.weight * 100).toFixed(1)}%</span>`).join("")}</div></div>` : ""}
    <button class="link-btn" data-inv-research="${escapeHtml(h.ticker)}">${icon("research", 12)}Research ${escapeHtml(h.ticker)}</button></article>`;
}

// ---------------------------------------------------------------- advanced (rendered only when opened)
function investAdvancedHtml(a) {
  const c = a.correlation;
  const syms = a.holdings.map((h) => h.ticker);
  const corr = (p, q) => p === q ? 1 : (c.matrix[`${p}|${q}`] ?? c.matrix[`${q}|${p}`]);
  const heat = (v) => v == null ? "transparent" : `rgba(52, 128, 236, ${Math.max(0.04, Math.min(0.85, v)).toFixed(2)})`;
  const sens = a.sensitivities || {};
  const proxies = ["SPY", "QQQ", "SOXX", "TLT", "UUP", "USO"];
  return `<div class="inv-adv">
    <p class="rp-fine">Weights are edited above, under the holdings (Weights).</p>
    <h4 class="rp-h4">Price correlation matrix</h4>
    <div class="table-scroll"><table class="inv-matrix"><thead><tr><th></th>${syms.map((s) => `<th>${escapeHtml(s)}</th>`).join("")}</tr></thead><tbody>
      ${syms.map((p) => `<tr><th>${escapeHtml(p)}</th>${syms.map((q) => { const v = corr(p, q); return `<td style="background:${heat(v)}">${v == null ? "—" : v.toFixed(2)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="rp-fine">Diversification ratio ${c.diversification_ratio == null ? "—" : c.diversification_ratio.toFixed(2)} (weighted volatility ÷ portfolio volatility; 1.0 = correlation gives no diversification). ${c.window_days} trading days of daily returns.</p>
    <h4 class="rp-h4">Measured sensitivity</h4>
    <div class="table-scroll"><table class="inv-matrix"><thead><tr><th></th><th>Beta (S&P 500)</th>${proxies.map((p) => `<th>${p}</th>`).join("")}</tr></thead><tbody>
      ${syms.map((s) => `<tr><th>${escapeHtml(s)}</th><td>${sens[s]?.beta == null ? "—" : sens[s].beta.toFixed(2)}</td>${proxies.map((p) => { const v = sens[s]?.[p]; return `<td style="background:${heat(v)}">${v == null ? "—" : v.toFixed(2)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="rp-fine">Correlation of daily returns with SPY (market), QQQ (tech/growth), SOXX (chip cycle), TLT (long Treasuries: rates), UUP (US dollar), USO (oil). Measured behaviour, not a dependency.</p>
    ${(a.lookthrough || []).length ? `<h4 class="rp-h4">Fund look-through</h4><ul class="inv-lt">${a.lookthrough.map((l) => `<li><b>${escapeHtml(l.fund)}</b> holds <b>${escapeHtml(l.ticker)}</b> at ${(l.fund_weight * 100).toFixed(1)}% → ${l.portfolio_weight.toFixed(2)}% of the basket</li>`).join("")}</ul>` : ""}
    <h4 class="rp-h4">Every exposure, with evidence</h4>
    <div class="table-scroll"><table class="rp-trades inv-factors"><thead><tr><th>Exposure</th><th>Kind</th><th>Investments (strength)</th><th>Weight</th></tr></thead><tbody>${a.factors.map((f) =>
      `<tr><td>${escapeHtml(f.label)}${f.general ? " <small>(listed by most companies)</small>" : ""}</td><td>${escapeHtml(KIND_NAME[f.kind] || f.kind)}</td><td>${f.holders.map((h) => `${escapeHtml(h.ticker)} <small>${escapeHtml(h.strength)}</small>`).join(", ")}</td><td>${f.material_weight.toFixed(0)}%</td></tr>`).join("")}</tbody></table></div>
    <p class="rp-fine">Strength: disclosed = a number in the filing; emphasized = discussed repeatedly in the business / risk sections; measured = computed from reported numbers or prices; classification = Yahoo sector / industry; look-through = a fund holds 5%+ in a company that discloses it; mentioned = named, but rarely. Dependency overlap = shared specific dependencies ÷ all specific dependencies of a pair (topics most companies list are excluded).</p>
  </div>`;
}


// ---------------------------------------------------------------- talking to the server (counted, cancellable)
async function invPost(url, body, signal) {
  INV.stats.req += 1;
  debugPanel();
  let response;
  try {
    response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Can't reach the MarketLab server. Is it still running in Terminal?");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

// ---------------------------------------------------------------- analysis: quick stage, then filings (progressive, cancellable)
function resetView() {
  Object.assign(INV, { focus: null, scenario: null, custom: null, highlight: null, openSrc: new Set(), goto: null, pair: null, depHolding: null,
                       chipOpen: null, confirmDelete: null, menuOpen: false, compare: false, summaryOpen: false, advancedOpen: false });
}

function snapshot(base, full) {
  const n = base.holdings.length;
  return {
    holdings: base.holdings.map((h) => `${h.ticker} ${h.weight.toFixed(0)}%`),
    avg: (full?.corr || base.corr)?.average ?? null,
    exposures: full ? (full.concentrations || []).slice(0, 3).map((c) => `${c.label.split(" (")[0]} (${c.count}/${n})`) : [],
    deps: full ? (full.dependencies?.shared || []).filter((d) => d.major).slice(0, 3).map((d) => d.label.split(" (")[0]) : [],
    themes: full ? (full.themes?.themes || []).filter((t) => t.shared).slice(0, 3).map((t) => t.label) : [],
  };
}

async function analyzeBasket({ refresh = false } = {}) {
  const a = cur();
  const pending = $inv("[data-inv-new]")?.value.trim();
  if (pending) await addTyped(pending);
  if (!a.holdings.length) { showToast("Add at least one holding first."); return; }
  const id = a.id;
  INV.ctrl[id]?.abort();                                     // a newer run for this analysis replaces the old one
  const ctrl = new AbortController();
  INV.ctrl[id] = ctrl;
  const run = ++INV.seq;
  const body = { ...requestBody(a), refresh };
  const r = { run, key: inputsKey(a), stamp: run, quick: null, full: null, st: { quick: "loading", full: "loading" }, err: {}, corr: {}, corrPending: new Set(), custom: new Map(), body };
  INV.results[id] = r;
  keepRecentResults(id);
  const live = () => INV.results[id] === r && !ctrl.signal.aborted;
  const showing = () => live() && WS.active === id && !INV.compare;
  if (WS.active === id) { resetView(); renderTop(); renderOut(); updateDirty(); }

  let t0 = performance.now();
  try {
    const q = await invPost("/api/invest/quick", body, ctrl.signal);
    if (!live()) return;
    Object.assign(r, { quick: q, stamp: ++INV.seq });
    r.corr["1y"] = q.corr;
    r.st.quick = "ok";
    INV.stats.quickMs = Math.round(performance.now() - t0);
    for (const h of q.holdings) { const mine = a.holdings.find((x) => x.ticker === h.ticker); if (mine && !mine.name) mine.name = h.name; }
    a.summary = snapshot(q, null);
    saveWorkspaces({ touch: false });
  } catch (error) {
    if (error.name === "AbortError" || !live()) return;
    r.st.quick = "error"; r.err.quick = error.message;
    r.st.full = "error"; r.err.full = "skipped — the holdings themselves could not be loaded.";
    if (showing()) paintStage();
    return;
  }
  if (showing()) paintStage();

  t0 = performance.now();
  try {
    const full = await invPost("/api/invest/analyze", { ...body, refresh: false }, ctrl.signal);   // prices were refreshed by the quick stage
    if (!live()) return;
    Object.assign(r, { full, stamp: ++INV.seq });
    r.corr["1y"] = full.corr;
    r.st.full = "ok";
    INV.stats.fullMs = Math.round(performance.now() - t0);
    a.analyzedAt = new Date().toISOString();
    a.summary = snapshot(full, full);
    saveWorkspaces({ touch: false });
  } catch (error) {
    if (error.name === "AbortError" || !live()) return;
    r.st.full = "error"; r.err.full = error.message;
  }
  if (showing()) { paintStage(); updateTopMeta(); }
}

// A stage landed: refresh progress, summary and the active tab in place. The scenario box (and its draft),
// open tabs' panes and the composer are left alone.
function paintStage() {
  const r = R();
  if (!$inv("#inv-panes") || !r) { renderOut(); return; }
  bump("stage");
  $inv("#inv-progress").innerHTML = progressHtml(r);
  $inv("#inv-summary-slot").innerHTML = summaryHtml(r);
  drawLens();
  if (!$inv("#inv-scen-sec")) renderRest();
}

// Results stay in memory for the few most recently used analyses only (inputs are always kept)
function keepRecentResults(id) {
  INV.resultOrder = [id, ...(INV.resultOrder || []).filter((x) => x !== id)];
  for (const old of INV.resultOrder.splice(8)) { if (old !== WS.active) { INV.ctrl[old]?.abort(); delete INV.results[old]; } }
}

async function loadCorr(win) {
  const r = R();
  const base = r && (r.full || r.quick);
  if (!base || r.corr[win] || r.corrPending.has(win)) return;
  r.corrPending.add(win);
  const id = WS.active;
  let c;
  try { c = await invPost("/api/invest/correlation", { analysis_id: base.id, window: win }); }
  catch (error) { c = { enough: false, missing: [], error: error.message, symbols: [], pairs: [], range: {} }; }
  r.corrPending.delete(win);
  if (INV.results[id] !== r) return;                         // re-analysed meanwhile: drop it
  r.corr[win] = c;
  if (WS.active === id && INV.lens === "market" && INV.corrWindow === win) drawLens();
}

// ---------------------------------------------------------------- parsing the text box (debounced; touches only the small rows)
let invParseTimer = null;
function scheduleParse() {
  clearTimeout(invParseTimer);
  invParseTimer = setTimeout(runParse, 450);
}
async function runParse() {
  const a = cur();
  const id = a.id, text = a.text || "";
  const seq = ++INV.parseSeq;
  if (!text.trim()) { INV.detect = null; dropDetected(a, []); renderDetected(); renderChips(); if (INV.weightsOpen) renderWeights(); return; }
  let d = INV.parseCache.get(text);
  if (!d) {
    try { d = await invPost("/api/invest/parse", { text }); } catch (e) { return; }
    INV.parseCache.set(text, d);
    if (INV.parseCache.size > 40) INV.parseCache.delete(INV.parseCache.keys().next().value);
  }
  if (seq !== INV.parseSeq || WS.active !== id) return;     // typed again / switched analysis: stale
  INV.detect = { suggestions: d.suggestions || [], themes: d.themes || [], candidates: d.candidates || [], ignored: d.ignored || [] };
  const before = a.holdings.map((h) => h.ticker).join();
  dropDetected(a, d.tickers);
  for (const h of d.detected || []) {
    if (a.holdings.some((x) => x.ticker === h.ticker) || a.dismissed.includes(h.ticker)) continue;
    a.holdings.push({ ticker: h.ticker, name: h.name || "", w: "", detected: true });
  }
  if (d.horizon && (d.horizon.id !== a.horizon || d.horizon.months !== a.months)) { a.horizon = d.horizon.id; a.months = d.horizon.months; renderHorizon(); }
  saveWorkspaces();
  renderDetected();
  if (a.holdings.map((h) => h.ticker).join() !== before) { renderChips(); if (INV.weightsOpen) renderWeights(); }
  updateDirty();
}
// Auto-detected chips the text no longer mentions go away (unless the user gave them a weight)
function dropDetected(a, tickers) {
  a.holdings = a.holdings.filter((h) => !h.detected || tickers.includes(h.ticker) || weightOf(h) != null);
}

// ---------------------------------------------------------------- holdings
function addHolding(sym, name = "") {
  sym = String(sym || "").toUpperCase().replace(/[^A-Z0-9.\-^=]/g, "").slice(0, 12);
  if (!sym) return false;
  const a = cur();
  if (a.holdings.some((h) => h.ticker === sym)) { showToast(`${sym} is already in this analysis.`); return false; }
  if (a.holdings.length >= 20) { showToast("Up to 20 holdings per analysis."); return false; }
  a.holdings.push({ ticker: sym, name, w: "", detected: false });
  a.dismissed = a.dismissed.filter((t) => t !== sym);
  saveWorkspaces();
  renderChips(); renderDetected();
  if (INV.weightsOpen) renderWeights();
  return true;
}

function removeHolding(sym) {
  const a = cur();
  const h = a.holdings.find((x) => x.ticker === sym);
  if (!h) return;
  a.holdings = a.holdings.filter((x) => x.ticker !== sym);
  if (!a.dismissed.includes(sym)) a.dismissed.push(sym);     // don't let the text box add it straight back
  if (INV.chipOpen === sym) INV.chipOpen = null;
  saveWorkspaces();
  renderChips(); renderDetected();
  if (INV.weightsOpen) renderWeights();
}

// "+ Add holding": ticker or company name, resolved through the search index
async function invSearch(q) {
  const key = q.toLowerCase();
  if (INV.searchCache.has(key)) return INV.searchCache.get(key);       // a promise: identical in-flight lookups share it
  const p = fetchJson(`/api/search?q=${encodeURIComponent(q)}`).then((d) => d.results || []).catch(() => { INV.searchCache.delete(key); return []; });
  INV.searchCache.set(key, p);
  if (INV.searchCache.size > 60) INV.searchCache.delete(INV.searchCache.keys().next().value);
  return p;
}
async function addTyped(q) {
  q = q.trim();
  if (!q) return;
  const input = $inv("[data-inv-new]");
  if (input) input.value = "";
  hideSuggest();
  const results = await invSearch(q);
  const exact = results.find((r) => r.symbol.toUpperCase() === q.toUpperCase());
  const looksTicker = /^[A-Za-z]{1,5}([.\-][A-Za-z])?$/.test(q);
  const pick = exact || (!looksTicker && results[0]) || null;
  if (pick) addHolding(pick.symbol, pick.name || "");
  else if (looksTicker) addHolding(q);
  else { showToast(`Could not resolve “${q}”.`); return; }
  $inv("[data-inv-new]")?.focus();
}

let invSuggestTimer = null, invSuggestSeq = 0;
function suggestHoldings(q) {
  clearTimeout(invSuggestTimer);
  if (!q.trim()) { hideSuggest(); return; }
  invSuggestTimer = setTimeout(async () => {
    const seq = ++invSuggestSeq;
    const results = await invSearch(q.trim());
    if (seq !== invSuggestSeq || $inv("[data-inv-new]")?.value.trim() !== q.trim()) return;
    INV.suggest = results.slice(0, 6);
    INV.suggestIdx = 0;
    drawSuggest();
  }, 160);
}
function drawSuggest() {
  const box = $inv("#inv-suggest");
  if (!box) return;
  if (!INV.suggest.length) { box.hidden = false; box.innerHTML = `<div class="rp-fine">No match — Enter adds it as a ticker.</div>`; return; }
  box.hidden = false;
  box.innerHTML = INV.suggest.map((s, i) => `<button role="option" tabindex="-1" aria-selected="${i === INV.suggestIdx}" class="${i === INV.suggestIdx ? "on" : ""}" data-inv-pick="${escapeHtml(s.symbol)}" data-name="${escapeHtml(s.name || "")}">
    <b>${escapeHtml(s.symbol)}</b><span>${escapeHtml(s.name || "")}</span><small>${escapeHtml(s.exchange || "")}</small></button>`).join("");
}
function hideSuggest() { INV.suggest = []; const box = $inv("#inv-suggest"); if (box) { box.hidden = true; box.innerHTML = ""; } }

// ---------------------------------------------------------------- weights (edited like normal inputs; rescaled only on request)
function syncWeightUI(ticker, source) {
  const a = cur();
  const h = a.holdings.find((x) => x.ticker === ticker);
  invRoot().querySelectorAll(`[data-inv-w="${CSS.escape(ticker)}"]`).forEach((el) => { if (el !== source) el.value = h?.w ?? ""; });
  const small = $inv(`[data-inv-chip="${CSS.escape(ticker)}"] small`);
  if (small && h) small.textContent = weightOf(h) != null ? `${weightOf(h)}%` : "auto";
  const total = $inv("#inv-total");
  if (total) total.innerHTML = totalText(weightTotal(a));
  updateDirty();
}

function round2(v) { return Math.round(v * 100) / 100; }
function setWeights(values) {                              // values: numbers summing to 100 − cash; last one absorbs rounding
  const a = cur();
  const target = 100 - cashOf(a);
  const rounded = values.map(round2);
  if (rounded.length) rounded[rounded.length - 1] = round2(target - rounded.slice(0, -1).reduce((s, v) => s + v, 0));
  a.holdings.forEach((h, i) => { h.w = String(rounded[i]); h.detected = false; });
  saveWorkspaces();
  renderWeights(); renderChips();
}
function equalWeights() {
  const a = cur();
  const n = a.holdings.length;
  if (!n) return;
  setWeights(a.holdings.map(() => Math.max(0, 100 - cashOf(a)) / n));
}
function normalizeWeights() {
  const a = cur();
  const n = a.holdings.length;
  if (!n) return;
  const target = Math.max(0, 100 - cashOf(a));
  const set = a.holdings.map(weightOf).map((v) => (v == null ? null : Math.max(0, v)));
  const given = set.reduce((s, v) => s + (v || 0), 0);
  const blanks = set.filter((v) => v == null).length;
  const fill = blanks ? Math.max(0, target - given) / blanks : 0;
  const filled = set.map((v) => (v == null ? fill : v));
  const sum = filled.reduce((s, v) => s + v, 0);
  if (sum <= 0) return equalWeights();
  setWeights(filled.map((v) => (v / sum) * target));
}

// ---------------------------------------------------------------- analyses (workspaces)
function openAnalysis(id) {
  if (!WS.items[id]) return;
  WS.active = id;
  resetView();
  INV.detect = null; INV.parseSeq += 1; clearTimeout(invParseTimer);
  saveWorkspaces({ touch: false });
  renderAll();                                              // results come from memory if this analysis already ran
  if (cur().text.trim()) runParse();
}
function newAnalysis(name, holdings = []) {
  const a = blankAnalysis(name || `Analysis ${Object.keys(WS.items).length + 1}`);
  a.holdings = holdings.map((t) => ({ ticker: t.trim().toUpperCase(), name: "", w: "", detected: false })).filter((h) => h.ticker);
  WS.items[a.id] = a;
  openAnalysis(a.id);
  return a;
}
function duplicateAnalysis() {
  const src = cur();
  const copy = JSON.parse(JSON.stringify(src));
  Object.assign(copy, { id: newId(), name: `${src.name} (copy)`.slice(0, 80), createdAt: new Date().toISOString(), archived: false, legacyId: undefined });
  WS.items[copy.id] = copy;
  if (INV.results[src.id]) INV.results[copy.id] = INV.results[src.id];   // same inputs → same results, no recompute
  openAnalysis(copy.id);
  saveWorkspaces();
  showToast("Duplicated — edits now apply to the copy only.");
}
function fallbackActive(exceptId) {
  const next = Object.values(WS.items).filter((x) => x.id !== exceptId && !x.archived).sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""))[0];
  if (next) openAnalysis(next.id); else newAnalysis("Untitled analysis");
}
function archiveAnalysis() {
  const a = cur();
  a.archived = !a.archived;
  saveWorkspaces();
  if (a.archived) { showToast(`Archived “${a.name}”. Show archived in the list to restore it.`); fallbackActive(a.id); }
  else renderTop();
}
function deleteAnalysis() {
  const a = cur();
  if (INV.confirmDelete !== a.id) {
    INV.confirmDelete = a.id; renderTop();
    setTimeout(() => { if (INV.confirmDelete === a.id) { INV.confirmDelete = null; renderTop(); } }, 4000);
    return;
  }
  INV.ctrl[a.id]?.abort();
  delete INV.results[a.id]; delete INV.ctrl[a.id]; delete WS.items[a.id];
  INV.confirmDelete = null;
  fallbackActive(a.id);
  saveWorkspaces({ touch: false });
  showToast(`Deleted “${a.name}”.`);
}

// Called from the home page: "Am I diversified? NVDA · AMD · …" — always a NEW analysis, never overwrites the current one
function investStartBasket(tickers, name) {
  if (!INV.rendered) renderInvest();
  const a = cur();
  const reuse = !a.holdings.length && !(a.text || "").trim() && !a.analyzedAt;
  const list = tickers.map((t) => t.trim().toUpperCase()).filter(Boolean);
  if (reuse) {
    a.holdings = list.map((t) => ({ ticker: t, name: "", w: "", detected: false }));
    if (name) a.name = name;
    saveWorkspaces(); renderAll();
  } else newAnalysis(name || list.slice(0, 4).join(" · "), list);
  analyzeBasket();
}

// ---------------------------------------------------------------- events
function investInput(event) {
  const t = event.target;
  const a = cur();
  if (t.matches("[data-inv-text]")) { a.text = t.value; saveWorkspaces(); updateDirty(); scheduleParse(); return; }
  if (t.matches("[data-inv-w]")) {
    const h = a.holdings.find((x) => x.ticker === t.dataset.invW);
    if (h) { h.w = t.value.slice(0, 8); h.detected = false; saveWorkspaces(); syncWeightUI(h.ticker, t); }
    return;
  }
  if (t.matches("[data-inv-cash]")) { a.cash = t.value.slice(0, 8); saveWorkspaces(); syncWeightUI("", t); return; }
  if (t.matches("[data-inv-months]")) { const m = parseInt(t.value, 10); if (m > 0 && m <= 600) { a.months = m; saveWorkspaces(); updateDirty(); } return; }
  if (t.matches("[data-ws-name]")) { a.name = t.value; saveWorkspaces(); const n = $inv(".inv-ws-name"); if (n) n.textContent = t.value || "Untitled analysis"; return; }
  if (t.matches("[data-ws-notes]")) { a.notes = t.value; saveWorkspaces(); return; }
  if (t.matches("[data-inv-new]")) { suggestHoldings(t.value); return; }
  if (t.matches("[data-inv-what-if]")) {                   // local draft only: no analysis, no re-render
    INV.whatIf[WS.active] = t.value;
    const x = $inv("[data-inv-what-if-clear]"); if (x) x.hidden = !t.value;
  }
}

function investChange() {}

function investBlur(event) {
  const t = event.target;
  if (t.matches("[data-ws-name]")) { const a = cur(); if (!a.name.trim()) { a.name = "Untitled analysis"; t.value = a.name; saveWorkspaces(); } renderTop(); }
  if (t.matches("[data-inv-new]")) setTimeout(() => { if (document.activeElement !== $inv("[data-inv-new]")) hideSuggest(); }, 120);
  if (t.matches("[data-inv-text]") && invParseTimer) { clearTimeout(invParseTimer); runParse(); }
}

function investKeys(event) {
  const t = event.target;
  if (t.matches("[data-inv-new]")) {
    const open = INV.suggest.length && !$inv("#inv-suggest")?.hidden;
    if (event.key === "ArrowDown" && open) { event.preventDefault(); INV.suggestIdx = (INV.suggestIdx + 1) % INV.suggest.length; drawSuggest(); return; }
    if (event.key === "ArrowUp" && open) { event.preventDefault(); INV.suggestIdx = (INV.suggestIdx - 1 + INV.suggest.length) % INV.suggest.length; drawSuggest(); return; }
    if (event.key === "Escape") { hideSuggest(); return; }
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      const pick = open ? INV.suggest[INV.suggestIdx] : null;
      const typed = t.value.trim();
      if (pick && typed && pick.symbol.toUpperCase() !== typed.toUpperCase() && /^[A-Za-z]{1,5}$/.test(typed) && typed === typed.toUpperCase()) { addTyped(typed); return; }
      if (pick) { t.value = ""; hideSuggest(); addHolding(pick.symbol, pick.name || ""); $inv("[data-inv-new]")?.focus(); }
      else if (typed) addTyped(typed);
    }
    return;
  }
  if (event.key === "Escape") {
    if (INV.menuOpen) { INV.menuOpen = false; renderTop(); return; }
    if (INV.chipOpen) { INV.chipOpen = null; renderChips(); return; }
  }
  if (t.matches("[data-inv-what-if]")) {
    if (event.key === "Enter") { event.preventDefault(); traceWhatIf(); }
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); t.blur(); }   // Esc leaves the text alone
    return;
  }
  if (event.key === "Enter" && t.matches("[data-inv-w], [data-inv-cash], [data-inv-months]")) { event.preventDefault(); t.blur(); return; }
  if (event.key === "Enter" && t.matches("[data-ws-name]")) { event.preventDefault(); t.blur(); return; }
  if ((event.key === "Enter" || event.key === " ") && t.matches("[data-inv-focus]") && t.tagName !== "BUTTON") { event.preventDefault(); t.dispatchEvent(new MouseEvent("click", { bubbles: true })); return; }
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && t.matches("[data-inv-text]")) { event.preventDefault(); analyzeBasket(); }
}

// Trace = the only thing that commits a scenario. Typing never does work beyond saving the draft.
async function traceWhatIf(textArg) {
  const input = $inv("[data-inv-what-if]");
  const text = (textArg ?? input?.value ?? "").trim().slice(0, 300);
  const r = R();
  if (!text || !r?.full) return;
  const id = WS.active, seq = ++INV.traceSeq;
  const a = cur();
  a.recentScen = [text, ...(a.recentScen || []).filter((x) => x.toLowerCase() !== text.toLowerCase())].slice(0, 5);
  saveWorkspaces({ touch: false });
  const recentEl = $inv("#inv-scen-recent"); if (recentEl) recentEl.innerHTML = recentScenHtml(a.recentScen);
  const show = (out) => {
    INV.custom = out; INV.scenario = null;
    const el = $inv("#inv-scen-out"); if (el) el.innerHTML = scenarioHtml(out);
    invRoot().querySelectorAll("[data-inv-scen].on").forEach((b) => b.classList.remove("on"));
  };
  const key = text.toLowerCase();
  if (r.custom.has(key)) { show(r.custom.get(key)); return; }          // same question, same analysis: instant
  const status = $inv("#inv-trace-status");
  if (status) status.innerHTML = `<span class="idea-spinner"></span> Tracing…`;
  try {
    const out = await invPost("/api/invest/scenario", { analysis_id: r.full.id, text, inputs: { ...r.body, refresh: false } });
    r.custom.set(key, out);
    if (WS.active !== id || R() !== r || seq !== INV.traceSeq) return;    // switched / re-analysed / newer trace: drop it
    show(out);
  } catch (error) {
    if (WS.active === id && seq === INV.traceSeq) showToast(error.message);
  } finally {
    const st = $inv("#inv-trace-status");
    if (st && seq === INV.traceSeq) st.textContent = "";
  }
}

function investClick(event) {
  const t = event.target;
  const el = (sel) => t.closest(sel);
  let hit;
  if (INV.menuOpen && !el(".inv-ws-menu") && !el("[data-ws-menu]")) { INV.menuOpen = false; renderTop(); }

  // ---- analyses
  if (el("[data-ws-menu]")) { INV.menuOpen = !INV.menuOpen; renderTop(); return; }
  if ((hit = el("[data-ws-open]"))) { INV.menuOpen = false; openAnalysis(hit.dataset.wsOpen); return; }
  if (el("[data-ws-new]")) { newAnalysis(); $inv("[data-inv-text]")?.focus(); return; }
  if (el("[data-ws-dup]")) { duplicateAnalysis(); return; }
  if (el("[data-ws-archive]")) { archiveAnalysis(); return; }
  if (el("[data-ws-delete]")) { deleteAnalysis(); return; }
  if (el("[data-ws-archived]")) { INV.showArchived = !INV.showArchived; INV.menuOpen = true; renderTop(); return; }
  if ((hit = el("[data-ws-tag]"))) {
    const a = cur(), tag = hit.dataset.wsTag;
    a.tags = a.tags.includes(tag) ? a.tags.filter((x) => x !== tag) : [...a.tags, tag];
    hit.classList.toggle("on", a.tags.includes(tag)); hit.setAttribute("aria-pressed", a.tags.includes(tag));
    saveWorkspaces(); return;
  }
  if (el("[data-ws-compare]")) {
    INV.compare = !INV.compare;
    if (INV.compare && !INV.compareSel.size) Object.values(WS.items).filter((x) => x.summary && !x.archived).slice(0, 3).forEach((x) => INV.compareSel.add(x.id));
    renderOut();
    if (INV.compare) $inv("#inv-out")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if ((hit = el("[data-ws-compare-pick]"))) { const id = hit.dataset.wsComparePick; if (INV.compareSel.has(id)) INV.compareSel.delete(id); else INV.compareSel.add(id); renderOut(); return; }
  if (el("[data-ws-compare-close]")) { INV.compare = false; renderOut(); return; }

  // ---- composer
  if ((hit = el("[data-inv-horizon]"))) { const a = cur(); a.horizon = hit.dataset.invHorizon; saveWorkspaces(); renderHorizon(); updateDirty(); return; }
  if (el("[data-inv-toggle-weights]")) {
    INV.weightsOpen = !INV.weightsOpen; renderWeights();
    const b = $inv("[data-inv-toggle-weights]"); b.textContent = INV.weightsOpen ? "Hide weights" : "Weights"; b.setAttribute("aria-expanded", INV.weightsOpen);
    if (INV.weightsOpen) $inv("#inv-weights input")?.focus();
    return;
  }
  if (el("[data-inv-toggle-notes]")) { INV.notesOpen = !INV.notesOpen; renderCompose(); if (INV.notesOpen) $inv("[data-ws-notes]")?.focus(); return; }
  if (el("[data-inv-equal]")) { equalWeights(); return; }
  if (el("[data-inv-normalize]")) { normalizeWeights(); return; }
  if ((hit = el("[data-inv-pick]"))) { const input = $inv("[data-inv-new]"); if (input) input.value = ""; hideSuggest(); addHolding(hit.dataset.invPick, hit.dataset.name || ""); $inv("[data-inv-new]")?.focus(); return; }
  if ((hit = el("[data-inv-accept]"))) { addHolding(hit.dataset.invAccept); return; }
  if ((hit = el("[data-inv-dismiss]"))) { const a = cur(); if (!a.dismissed.includes(hit.dataset.invDismiss)) a.dismissed.push(hit.dataset.invDismiss); saveWorkspaces(); renderDetected(); return; }
  if ((hit = el("[data-inv-add]"))) { addHolding(hit.dataset.invAdd); return; }
  if ((hit = el("[data-inv-remove]"))) { removeHolding(hit.dataset.invRemove); return; }
  if ((hit = el("[data-inv-chip]"))) {
    INV.chipOpen = INV.chipOpen === hit.dataset.invChip ? null : hit.dataset.invChip; renderChips();
    if (INV.chipOpen) $inv(".inv-chip-detail [data-inv-w]")?.focus();
    return;
  }
  if (el(".inv-bubbles") && !el("button") && !el("input")) { $inv("[data-inv-new]")?.focus(); return; }
  if ((hit = el("[data-inv-example]"))) { investStartBasket(hit.dataset.invExample.split(","), hit.dataset.label); return; }
  if (el("[data-inv-go]")) { analyzeBasket(); return; }
  if (el("[data-inv-refresh]")) { analyzeBasket({ refresh: true }); return; }

  // ---- results
  const full = R()?.full;
  if ((hit = el("[data-inv-lens]"))) {
    INV.lens = hit.dataset.invLens; INV.focus = null; INV.goto = hit.dataset.invGoto || null;
    if (INV.goto && INV.lens === "dependency" && full) { const d = full.dependencies.shared.find((x) => x.key === INV.goto); if (d) { INV.depCat = "all"; if (d.general) INV.showGeneral = true; } }
    drawLens();
    if (INV.lens === "market") loadCorr(INV.corrWindow);
    if (hit.closest(".inv-factor, .inv-xlinks, .inv-holding")) $inv("#inv-lenses")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if ((hit = el("[data-inv-focus]"))) { INV.focus = INV.focus === hit.dataset.invFocus ? null : hit.dataset.invFocus; drawLens(); return; }
  if ((hit = el("[data-inv-map-filter]"))) { INV.mapFilter = hit.dataset.invMapFilter; drawLens(); return; }
  if ((hit = el("[data-inv-show-map]"))) {
    INV.highlight = { label: hit.dataset.label, tickers: hit.dataset.invShowMap.split(",") }; INV.lens = "map"; INV.focus = null;
    drawLens(); $inv("#inv-lenses")?.scrollIntoView({ behavior: "smooth", block: "start" }); return;
  }
  if ((hit = el(".map-ticker"))) { INV.depHolding = hit.dataset.t; INV.lens = "dependency"; INV.goto = null; drawLens(); return; }
  if (el("[data-inv-hl-clear]")) { INV.highlight = null; drawLens(); return; }
  if ((hit = el("[data-inv-src]"))) { const k = hit.dataset.invSrc; if (INV.openSrc.has(k)) INV.openSrc.delete(k); else INV.openSrc.add(k); drawLens(); return; }
  if ((hit = el("[data-inv-dep-cat]"))) { INV.depCat = hit.dataset.invDepCat; INV.goto = null; drawLens(); return; }
  if ((hit = el("[data-inv-dep-holding]"))) { INV.depHolding = hit.dataset.invDepHolding; INV.goto = null; drawLens(); return; }
  if (el("[data-inv-general]")) { INV.showGeneral = !INV.showGeneral; drawLens(); return; }
  if ((hit = el("[data-inv-corr-win]"))) { INV.corrWindow = hit.dataset.invCorrWin; INV.pair = null; drawLens(); loadCorr(INV.corrWindow); return; }
  if ((hit = el("[data-inv-pair]"))) { INV.pair = hit.dataset.invPair.split(","); drawLens(); return; }
  if (el("[data-inv-pair-clear]")) { INV.pair = null; drawLens(); return; }
  if ((hit = el("[data-inv-scen]")) && full) {
    const i = Number(hit.dataset.invScen); INV.custom = null; INV.scenario = INV.scenario === i ? null : i;
    invRoot().querySelectorAll("[data-inv-scen]").forEach((b) => b.classList.toggle("on", Number(b.dataset.invScen) === INV.scenario));
    const out = $inv("#inv-scen-out"); if (out) out.innerHTML = INV.scenario != null ? scenarioHtml(full.scenarios[INV.scenario]) : "";
    return;
  }
  if (el("[data-inv-what-if-go]")) { traceWhatIf(); return; }
  if (el("[data-inv-what-if-clear]")) {
    INV.whatIf[WS.active] = "";
    const input = $inv("[data-inv-what-if]"); if (input) { input.value = ""; input.focus(); }
    el("[data-inv-what-if-clear]").hidden = true;
    return;
  }
  if ((hit = el("[data-inv-recent-edit]")) || (hit = el("[data-inv-recent]"))) {
    const text = (cur().recentScen || [])[Number(hit.dataset.invRecentEdit ?? hit.dataset.invRecent)];
    if (!text) return;
    INV.whatIf[WS.active] = text;
    const input = $inv("[data-inv-what-if]");
    if (input) input.value = text;
    const x = $inv("[data-inv-what-if-clear]"); if (x) x.hidden = false;
    if (hit.dataset.invRecentEdit != null) { input?.focus(); input?.setSelectionRange(text.length, text.length); } else traceWhatIf(text);
    return;
  }
  if ((hit = el("[data-inv-research]"))) { openResearch(hit.dataset.invResearch); }
}

// Hovering a cluster or a ticker lights up its connections — listeners live on the map only, not the whole page
function bindMapHover() {
  const map = $inv(".inv-map");
  if (!map || map.dataset.bound) return;
  map.dataset.bound = "1";
  map.addEventListener("mouseover", investHover);
  map.addEventListener("mouseout", investHover);
}
let invHoverNode = null;
function investHover(event) {
  const svg = event.currentTarget.querySelector("svg");
  if (!svg) return;
  const g = event.type === "mouseover" ? event.target.closest(".map-cluster, .map-ticker") : (event.relatedTarget?.closest?.(".map-cluster, .map-ticker") || null);
  if (g === invHoverNode) return;                          // still on the same node (or still on empty space): nothing to do
  invHoverNode = g;
  svg.querySelectorAll(".lit").forEach((x) => x.classList.remove("lit"));
  svg.classList.toggle("hovering", !!g);
  if (!g) return;
  g.classList.add("lit");
  const sel = g.classList.contains("map-cluster") ? `[data-f="${CSS.escape(g.dataset.f)}"]` : `.map-edge[data-t="${CSS.escape(g.dataset.t)}"]`;
  svg.querySelectorAll(sel).forEach((x) => {
    x.classList.add("lit");
    if (x.classList.contains("map-edge")) {
      svg.querySelector(`.map-ticker[data-t="${CSS.escape(x.dataset.t)}"]`)?.classList.add("lit");
      svg.querySelector(`.map-cluster[data-f="${CSS.escape(x.dataset.f)}"]`)?.classList.add("lit");
    }
  });
}

// ---------------------------------------------------------------- debug / performance panel (off unless ?debug or localStorage marketlab.debug = "1")
let invDebugQueued = false;
function debugOn() {
  try { return localStorage.getItem("marketlab.debug") === "1" || /[?&#]debug\b/.test(location.href); } catch (e) { return false; }
}
function debugPanel() {
  if (invDebugQueued || !debugOn()) return;
  invDebugQueued = true;
  requestAnimationFrame(() => {
    invDebugQueued = false;
    const el = $inv("#inv-debug");
    if (!el) return;
    const s = INV.stats;
    const map = $inv(".inv-map svg");
    el.hidden = false;
    el.innerHTML = `<b>perf</b> renders ${Object.entries(s.renders).map(([k, v]) => `${k}:${v}`).join(" ")}
      · requests ${s.req} · quick ${s.quickMs ?? "—"} ms · filings ${s.fullMs ?? "—"} ms · last tab ${s.lensMs ?? "—"} ms
      · map nodes ${map ? map.querySelectorAll("*").length : 0} · DOM ${invRoot().querySelectorAll("*").length} · analyses ${Object.keys(WS.items).length}`;
  });
}
