// =========================================================
// app.js — the SHELL: environments, search, research windows.
//
// Level 1 (top bar):  Research | Lab | Notebook | Replay | Portfolio
// Level 2 (inside a Research window): Overview | Profile | Earnings | Chart | News | Risk
//
//   switchEnv(name)       show one environment
//   openStock(ticker)     open (or focus) a Research window
//   loadPart(win, part)   fetch one piece of a window's data
//
// What goes INSIDE a tab: tabs.js, tab_*.js, charts.js.
// The Lab: lab.js. Notebook: notebook.js. AI panel: assistant.js.
// =========================================================

const workspace   = document.getElementById("workspace");
const searchForm  = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const searchList  = document.getElementById("search-results");
const emptyState  = document.getElementById("empty-state");
const dock        = document.getElementById("dock");
const toasts      = document.getElementById("toasts");
const tooltip     = document.getElementById("tooltip");
const popover     = document.getElementById("popover");
const template    = document.getElementById("window-template");

const MIN_WIDTH = 420;
const MIN_HEIGHT = 320;
const DEFAULT_WIDTH = 760;
const DEFAULT_HEIGHT = 720;

// The pieces of data a window can load. Each has its own URL, so they load
// in parallel and a failure in one never blocks the others.
const PARTS = {
  overview: { endpoint: () => "",           label: "Market" },
  summary:  { endpoint: () => "/summary",   label: "Description" },
  filings:  { endpoint: () => "/filings",   label: "SEC" },
  profile:  { endpoint: () => "/profile",   label: "Financials" },
  earnings: { endpoint: () => "/earnings",  label: "Earnings" },
  news:     { endpoint: () => "/news",      label: "News" },
  risk:     { endpoint: (win) => {
    const p = win.riskParams || {};
    return `/risk?period=${p.period || "full"}${p.custom ? `&custom=${p.custom}` : ""}`;
  }, label: "Risk" },
};

// "AAPL" -> { ticker, el, chip, data, status, errors, pending, activeTab, minimized, maximized, charts }
const windows = new Map();
let topZ = 10;
let openCount = 0;


// =========================================================
// 1. Saved state (this browser)
// =========================================================
const LAYOUT_KEY = "marketlab.layout.v3";
const LAB_KEY_STORE = "marketlab.lab.v2";
const layout = loadStored(sessionStorage, LAYOUT_KEY, { open: [], windows: {}, env: "research" });

function loadStored(store, key, fallback) {
  try {
    const saved = JSON.parse(store.getItem(key));
    if (saved && typeof saved === "object") return { ...fallback, ...saved };
  } catch (error) { /* storage unavailable: start fresh */ }
  return fallback;
}

function saveLayout() {
  layout.open = [...windows.keys()];
  for (const win of windows.values()) {
    const geometry = win.maximized ? win.restoreRect : currentRect(win.el);
    layout.windows[win.ticker] = { ...geometry, tab: win.activeTab, minimized: win.minimized };
  }
  try { sessionStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch (error) { /* ignore */ }
}

function currentRect(el) {
  return {
    left: parseFloat(el.style.left) || 0,
    top: parseFloat(el.style.top) || 0,
    width: parseFloat(el.style.width) || DEFAULT_WIDTH,
    height: parseFloat(el.style.height) || DEFAULT_HEIGHT,
  };
}


// =========================================================
// 2. Environments (level-1 navigation)
// =========================================================
const ENVS = ["research", "invest", "lab", "notebook", "replay", "data"];
let currentEnv = null;

function switchEnv(name) {
  if (!ENVS.includes(name)) name = "research";
  if (name === currentEnv) return;
  currentEnv = name;
  document.querySelectorAll(".env").forEach((el) => { el.hidden = el.dataset.env !== name; });
  document.querySelectorAll("[data-env]").forEach((b) => {
    b.classList.toggle("active", b.dataset.env === name);
    b.setAttribute("aria-current", b.dataset.env === name ? "page" : "false");
  });
  document.body.dataset.env = name;
  writeHash();
  if (name === "notebook") showNotebook();
  if (name === "research") requestAnimationFrame(refitWindows);
  if (name === "lab" && !labState.rendered) { renderLab(labState); labState.rendered = true; }
  if (name === "invest") renderInvest();
  closeMoreMenu();
  layout.env = name;
  saveLayout();
}

document.getElementById("env-nav").addEventListener("click", (event) => {
  const button = event.target.closest("[data-env]");
  if (button && !button.disabled) switchEnv(button.dataset.env);
});
document.addEventListener("click", (event) => {
  const go = event.target.closest("[data-env-go]");
  if (go) switchEnv(go.dataset.envGo);
});

// Secondary menu (···): Replay, Advanced research, Quick test, Data & methodology
const moreBtn = document.getElementById("more-btn");
const moreMenu = document.getElementById("more-menu");
function closeMoreMenu() { if (moreMenu && !moreMenu.hidden) { moreMenu.hidden = true; moreBtn.setAttribute("aria-expanded", "false"); } }
moreBtn.addEventListener("click", (event) => {
  event.stopPropagation();
  const r = moreBtn.getBoundingClientRect();
  moreMenu.style.left = `${Math.max(12, r.right - 300)}px`;
  moreMenu.style.top = `${r.bottom + 10}px`;
  moreMenu.hidden = !moreMenu.hidden;
  moreBtn.setAttribute("aria-expanded", String(!moreMenu.hidden));
  if (!moreMenu.hidden) moreMenu.querySelector("button")?.focus();
});
moreMenu.addEventListener("click", (event) => {
  const b = event.target.closest("[data-more]");
  if (!b) return;
  const what = b.dataset.more;
  closeMoreMenu();
  if (what === "replay" || what === "data") { switchEnv(what); return; }
  goLab(what === "advanced" ? "projects" : "quick");
});
document.addEventListener("click", (event) => { if (!event.target.closest("#more-menu")) closeMoreMenu(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMoreMenu(); });

// ---------- Appearance: Light / Dark / System (persisted; System follows the OS live) ----------
const THEME_KEY = "marketlab.theme";
const darkQuery = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
function themePref() { try { return localStorage.getItem(THEME_KEY) || "light"; } catch (e) { return "light"; } }
function applyTheme(pref, { save = true } = {}) {
  if (save) { try { localStorage.setItem(THEME_KEY, pref); } catch (e) { /* ignore */ } }
  const dark = pref === "dark" || (pref === "system" && darkQuery && darkQuery.matches);
  const before = document.documentElement.dataset.theme;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themePref = pref;
  document.querySelectorAll("[data-theme-set]").forEach((b) => { b.classList.toggle("on", b.dataset.themeSet === pref); b.setAttribute("aria-pressed", String(b.dataset.themeSet === pref)); });
  if (before !== document.documentElement.dataset.theme) refreshThemedViews();
}
// Canvas charts take their colours at creation: redraw what's on screen (SVG and CSS adapt by themselves)
function refreshThemedViews() {
  if (typeof windows !== "undefined") for (const win of windows.values()) {
    if (!win.minimized && win.activeTab && ["overview", "chart"].includes(win.activeTab)) showTab(win, win.activeTab, { keepScroll: true });
  }
}
document.addEventListener("click", (event) => {
  const set = event.target.closest("[data-theme-set]");
  if (set) { event.stopPropagation(); applyTheme(set.dataset.themeSet); }
}, true);
document.getElementById("theme-toggle").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
if (darkQuery) darkQuery.addEventListener("change", () => { if (themePref() === "system") applyTheme("system", { save: false }); });
applyTheme(themePref(), { save: false });

// Open the Lab in a given mode ("idea", "trades", "quick", "projects")
function goLab(mode) {
  labState.mode = mode;
  labState.rendered = false;
  if (currentEnv === "lab") { renderLab(labState); labState.rendered = true; } else switchEnv("lab");
  writeHash();
}

// The Lab is an environment with a single persistent state object.
const savedLab = loadStored(localStorage, LAB_KEY_STORE, {});
const labState = {
  ticker: "@LAB", kind: "lab",
  el: document.getElementById("lab-env"),
  def: savedLab.def || (savedLab.params ? definitionFromLegacy(savedLab.params) : defaultDefinition("NVDA")),
  entry: savedLab.entry || null,
  result: null,
  mode: initialLabMode(),   // "idea", "trades" (idea.js), "quick" (sentence builder), "projects" (research.js)
};

function saveLabState() {
  try { localStorage.setItem(LAB_KEY_STORE, JSON.stringify({ def: labState.def, entry: labState.entry })); } catch (error) { /* ignore */ }
}

// Research -> Lab. Every contextual "Test this…" action comes through here.
//   openLab({ ticker })                       keep the experiment, switch instrument
//   openLab({ ticker, preset, origin })       start from a prepared idea (the previous
//                                             experiment can be restored from the banner)
function openLab({ ticker = null, preset = null, origin = null, run = false } = {}) {
  const changed = (ticker && ticker !== labState.def.instrument.symbol) || preset;
  if (changed) {
    labState.previous = { def: JSON.parse(JSON.stringify(labState.def)), result: labState.result, entry: labState.entry };
    const def = preset ? { ...defaultDefinition(ticker || labState.def.instrument.symbol), ...JSON.parse(JSON.stringify(preset)) } : labState.def;
    if (ticker) def.instrument = { ...def.instrument, symbol: ticker };
    def.conditions = def.conditions.map((c, i) => ({ id: c.id || `c${i + 1}`, ...c }));
    labState.def = def;
    labState.result = null;
    labState.challenge = null;
    labState.entry = null;
    labState.origin = origin;
    labState.rendered = false;
    saveLabState();
  }
  if (labState.mode !== "quick") { labState.mode = "quick"; labState.rendered = false; }
  switchEnv("lab");
  if (!labState.rendered) { renderLab(labState); labState.rendered = true; }
  if (run) runExperiment(labState);
}

// Contextual "Test …" buttons inside Research tabs. Each builds a starting
// experiment from what the user is looking at, and says where it came from.
function contextLabClick(win, event) {
  const preset = event.target.closest("[data-lab-preset]");
  if (preset) {
    openLab({ ticker: win.ticker, preset: JSON.parse(preset.dataset.labPreset),
              origin: { ticker: win.ticker, from: preset.dataset.labFrom, note: preset.dataset.labNote || "" } });
    return true;
  }
  if (event.target.closest("[data-lab-chart]")) {
    loadChart(win.ticker, "1Y").then((data) => {
      const closes = data.close.filter(isNum);
      const move = closes[closes.length - 1] / closes[closes.length - 6] - 1;
      const threshold = Math.max(1, Math.floor(Math.abs(move) * 200) / 2);
      openLab({ ticker: win.ticker, origin: { ticker: win.ticker, from: "Chart",
        note: `${win.ticker} moved ${stripTags(fmtPct(move, true, 1))} over the last 5 trading days. This tests what followed moves at least that large.` },
        preset: { conditions: [{ id: "c1", type: "price_move", params: { direction: move < 0 ? "falls" : "rises", threshold, window: 5 } }],
                  outcome: { type: "forward_return", horizon: 10 } } });
    }).catch((error) => showToast(error.message));
    return true;
  }
  return false;
}

// Lab -> Research, without losing the experiment (labState is untouched)
function openResearch(ticker, tab = null) {
  switchEnv("research");
  if (windows.has(ticker)) {
    restoreWindow(ticker);
    if (tab) showTab(windows.get(ticker), tab);
    drawAttention(windows.get(ticker).el);
  } else {
    openStock(ticker).then(() => { if (tab && windows.has(ticker)) showTab(windows.get(ticker), tab); });
  }
}


// =========================================================
// 3. Search with autocomplete (company names or tickers)
// =========================================================
let searchTimer = null;
let searchToken = 0;
let searchResults = [];
let searchIndex = -1;

const RECENT_KEY = "marketlab.recent.v1";
function recentSearches() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch (e) { return []; }
}
function rememberSearch(result) {
  const list = [result, ...recentSearches().filter((r) => r.symbol !== result.symbol)].slice(0, 6);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (e) { /* ignore */ }
}
searchInput.addEventListener("focus", () => {
  if (searchInput.value.trim()) return;
  const recent = recentSearches();
  if (!recent.length) return;
  searchResults = recent;
  searchIndex = -1;
  drawSearch(null);
});

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = searchInput.value.trim();
  if (!query) { closeSearch(); return; }
  searchTimer = setTimeout(() => runSearch(query), 140);
});

async function runSearch(query) {
  const token = ++searchToken;
  let data;
  try {
    data = await fetchJson(`/api/search?q=${encodeURIComponent(query)}`);
  } catch (error) {
    return;
  }
  if (token !== searchToken || searchInput.value.trim() !== query) return;
  searchResults = data.results;
  searchIndex = searchResults.length ? 0 : -1;
  drawSearch(query);
}

function drawSearch(query) {
  const heading = query === null ? '<div class="sr-head">Recent</div>' : "";
  if (!searchResults.length) {
    searchList.innerHTML = `<div class="sr-empty">No matches for “${escapeHtml(query)}”. Press Enter to try it as a ticker.</div>`;
  } else {
    searchList.innerHTML = heading + searchResults.map((r, i) => `
      <button class="sr ${i === searchIndex ? "on" : ""}" data-sr="${i}" role="option" aria-selected="${i === searchIndex}">
        ${logoHtml(r.symbol, r.name, 30)}
        <span class="sr-name">${escapeHtml(r.name)}</span>
        <span class="sr-meta"><b>${escapeHtml(r.symbol)}</b>${escapeHtml(r.exchange || "")}${r.type && r.type !== "EQUITY" ? ` · ${escapeHtml(r.type === "MUTUALFUND" ? "Fund" : r.type)}` : ""}</span>
      </button>`).join("");
  }
  searchList.hidden = false;
  searchForm.classList.add("open");
}

function closeSearch() {
  searchList.hidden = true;
  searchList.innerHTML = "";
  searchForm.classList.remove("open");
  searchResults = [];
  searchIndex = -1;
}

function chooseSearch(result) {
  if (result.name) rememberSearch({ symbol: result.symbol, name: result.name, exchange: result.exchange || "", type: result.type || "EQUITY" });
  searchInput.value = "";
  closeSearch();
  searchInput.blur();
  switchEnv("research");
  openStock(result.symbol, result.name);
}

searchInput.addEventListener("keydown", (event) => {
  if (searchList.hidden || !searchResults.length) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    searchIndex = (searchIndex + (event.key === "ArrowDown" ? 1 : -1) + searchResults.length) % searchResults.length;
    searchList.querySelectorAll(".sr").forEach((el, i) => { el.classList.toggle("on", i === searchIndex); el.setAttribute("aria-selected", i === searchIndex); });
    searchList.querySelector(".sr.on")?.scrollIntoView({ block: "nearest" });
  }
});

searchList.addEventListener("mousedown", (event) => {
  const row = event.target.closest("[data-sr]");
  if (!row) return;
  event.preventDefault();   // keep focus until the click lands
  chooseSearch(searchResults[Number(row.dataset.sr)]);
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (searchResults.length && searchIndex >= 0) { chooseSearch(searchResults[searchIndex]); return; }
  const raw = searchInput.value.trim();
  if (!raw) return;
  // No suggestion yet (typed fast): treat it as a ticker if it looks like one
  if (/^[A-Za-z0-9.\-^=]{1,10}$/.test(raw)) chooseSearch({ symbol: raw.toUpperCase(), name: null });
});

searchInput.addEventListener("blur", () => setTimeout(closeSearch, 120));

document.addEventListener("keydown", (event) => {
  const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (event.key === "/" && !typing) {
    event.preventDefault();
    searchInput.focus();
  }
  if (event.key === "Escape") {
    if (!popover.hidden) { closePopover(); return; }
    if (closeDrawer()) return;
    if (document.activeElement === searchInput) { closeSearch(); searchInput.blur(); }
  }
  // Cmd/Ctrl + Enter: run the experiment (from anywhere in the Lab)
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && currentEnv === "lab" && labState.mode === "quick") {
    event.preventDefault();
    runExperiment(labState);
  }
  if (event.key === "Enter" && document.activeElement.classList.contains("term")) {
    openPopover(document.activeElement);
  }
});


// =========================================================
// 4. Talking to the server
// =========================================================
async function fetchJson(url) {
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    throw new Error("Can't reach the MarketLab server. Is it still running in Terminal?");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const failure = new Error(data.error || `Request failed (${response.status}).`);
    failure.status = response.status;
    throw failure;
  }
  return data;
}

function apiUrl(ticker, endpoint = "") {
  return `/api/stock/${encodeURIComponent(ticker)}${endpoint}`;
}

// Load one part of a window's data, updating its status as it goes.
function loadPart(win, part) {
  if (win.status[part] === "loading" || win.status[part] === "ok") return win.pending[part];
  win.status[part] = "loading";
  delete win.errors[part];
  updateStatusBar(win);

  win.pending[part] = (async () => {
    try {
      win.data[part] = await fetchJson(apiUrl(win.ticker, PARTS[part].endpoint(win)));
      win.status[part] = "ok";
    } catch (error) {
      win.errors[part] = error.message;
      // "...is off. Add X to your .env" = a source the user hasn't switched on, not a failure
      win.status[part] = /\bis off\b/.test(error.message) ? "off" : "error";
    }
    updateStatusBar(win);
    refreshSlots(win, part);
  })();
  return win.pending[part];
}

// Forget a part and load it again (e.g. Risk with a new holding period)
function reloadPart(win, part) {
  win.status[part] = undefined;
  if (win.activeTab && TABS.find((t) => t.id === win.activeTab)?.needs === part) showTab(win, win.activeTab, { keepScroll: true });
  else loadPart(win, part);
}

// Re-draw any slot (inside the showing tab) that depends on this part.
function refreshSlots(win, part) {
  win.el.querySelectorAll("[data-slot]").forEach((slot) => {
    const spec = SLOTS[slot.dataset.slot];
    if (!spec || !spec.parts.includes(part)) return;
    slot.innerHTML = spec.render(win, slot);
    slot.classList.remove("fade"); void slot.offsetWidth; slot.classList.add("fade");
  });
}


// =========================================================
// 5. Opening a stock
// =========================================================
async function openStock(ticker, knownName = null) {
  ticker = ticker.toUpperCase();
  if (windows.has(ticker)) {
    restoreWindow(ticker);
    drawAttention(windows.get(ticker).el);
    return;
  }
  const saved = layout.windows[ticker];
  const win = createWindow(ticker, saved, knownName);

  // Market data first: it proves the ticker exists and gives us the name.
  await loadPart(win, "overview");
  if (win.status.overview !== "ok") {
    closeWindow(ticker, { forget: true });
    showToast(win.errors.overview);
    return;
  }
  setWindowTitle(win, win.data.overview.name);

  // The description is slower (SEC + optional AI); its slots fill in when ready.
  loadPart(win, "summary");
  showTab(win, saved?.tab || "overview");
  if (saved?.minimized) minimizeWindow(ticker);
  saveLayout();
}

function setWindowTitle(win, name) {
  win.name = name;
  win.el.querySelector(".window-title").innerHTML =
    `${logoHtml(win.ticker, name, 22)}<span class="w-ticker">${escapeHtml(win.ticker)}</span><span class="w-name">${escapeHtml(name || "")}</span>`;
}


// =========================================================
// 6. Creating a window
// =========================================================
function createWindow(ticker, saved, knownName) {
  const el = template.content.firstElementChild.cloneNode(true);
  el.dataset.ticker = ticker;     // "Explain this" buttons read the ticker from here

  const win = {
    ticker, el, chip: null, data: {}, status: {}, errors: {}, pending: {}, charts: [],
    activeTab: "overview", minimized: false, maximized: false, restoreRect: null, name: knownName,
  };
  setWindowTitle(win, knownName);

  const step = openCount++ % 8;
  const rect = saved || {
    left: 20 + step * 32,
    top: 12 + step * 26,
    width: Math.min(DEFAULT_WIDTH, workspace.clientWidth - 40),
    height: Math.min(DEFAULT_HEIGHT, workspace.clientHeight - 24),
  };
  applyRect(el, fitInsideWorkspace(rect));

  // Level-2 navigation: the six Research sections for this company
  const capsule = el.querySelector(".nav-capsule");
  for (const tab of TABS) {
    const button = document.createElement("button");
    button.className = "nav-item";
    button.dataset.tab = tab.id;
    button.innerHTML = `${icon(tab.icon, 15)}<span>${tab.label}</span>`;
    button.addEventListener("click", () => showTab(win, tab.id));
    capsule.appendChild(button);
  }

  labelMaxButton(win);
  el.querySelector(".btn-min").addEventListener("click", () => minimizeWindow(ticker));
  el.querySelector(".window-status").addEventListener("click", (event) => {
    if (event.target.closest("[data-sources]")) openSourcesPopover(win, event.target.closest("[data-sources]"));
  });
  el.querySelector(".btn-max").addEventListener("click", () => toggleMaximize(win));
  el.querySelector(".btn-close").addEventListener("click", () => closeWindow(ticker));
  el.querySelector(".window-bar").addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) toggleMaximize(win);
  });

  el.querySelector(".window-body").addEventListener("click", (event) => {
    const goto = event.target.closest("[data-goto]");
    if (goto) { showTab(win, goto.dataset.goto); return; }
    if (event.target.closest("[data-retry]")) {
      const tab = TABS.find((t) => t.id === win.activeTab);
      if (tab?.needs) win.status[tab.needs] = undefined;
      showTab(win, win.activeTab);
      return;
    }
    if (event.target.closest("[data-ask]")) { openAssistant(ticker); return; }
    const labBtn = event.target.closest("[data-lab-ticker]");
    if (labBtn) { openLab({ ticker: labBtn.dataset.labTicker }); return; }
    if (contextLabClick(win, event)) return;
    const filingsBtn = event.target.closest("[data-filings]");
    if (filingsBtn) { openFilings(win, filingsBtn); return; }
    if (chartClick(win, event)) return;
    if (win.activeTab === "news" && newsClick(win, event)) return;
    if (win.activeTab === "risk" && riskClick(win, event)) return;
  });

  el.addEventListener("pointerdown", () => bringToFront(el));
  makeDraggable(win);
  makeResizable(win);

  el.querySelector(".window-body").innerHTML = skeletonHtml();
  workspace.appendChild(el);
  bringToFront(el);

  windows.set(ticker, win);
  updateEmptyStates();
  return win;
}


// =========================================================
// 7. Tabs (level-2 navigation)
// =========================================================
async function showTab(win, tabId, { keepScroll = false } = {}) {
  const tab = TABS.find((t) => t.id === tabId) || TABS[0];
  win.activeTab = tab.id;
  win.el.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab.id));

  const body = win.el.querySelector(".window-body");
  const scroll = body.scrollTop;
  destroyCharts(win);           // charts belong to the tab being replaced
  closePopover();

  if (tab.needs && win.status[tab.needs] !== "ok") {
    body.innerHTML = skeletonHtml(tab.label);
    await loadPart(win, tab.needs);
    if (win.activeTab !== tab.id) return;       // user switched tabs meanwhile
    if (win.status[tab.needs] !== "ok") {
      body.innerHTML = `<div class="inline-error">${escapeHtml(win.errors[tab.needs] || "Couldn't load this tab.")}<br><button class="pill-btn" data-retry>Try again</button></div>`;
      return;
    }
  }

  try {
    body.innerHTML = tab.render(win);
    renumberSections(body);
  } catch (error) {
    console.error(error);
    body.innerHTML = `<div class="inline-error">Something went wrong drawing this tab. (${escapeHtml(error.message)})<br><button class="pill-btn" data-retry>Try again</button></div>`;
    return;
  }
  body.scrollTop = keepScroll ? scroll : 0;
  if (!keepScroll) { body.classList.remove("fade"); void body.offsetWidth; body.classList.add("fade"); }
  if (tab.after) tab.after(win);
  saveLayout();
  writeHash();
}

function openFilings(win, anchor) {
  const fromProfile = win.data.profile;
  const show = (filings, url) => {
    popover.innerHTML = filingsPopoverHtml(filings, url);
    popover.hidden = false;
    tooltip.hidden = true;
    placeNear(popover, anchor.getBoundingClientRect());
  };
  if (fromProfile) { show(fromProfile.filings, fromProfile.edgar_url); return; }
  loadPart(win, "filings").then(() => {
    if (win.status.filings === "ok") show(win.data.filings.filings, win.data.filings.edgar_url);
    else showToast(win.errors.filings || "Filings unavailable.");
  });
}


// =========================================================
// 8. Status bar: sources + freshness
// =========================================================
function updateStatusBar(win) {
  const bar = win.el.querySelector(".window-status");
  const chips = Object.keys(PARTS).filter((part) => win.status[part]).map((part) => {
    const state = win.status[part];
    let label = PARTS[part].label;
    if (part === "summary" && state === "ok") label = win.data.summary.method === "ai" ? "AI summary" : "Description";
    const tips = {
      loading: `${label}: loading…`,
      ok: `${label}: loaded`,
      off: win.errors[part],
      error: `${label}: ${win.errors[part]}`,
    };
    return `<span class="src-chip ${state}" data-tip="${escapeHtml(tips[state] || "")}">${escapeHtml(label)}</span>`;
  });
  const overview = win.data.overview;
  const fresh = overview
    ? `<span class="freshness" data-tip="Market data downloaded ${escapeHtml(fmtDateTime(overview.fetched_at))}">Price updated <span data-relative="${overview.fetched_at}">${fmtRelative(overview.fetched_at)}</span></span>`
    : "";
  bar.innerHTML = chips.join("") + '<button class="sources-btn" data-sources>Data sources</button>' + fresh;
}

// "Data sources": what each part of this window came from (provenance without clutter)
function openSourcesPopover(win, anchor) {
  const d = win.data;
  const row = (label, value, state) => `<div class="src-row"><span class="dot ${state || ""}"></span><b>${escapeHtml(label)}</b><span>${value}</span></div>`;
  const news = d.news ? Object.values(d.news.providers) : null;
  popover.innerHTML = `
    <h4>Data sources</h4><p class="full">Where everything in this ${escapeHtml(win.ticker)} window comes from.</p>
    ${row("Prices & market data", "Yahoo Finance (delayed). Daily history adjusted for splits and dividends, checked by MarketLab.", "ok")}
    ${row("Financial statements", d.profile ? "SEC filings (XBRL) first; Yahoo Finance fills gaps." : "Loads with the Profile tab.", d.profile ? "ok" : "")}
    ${row("Company description", d.summary ? (d.summary.method === "ai" ? "Yahoo Finance description, AI-condensed and checked against the source." : "Yahoo Finance description.") : "Loading…", d.summary ? "ok" : "")}
    ${row("Earnings & estimates", d.earnings ? "Yahoo Finance (estimates), SEC (revenue), prices for reactions." : "Loads with the Earnings tab.", d.earnings ? "ok" : "")}
    ${row("Logos", "Financial Modeling Prep public logo images.", "ok")}
    ${news ? `<div class="label" style="margin-top:12px">News sources</div>${news.map((p) => row(p.label, p.ok ? `${p.count} items` : escapeHtml(p.error || "unavailable"), p.ok ? "ok" : p.configured ? "err" : "")).join("")}`
           : row("News", "Loads with the News tab (many sources).", "")}`;
  popover.hidden = false;
  tooltip.hidden = true;
  placeNear(popover, anchor.getBoundingClientRect());
}

setInterval(() => {
  document.querySelectorAll("[data-relative]").forEach((el) => { el.textContent = fmtRelative(el.dataset.relative); });
}, 30_000);


// =========================================================
// 9. Moving, resizing, maximizing
// =========================================================
function trackPointer(handle, startEvent, onMove, onEnd) {
  const startX = startEvent.clientX;
  const startY = startEvent.clientY;
  handle.setPointerCapture(startEvent.pointerId);
  let frame = null, last = null;
  function move(event) {
    last = event;
    if (frame) return;                      // at most one layout update per animation frame
    frame = requestAnimationFrame(() => { frame = null; onMove(last.clientX - startX, last.clientY - startY); });
  }
  function end() {
    if (frame) cancelAnimationFrame(frame);
    if (last) onMove(last.clientX - startX, last.clientY - startY);
    handle.removeEventListener("pointermove", move);
    handle.removeEventListener("pointerup", end);
    handle.removeEventListener("pointercancel", end);
    onEnd();
  }
  handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
}

function makeDraggable(win) {
  const { el } = win;
  const bar = el.querySelector(".window-bar");
  bar.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button") || win.maximized) return;
    const startLeft = el.offsetLeft;
    const startTop = el.offsetTop;
    el.classList.add("dragging");
    trackPointer(bar, event,
      (dx, dy) => {
        const maxLeft = Math.max(0, workspace.clientWidth - el.offsetWidth);
        const maxTop = Math.max(0, workspace.clientHeight - 48);
        el.style.left = `${clamp(startLeft + dx, 0, maxLeft)}px`;
        el.style.top = `${clamp(startTop + dy, 0, maxTop)}px`;
      },
      () => { el.classList.remove("dragging"); saveLayout(); }
    );
  });
}

function makeResizable(win) {
  const { el } = win;
  el.querySelectorAll(".resize-handle").forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      bringToFront(el);
      const direction = handle.dataset.dir;
      const startWidth = el.offsetWidth;
      const startHeight = el.offsetHeight;
      el.classList.add("resizing");
      trackPointer(handle, event,
        (dx, dy) => {
          const maxWidth = Math.max(MIN_WIDTH, workspace.clientWidth - el.offsetLeft);
          const maxHeight = Math.max(MIN_HEIGHT, workspace.clientHeight - el.offsetTop);
          if (direction.includes("e")) el.style.width = `${clamp(startWidth + dx, MIN_WIDTH, maxWidth)}px`;
          if (direction.includes("s")) el.style.height = `${clamp(startHeight + dy, MIN_HEIGHT, maxHeight)}px`;
        },
        () => { el.classList.remove("resizing"); saveLayout(); }
      );
    });
  });
}

function toggleMaximize(win) {
  if (win.maximized) {
    win.maximized = false;
    win.el.classList.remove("maximized");
    applyRect(win.el, fitInsideWorkspace(win.restoreRect));
  } else {
    win.restoreRect = currentRect(win.el);
    win.maximized = true;
    win.el.classList.add("maximized");
    applyRect(win.el, { left: 0, top: 0, width: workspace.clientWidth, height: workspace.clientHeight });
  }
  labelMaxButton(win);
  bringToFront(win.el);
  saveLayout();
}

function labelMaxButton(win) {
  const button = win.el.querySelector(".btn-max");
  const label = win.maximized ? "Restore" : "Maximize";
  button.dataset.tip = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = win.maximized
    ? '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M7 2.5V5h2.5M5 9.5V7H2.5"/></svg>'
    : '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M4 8L8 4M5 3.5h3.5V7"/></svg>';
  if (lastTipTarget === button && !tooltip.hidden) tooltip.textContent = label;
}

function refitWindows() {
  if (!workspace.clientWidth) return;     // Research hidden: fit windows when it's shown again
  for (const win of windows.values()) {
    if (win.maximized) applyRect(win.el, { left: 0, top: 0, width: workspace.clientWidth, height: workspace.clientHeight });
    else applyRect(win.el, fitInsideWorkspace(currentRect(win.el)));
  }
}

let resizeFrame = null;
window.addEventListener("resize", () => {
  if (resizeFrame) return;
  resizeFrame = requestAnimationFrame(() => { resizeFrame = null; refitWindows(); });
});

function applyRect(el, rect) {
  el.style.left = `${rect.left}px`;
  el.style.top = `${rect.top}px`;
  el.style.width = `${rect.width}px`;
  el.style.height = `${rect.height}px`;
}

function fitInsideWorkspace(rect) {
  const areaW = workspace.clientWidth || window.innerWidth - 36;
  const areaH = workspace.clientHeight || window.innerHeight - 120;
  const width = clamp(rect.width, Math.min(MIN_WIDTH, areaW), Math.max(MIN_WIDTH, areaW));
  const height = clamp(rect.height, Math.min(MIN_HEIGHT, areaH), Math.max(MIN_HEIGHT, areaH));
  return {
    width: Math.min(width, areaW), height: Math.min(height, areaH),
    left: clamp(rect.left, 0, Math.max(0, areaW - width)),
    top: clamp(rect.top, 0, Math.max(0, areaH - 48)),
  };
}

function bringToFront(el) {
  if (el.classList.contains("focused")) return;
  el.style.zIndex = ++topZ;
  document.querySelectorAll(".window.focused").forEach((w) => w.classList.remove("focused"));
  el.classList.add("focused");
  writeHash();
}

// The address bar mirrors where you are (#research/NVDA/risk, #lab, #notebook/e_…),
// so a refresh or a bookmark returns to the same place. Experiment definitions are
// kept in local storage, not in the URL.
function writeHash() {
  let hash = currentEnv || "research";
  if (currentEnv === "research") {
    const focused = document.querySelector(".window.focused:not([hidden])");
    const win = focused && windows.get(focused.dataset.ticker);
    if (win) hash += `/${win.ticker}/${win.activeTab}`;
  }
  if (currentEnv === "notebook" && typeof notebookUi !== "undefined" && notebookUi.selected) hash += `/${notebookUi.selected}`;
  if (currentEnv === "lab" && labState.mode === "projects") hash += RP.view === "project" && RP.project ? `/projects/${RP.project.id}` : "/projects";
  if (currentEnv === "lab" && ["quick", "trades"].includes(labState.mode)) hash += `/${labState.mode}`;
  if (location.hash.slice(1) !== hash) history.replaceState(null, "", `#${hash}`);
}

function readHash() {
  let [env, a, b] = location.hash.slice(1).split("/");
  if (env === "lab" && a === "invest") { env = "invest"; a = null; }
  return { env: ENVS.includes(env) ? env : null, ticker: a ? a.toUpperCase() : null, tab: b || null, entry: env === "notebook" ? a : null };
}


// =========================================================
// 10. Minimize (horizontal dock), restore, close
// =========================================================
function minimizeWindow(ticker) {
  const win = windows.get(ticker);
  if (!win || win.minimized) return;
  win.minimized = true;
  win.el.hidden = true;
  win.chip = createDockChip(win);
  dock.appendChild(win.chip);
  updateEmptyStates();
  saveLayout();
}

function restoreWindow(ticker) {
  const win = windows.get(ticker);
  if (!win) return;
  if (win.minimized) {
    win.minimized = false;
    win.chip.remove();
    win.chip = null;
    win.el.hidden = false;
  }
  bringToFront(win.el);
  updateEmptyStates();
  saveLayout();
}

function closeWindow(ticker, { forget = false } = {}) {
  const win = windows.get(ticker);
  if (!win) return;
  destroyCharts(win);
  windows.delete(ticker);
  if (win.chip) win.chip.remove();
  win.el.remove();
  if (forget) delete layout.windows[ticker];
  closePopover();
  updateEmptyStates();
  saveLayout();
}

function createDockChip(win) {
  const data = win.data.overview;
  const chip = document.createElement("button");
  chip.className = "dock-chip";
  chip.dataset.tip = `${data ? data.name : win.ticker}: click to restore`;
  const pct = data && isNum(data.change_pct) ? `<span class="${signClass(data.change)}">${fmtPct(data.change_pct / 100, true, 2)}</span>` : "";
  chip.innerHTML = `${logoHtml(win.ticker, data?.name, 20)}<b>${escapeHtml(win.ticker)}</b>${pct}`;
  chip.addEventListener("click", () => restoreWindow(win.ticker));
  return chip;
}


// =========================================================
// 11. Tooltips (hover) and glossary popovers (click)
// =========================================================
// Tooltips appear after a short pause (like native ones), never move layout, and
// show immediately when you move from one tooltip to the next ("warm" for 600 ms).
const TIP_DELAY = 480, TERM_DELAY = 300, WARM_MS = 600;
let lastTipTarget = null, tipTimer = null, tipWarmUntil = 0;

function hideTooltip() {
  clearTimeout(tipTimer);
  if (!tooltip.hidden) { tooltip.hidden = true; tipWarmUntil = performance.now() + WARM_MS; }
  lastTipTarget = null;
}

document.addEventListener("mouseover", (event) => {
  const termEl = event.target.closest(".term[data-term]");
  const richEl = event.target.closest("[data-tiphtml]");
  const tipEl = event.target.closest("[data-tip]");
  const target = termEl || richEl || tipEl;
  if (!popover.hidden && popover.contains(event.target)) return;
  if (target === lastTipTarget) return;       // still over the same element: nothing to redraw
  hideTooltip();
  lastTipTarget = target;
  if (!target) return;

  let fill, rich = false;
  if (termEl && GLOSSARY[termEl.dataset.term]) {
    const entry = GLOSSARY[termEl.dataset.term];
    fill = `<b>${escapeHtml(entry.name)}</b>${escapeHtml(entry.short)}<span class="hint">Click for more</span>`;
    rich = true;
  } else if (richEl) {
    fill = richEl.dataset.tiphtml;          // built by MarketLab from numbers and dates only
    rich = true;
  } else if (tipEl && tipEl.dataset.tip) {
    fill = tipEl.dataset.tip;
  } else {
    return;
  }
  const show = () => {
    if (lastTipTarget !== target || !target.isConnected) return;
    if (rich) tooltip.innerHTML = fill; else tooltip.textContent = fill;
    tooltip.classList.toggle("rich", rich);
    tooltip.hidden = false;
    placeNear(tooltip, target.getBoundingClientRect());
  };
  const delay = performance.now() < tipWarmUntil || richEl ? 0 : termEl ? TERM_DELAY : TIP_DELAY;
  if (delay) tipTimer = setTimeout(show, delay); else show();
});
document.addEventListener("pointerdown", hideTooltip);
// Only touch the DOM if a tooltip is actually showing (writes during scroll force extra style work).
document.addEventListener("scroll", () => { if (!tooltip.hidden || tipTimer) hideTooltip(); }, { capture: true, passive: true });

function placeNear(box, anchor) {
  const left = clamp(anchor.left, 10, window.innerWidth - box.offsetWidth - 10);
  const below = anchor.bottom + 8;
  const top = below + box.offsetHeight > window.innerHeight - 10 ? anchor.top - box.offsetHeight - 8 : below;
  box.style.left = `${left}px`;
  box.style.top = `${Math.max(10, top)}px`;
}

function openPopover(termEl) {
  const entry = GLOSSARY[termEl.dataset.term];
  if (!entry) return;
  const ticker = termEl.closest(".window")?.dataset.ticker;
  const label = termEl.firstChild.textContent.trim();
  const plain = termEl.dataset.plain;
  popover.innerHTML = `
    <h4>${escapeHtml(entry.term)}</h4>
    ${entry.name !== entry.term ? `<div class="full">${escapeHtml(entry.name)}</div>` : ""}
    <p>${escapeHtml(entry.short)}</p>
    ${plain && ticker ? `<p class="for-company"><b>${escapeHtml(ticker)}:</b> ${escapeHtml(plain)}</p>` : ""}
    ${entry.why ? `<p><span class="why-label">Why this matters.</span> ${escapeHtml(entry.why)}</p>` : ""}
    ${ticker ? `<button class="explain-btn" data-explain-label="${escapeHtml(label)}" data-explain-value="${escapeHtml(termEl.dataset.value || "")}"
                 data-explain-section="${escapeHtml(termEl.dataset.section || "")}" data-ticker="${escapeHtml(ticker)}">${icon("ask", 14)} Explain ${escapeHtml(ticker)}'s ${escapeHtml(label)}</button>` : ""}`;
  popover.hidden = false;
  tooltip.hidden = true;
  placeNear(popover, termEl.getBoundingClientRect());
}

function closePopover() { popover.hidden = true; }

// One side drawer for detail views (episode inspector, methodology...). Esc closes it.
const drawer = document.getElementById("drawer");
function openDrawer(html, { label = "Details" } = {}) {
  drawer.innerHTML = `<div class="drawer-head"><span class="label">${escapeHtml(label)}</span>
    <button class="icon-btn" data-close-drawer aria-label="Close">${icon("close", 15)}</button></div><div class="drawer-body">${html}</div>`;
  drawer.hidden = false;
  requestAnimationFrame(() => drawer.classList.add("open"));
  drawer.querySelector("[data-close-drawer]").focus();
}
function closeDrawer() {
  if (drawer.hidden) return false;
  drawer.classList.remove("open");
  setTimeout(() => { if (!drawer.classList.contains("open")) drawer.hidden = true; }, 260);
  return true;
}
drawer.addEventListener("click", (event) => { if (event.target.closest("[data-close-drawer]")) closeDrawer(); });

document.addEventListener("click", (event) => {
  const termEl = event.target.closest(".term[data-term]");
  if (termEl && !popover.contains(termEl)) {
    event.preventDefault();
    event.stopPropagation();
    openPopover(termEl);
    return;
  }
  const explain = event.target.closest(".explain-btn");
  if (explain) {
    event.preventDefault();
    const ticker = explain.dataset.ticker || explain.closest(".window")?.dataset.ticker;
    if (ticker) {
      const focus = { label: explain.dataset.explainLabel, value: explain.dataset.explainValue, section: explain.dataset.explainSection };
      closePopover();
      openAssistant(ticker, { question: `Explain ${ticker}'s ${focus.label} to me.`, focus });
    }
    return;
  }
  if (!popover.hidden && !popover.contains(event.target) && !event.target.closest("[data-filings],[data-sources],[data-news-sources]")) closePopover();
}, true);

emptyState.addEventListener("click", (event) => {
  const open = event.target.closest("[data-open]");
  const labButton = event.target.closest("[data-lab]");
  if (open) openStock(open.dataset.open);
  if (labButton) openLab({ ticker: labButton.dataset.lab });
  if (event.target.closest("[data-home-search]")) { const i = document.getElementById("search-input"); i.focus(); i.select(); }
  const home = event.target.closest("[data-home-go]");
  if (home) { if (home.dataset.homeGo === "invest") switchEnv("invest"); else goLab("idea"); }
  const basket = event.target.closest("[data-home-basket]");
  if (basket) { switchEnv("invest"); investStartBasket(basket.dataset.homeBasket.split(",")); }
});


// =========================================================
// 12. Small helpers
// =========================================================
function clamp(value, min, max) { return Math.min(Math.max(value, min), max); }

function skeletonHtml(label = "") {
  return `<div class="skeleton">${label ? `<span class="label">Loading ${escapeHtml(label)}…</span>` : ""}<div class="sk big"></div><div class="sk"></div><div class="sk" style="width:70%"></div><div class="sk" style="width:85%"></div></div>`;
}

function drawAttention(el) {
  el.classList.add("attention");
  setTimeout(() => el.classList.remove("attention"), 700);
}

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  toasts.appendChild(toast);
  setTimeout(() => toast.remove(), 4500);
}

function updateEmptyStates() {
  emptyState.hidden = [...windows.values()].some((w) => !w.minimized);
  dock.hidden = dock.children.length === 0;
}

function tick() {
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}
tick();
setInterval(tick, 30_000);

function refreshStatusPills() {
  return fetchJson("/api/status").then((status) => {
    const sec = document.getElementById("pill-sec");
    const ai = document.getElementById("pill-ai");
    sec.classList.toggle("on", status.sec);
    ai.classList.toggle("on", status.ai);
    sec.dataset.tip = status.sec ? "SEC EDGAR is on: official filings and filed financials."
                                 : "SEC EDGAR is off. Add SEC_USER_AGENT (your name and email) to the .env file, then restart.";
    ai.dataset.tip = status.ai ? `Ask MarketLab is on: ${status.ai_label}.${status.ai_summaries ? " AI company summaries are on." : ""}`
                               : "Ask MarketLab isn't connected. Open Ask MarketLab and follow Connect Claude.";
  }).catch(() => {});
}
refreshStatusPills();


// =========================================================
// 13. Start-up: reopen whatever was open before a page refresh
// =========================================================
document.getElementById("brand-mark").innerHTML = icon("mark", 26);
document.querySelectorAll("[data-icon]").forEach((el) => { el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon, Number(el.dataset.size) || 17)); });
const reopen = layout.open.filter((key) => key !== "@LAB");   // read before switchEnv saves the (still empty) layout
const fromHash = readHash();
if (fromHash.entry && typeof notebookUi !== "undefined") notebookUi.selected = fromHash.entry;
switchEnv(fromHash.env || (["lab", "notebook", "replay"].includes(layout.env) ? layout.env : "research"));
if (fromHash.ticker && fromHash.env === "research" && !reopen.includes(fromHash.ticker)) reopen.push(fromHash.ticker);
reopen.forEach((key) => openStock(key).then(() => {
  if (key === fromHash.ticker && windows.has(key)) {
    const win = windows.get(key);
    restoreWindow(key);
    if (fromHash.tab && TABS.some((t) => t.id === fromHash.tab) && win.activeTab !== fromHash.tab) showTab(win, fromHash.tab);
  }
}));
updateEmptyStates();
