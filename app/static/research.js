// =========================================================
// research.js — Research Projects (the Lab's second workflow).
//
// Quick Test (lab.js) = one structured sentence, tested immediately.
// Research Projects  = a living notebook for complex, mostly intraday ideas:
//   observation (kept verbatim) → formalize (terms that need definitions)
//   → definitions (explicit, from the Concept Library) → hypothesis (conditions)
//   → entry / stop / exit ideas to compare → questions, examples, notes, versions.
//
// Everything here is plain data saved by research_projects.py. No AI needed.
// Testing switches on once intraday data is connected (Phase 2+).
// =========================================================

const RP = {
  meta: null,          // /api/research/library (concepts, sessions, kinds…)
  projects: null,      // list for the home view
  project: null,       // the open project
  view: "home",        // home | new | project
  openId: null,        // project id from the URL on start-up
  editor: null,        // definition being created/edited
  ideaDraft: { entries: { kind: "validation_close" }, exits: { kind: "r_multiple" }, stops: { kind: "fvg_invalidation" } },
  revising: false, confirmDelete: false, ruleFor: null,
};

const RP_OPTION_LABELS = {
  close_beyond: "Close back beyond the gap", hold_midpoint: "Midpoint holds, then a close beyond", reaction_candle: "Reaction candle out of the gap",
  close_beyond_far: "Close beyond the far edge", trade_beyond_far: "Trade beyond the far edge", close_beyond_mid: "Close beyond the midpoint",
  globex: "Full Globex day (18:00–17:00 ET)", rth: "Regular hours (09:30–16:00 ET)",
  only_event_days: "Only event days", exclude_event_days: "Exclude event days", compare: "Compare event vs. ordinary days",
  "sell-side": "Sell-side (below lows)", "buy-side": "Buy-side (above highs)", both: "Both (direction from side)", close: "Candle close", wick: "Any trade (wick)",
  bullish: "Bullish", bearish: "Bearish", either: "Either", points: "points", ticks: "ticks", percent: "% of price", ATR: "× ATR",
};
const RP_FLOW = ["Observe", "Write the idea", "Define the terms", "Formalize", "Test", "Inspect examples", "Change the rules", "Test again", "Challenge", "Save"];

// Where the Lab starts: #lab = Describe an idea; #lab/invest, #lab/trades, #lab/quick, #lab/projects[/<id>].
function initialLabMode() {
  const [env, mode, id] = location.hash.slice(1).split("/");
  if (env !== "lab") return "idea";
  if (mode === "projects") { RP.openId = id || null; return "projects"; }
  if (["quick", "trades"].includes(mode)) return mode;
  return "idea";
}

function labModeBar(mode) {
  const on = (id) => mode === id;
  const tab = (id, label, sub) => `<button role="tab" class="lab-mode${on(id) ? " on" : ""}" aria-selected="${on(id)}" data-lab-mode="${id}">
      <span>${label}</span><small>${sub}</small></button>`;
  return `<div class="lab-modes-row"><div class="lab-modes" role="tablist" aria-label="Lab">
    ${tab("idea", "Test an idea", "Paragraph → results")}${tab("trades", "Log a trade", "Was it actually good?")}
    ${tab("quick", "Quick test", "Swing questions")}${tab("projects", "Advanced", "Full strategy editor")}</div></div>`;
}

document.addEventListener("click", (event) => {
  const modeBtn = event.target.closest("[data-lab-mode]");
  if (!modeBtn || typeof labState === "undefined") return;
  if (labState.mode === modeBtn.dataset.labMode) return;
  labState.mode = modeBtn.dataset.labMode;
  if (labState.mode === "projects") { RP.view = RP.project ? "project" : "home"; renderResearch(labState); }
  else if (["idea", "trades"].includes(labState.mode)) renderIdeaLab(labState);
  else { renderLab(labState); labState.rendered = true; }
  writeHash();
});

async function rpMeta() {
  if (!RP.meta) RP.meta = await fetchJson("/api/research/library");
  return RP.meta;
}

async function rpCall(url, body, method = "POST") {
  try {
    const data = await postJson(url, body, method);
    if (data && data.id && data.terms) RP.project = data;
    return data;
  } catch (error) {
    showToast(error.message);
    return null;
  }
}

const rpUrl = (path = "") => `/api/research/projects/${RP.project.id}${path}`;
const conceptOf = (id) => RP.meta.concepts.find((c) => c.id === id);
const tfLabel = (tf) => (RP.meta?.timeframe_labels || {})[tf] || tf;
const optLabel = (value) => RP_OPTION_LABELS[value] || tfLabel(value) || String(value);


// =========================================================
// Rendering
// =========================================================
async function renderResearch(lab) {
  const body = lab.el.querySelector(".window-body");
  if (!lab.rpBound) {
    body.addEventListener("click", rpClick);
    body.addEventListener("change", rpChange);
    body.addEventListener("input", rpInput);
    body.addEventListener("keydown", rpKey);
    lab.rpBound = true;
  }
  body.innerHTML = `${labModeBar("projects")}<div class="rp-loading">Loading research projects…</div>`;
  try {
    await rpMeta();
    if (RP.openId) {
      const id = RP.openId;
      RP.openId = null;
      RP.project = await fetchJson(`/api/research/projects/${encodeURIComponent(id)}`).catch(() => null);
      RP.view = RP.project ? "project" : "home";
      if (RP.project) setTimeout(loadDataset, 0);
    }
    if (RP.view === "home") RP.projects = (await fetchJson("/api/research/projects")).projects;
  } catch (error) {
    body.innerHTML = `${labModeBar("projects")}<div class="rp-empty">${escapeHtml(error.message)}</div>`;
    return;
  }
  drawResearch();
}

function drawResearch({ keepScroll = true } = {}) {
  if (labState.mode !== "projects") return;
  const body = labState.el.querySelector(".window-body");
  const scroll = body.scrollTop;
  const view = RP.view === "project" && RP.project ? projectHtml(RP.project) : RP.view === "new" ? newProjectHtml()
    : RP.view === "new-quick" ? newQuickHtml() : homeHtml();
  body.innerHTML = labModeBar("projects") + view;
  if (keepScroll) body.scrollTop = scroll;
  if (RP.editor) loadExplanation();
  writeHash();
}

function flowHtml(reached) {
  return `<ol class="rp-flow" aria-label="The research method">${RP_FLOW.map((step, i) =>
    `<li class="${i < reached ? "done" : i >= 4 ? "later" : ""}">${escapeHtml(step)}</li>`).join("")}</ol>`;
}

function homeHtml() {
  const projects = RP.projects || [];
  const cards = projects.map((p) => `
    <button class="rp-card glass" data-rp-open="${escapeHtml(p.id)}">
      <span class="rp-card-top">${statusChip(p.status)}<b>${escapeHtml(p.instrument.symbol)}</b>${p.kind === "trade_check" ? `<span class="rp-fork-tag">luck check</span>` : ""}${p.timeframes.primary ? `<span>${escapeHtml(tfLabel(p.timeframes.primary))}${p.timeframes.execution.length ? " → " + p.timeframes.execution.map(escapeHtml).join(" / ") : ""}</span>` : ""}
        ${p.parent_project_id ? `<span class="rp-fork-tag">${icon("fork", 12)} fork</span>` : ""}</span>
      <span class="rp-card-name">${escapeHtml(p.name)}</span>
      <span class="rp-card-obs">${p.observation ? `“${escapeHtml(p.observation)}${p.observation.length >= 280 ? "…" : ""}”` : "No observation written yet"}</span>
      <span class="rp-card-meta">${p.mode === "quick" ? `<b>${p.tests || 0} test${p.tests === 1 ? "" : "s"}</b>` : p.open_terms ? `<b class="warn">${p.open_terms} undefined term${p.open_terms === 1 ? "" : "s"}</b>` : `<b class="ok">All terms resolved</b>`}
        · ${p.counts.definitions} definitions · ${p.counts.questions} questions · ${p.counts.examples} examples · ${p.counts.versions} versions
        <span class="rp-when">${escapeHtml(fmtDateTime(p.updated_at))}</span></span>
    </button>`).join("");
  return `
    <div class="lab-head">
      <div class="lab-kicker">${icon("lab", 18)} Lab · Research projects</div>
      <h1>Research projects</h1>
      <div class="aside">I think I see something in the market. What exactly do I mean, and does it survive testing?</div>
    </div>
    <div class="rp-starts">
      <button class="rp-start glass primary" data-rp-new-quick><b>${icon("plus", 15)}Describe a trade</b><span>Write how you'd take it, in your own words. MarketLab turns it into exact rules, asks only what matters, and tests it.</span></button>
      <button class="rp-start glass" data-rp-new><b>${icon("method", 15)}Define it yourself</b><span>Start from an observation and define every concept and rule by hand.</span></button>
      <button class="rp-start glass" data-rp-new-luck><b>${icon("challenge", 15)}Was my trade just luck?</b><span>Describe a trade you took and compare its result with every historical setup that matches the same rules.</span></button>
    </div>
    <div class="rp-actions">
      ${Object.entries(RP.meta.templates).map(([id, t]) => `<button class="pill-btn" data-rp-template="${id}">Start from an example: ${escapeHtml(t.name)}</button>`).join("")}
      <span class="rp-spacer"></span>
      <button class="link-btn" data-dmt-my-defs>${icon("save", 14)}My definitions</button>
      <button class="link-btn" data-rp-library>${icon("method", 14)}Concept library</button>
      <button class="link-btn" data-rp-data>${icon("data", 14)}Data status</button>
    </div>
    ${projects.length ? `<h4 class="rp-h4">Recent projects</h4><div class="rp-grid">${cards}</div>` : `
      <div class="rp-empty glass">
        <h3>No research projects yet</h3>
        <p>Start with something you think you've noticed, in your own words. MarketLab then helps you pin down every term until a computer could test it without hindsight.</p>
        <p class="rp-fine">Quick Test stays the fastest way to check simple ideas like “AAPL falls 5% in a week”.</p>
      </div>`}`;
}

function newProjectHtml() {
  return `
    <nav class="rp-crumbs"><button class="link-btn" data-rp-home>Research projects</button><span>›</span><span>New project</span></nav>
    <div class="rp-new glass">
      <label class="rp-big-label" for="rp-obs">What do you think is happening?</label>
      <textarea id="rp-obs" class="rp-obs-input" data-rp-obs rows="6" maxlength="8000"
        placeholder="Describe something you think you've noticed in the market..."></textarea>
      <p class="rp-fine">Saved exactly as you write it. MarketLab never rewrites it; later wordings are added beside it.</p>
      <div class="rp-detect" data-rp-detect></div>
      <div class="rp-new-row">
        <label>Project name<input data-rp-name maxlength="120" placeholder="e.g. NQ NY Open Reversal"></label>
        <label>Instrument<select data-rp-inst>${Object.entries(RP.meta.instruments).map(([s, i]) =>
          `<option value="${s}" ${i.available ? "" : "disabled"} ${s === "NQ" ? "selected" : ""}>${s} · ${escapeHtml(i.label)}${i.available ? "" : " (later)"}</option>`).join("")}</select></label>
      </div>
      <div class="rp-new-foot">
        <button class="run-btn" data-rp-create>Create project <span class="arrow">→</span></button>
        <button class="link-btn" data-rp-home>Cancel</button>
      </div>
    </div>`;
}

function detectHtml(analysis) {
  if (!analysis) return "";
  const concepts = analysis.terms.filter((t) => t.kind !== "rule");
  const missing = analysis.terms.filter((t) => t.kind === "rule" && !t.mentioned);
  return `
    <div class="rp-detect-row"><span class="label">Instrument</span> ${analysis.instrument ? `<b>${escapeHtml(analysis.instrument)}</b>` : "<i>not named</i>"}
      <span class="label">Timeframes</span> ${analysis.timeframes.mentioned.length ? analysis.timeframes.mentioned.map((tf) => `<b>${escapeHtml(tfLabel(tf))}</b>`).join(" ") : "<i>none named</i>"}</div>
    ${concepts.length ? `<div class="rp-detect-row"><span class="label">Will need a definition</span>${concepts.map((t) => `<span class="rp-chip open">○ ${escapeHtml(t.label)}</span>`).join("")}</div>` : ""}
    ${missing.length ? `<div class="rp-detect-row"><span class="label">Not mentioned yet</span>${missing.map((t) => `<span class="rp-chip muted">${escapeHtml(t.label)}</span>`).join("")}</div>` : ""}`;
}

// ---------- Project page ----------
function rpSection(idx, title, aside, inner, id) {
  return `<section class="rp-section" ${id ? `id="rp-${id}"` : ""}>
    <div class="section-head"><span class="idx">${idx}</span><h3>${title}</h3>${aside ? `<span class="aside">${aside}</span>` : ""}</div>
    ${inner}</section>`;
}

function projectHtml(p) {
  const defined = p.terms.filter((t) => t.status !== "open").length;
  const reached = !p.observation.original ? 1 : defined < p.terms.length ? 3 : 4;
  return `
    <nav class="rp-crumbs"><button class="link-btn" data-rp-home>Research projects</button><span>›</span><span>${escapeHtml(p.name)}</span></nav>
    <header class="rp-head">
      <input class="rp-title" data-rp-field="name" value="${escapeHtml(p.name)}" maxlength="120" aria-label="Project name">
      <div class="rp-head-meta">
        <label>Instrument <select data-rp-field="instrument">${Object.entries(RP.meta.instruments).map(([s, i]) =>
          `<option value="${s}" ${p.instrument.symbol === s ? "selected" : ""} ${i.available ? "" : "disabled"}>${s}</option>`).join("")}</select></label>
        ${rolesHtml(p)}
      </div>
      <div class="rp-head-actions">
        ${statusChip(p.status)}
        <span class="rp-fine" data-tip="Distinct rule sets tested · variations saved">${(p.test_counts || {}).rule_sets || 0} tested · ${(p.test_counts || {}).variations || 0} variations</span>
        <button class="small-btn" data-rp-jump="versions">${icon("save", 13)}Save version</button>
        <button class="small-btn" data-rp-duplicate>${icon("duplicate", 13)}Duplicate</button>
        <button class="small-btn" data-rp-fork>${icon("fork", 13)}Fork project</button>
        <button class="small-btn ${RP.confirmDelete ? "danger" : ""}" data-rp-delete>${icon("trash", 13)}${RP.confirmDelete ? "Click again to delete" : "Delete"}</button>
        ${p.parent_project_id ? `<button class="link-btn" data-rp-open="${escapeHtml(p.parent_project_id)}">${icon("fork", 12)} Forked from parent</button>` : ""}
      </div>
    </header>
    ${modeToggleHtml(p)}
    ${p.mode === "quick" ? quickSections(p) : manualSections(p, reached)}`;
}

function quickSections(p) {
  const n = (i) => String(i).padStart(2, "0");
  const parts = [
    ["Describe my trade", p.kind === "trade_check" ? "Your trade, and the reasoning behind it" : "In your own words; MarketLab structures it", describeSectionHtml(p), "describe"],
    ["Dataset", "Exactly which data a test uses", `<div data-rp-dataset>${datasetHtml(p)}</div>`, "dataset"],
    ["Test", "Every setup that matches the rules", experimentsHtml(p), "experiments"],
    ["Definitions", "The exact concepts in use (edit any of them)", definitionsHtml(p), "definitions"],
    ["Conditions", "The sequence the engine checks", hypothesisHtml(p), "hypothesis"],
    ["Entries, stops and exits", "Alternatives to compare", ideasHtml(p), "ideas"],
    ["Trading assumptions", "Session, holding time, contract, costs", settingsHtml(p), "settings"],
    ["Open questions", "", questionsHtml(p), "questions"],
    ["Examples", "Successes, failures and the unclear ones", examplesHtml(p), "examples"],
    ["Notes", "", notesHtml(p), "notes"],
    ["Strategy versions", "Every change and variation is kept", versionsHtml(p), "versions"],
  ];
  return parts.map(([title, aside, inner, id], i) => rpSection(n(i + 1), title, aside, inner, id)).join("");
}

function manualSections(p, reached) {
  const defined = p.terms.filter((t) => t.status !== "open").length;
  return `
    ${flowHtml(reached)}
    ${rpSection("01", "Original observation", "In your words. Never changed.", observationHtml(p), "observation")}
    ${rpSection("02", "Formalize this idea", `${defined} of ${p.terms.length} resolved`, formalizeHtml(p), "formalize")}
    ${rpSection("03", "Definitions", "Exactly what MarketLab will look for", definitionsHtml(p), "definitions")}
    ${rpSection("04", "Current hypothesis", "Conditions built from your definitions", hypothesisHtml(p), "hypothesis")}
    ${rpSection("05", "Trade ideas to compare", "Alternatives, not one answer", ideasHtml(p), "ideas")}
    ${rpSection("06", "Trading assumptions", "Session, holding time, contract, costs", settingsHtml(p), "settings")}
    ${rpSection("07", "Dataset", "Exactly which data a test uses", `<div data-rp-dataset>${datasetHtml(p)}</div>`, "dataset")}
    ${rpSection("08", "Experiments", "Every setup that matches your exact rules", experimentsHtml(p), "experiments")}
    ${rpSection("09", "Open questions", "Write them down now; test them later", questionsHtml(p), "questions")}
    ${rpSection("10", "Examples", "Successes, failures and the unclear ones", examplesHtml(p), "examples")}
    ${rpSection("11", "Notes", "", notesHtml(p), "notes")}
    ${rpSection("12", "Strategy versions", "Every change is kept", versionsHtml(p), "versions")}`;
}

function observationHtml(p) {
  const o = p.observation;
  return `
    ${o.original ? `<blockquote class="rp-quote">${escapeHtml(o.original)}<footer>Saved ${escapeHtml(fmtDateTime(o.saved_at))} · kept exactly as written</footer></blockquote>`
      : `<p class="rp-fine">No observation yet.</p>`}
    ${o.revisions.map((r, i) => `<blockquote class="rp-quote rev">${escapeHtml(r.text)}<footer>Revised wording ${i + 1} · ${escapeHtml(fmtDateTime(r.saved_at))}</footer></blockquote>`).join("")}
    ${RP.revising ? `<div class="rp-inline-form"><textarea data-rp-revision rows="3" maxlength="8000" placeholder="${o.original ? "A clearer or narrower wording. The original stays above." : "What do you think is happening?"}"></textarea>
        <button class="pill-btn" data-rp-save-revision>${o.original ? "Add revised wording" : "Save observation"}</button><button class="link-btn" data-rp-cancel-revision>Cancel</button></div>`
      : `<button class="link-btn" data-rp-revise>${icon("plus", 13)}${o.original ? "Add a revised wording" : "Write the observation"}</button>`}`;
}

function formalizeHtml(p) {
  const t = p.timeframes;
  const rows = p.terms.map((term) => {
    const done = term.status !== "open";
    const def = term.definition_id && p.definitions.find((d) => d.id === term.definition_id);
    let actions = "";
    if (term.status === "open") {
      if (term.kind === "concept") {
        const sameConcept = p.definitions.filter((d) => d.concept === term.concept);
        actions = `<button class="pill-btn" data-rp-define="${escapeHtml(term.key)}">Define</button>
          ${sameConcept.length ? `<select data-rp-link-term="${escapeHtml(term.key)}"><option value="">Use an existing definition…</option>${sameConcept.map((d) =>
            `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join("")}</select>` : ""}`;
      } else if (["entry", "invalidation", "target"].includes(term.key)) {
        actions = `<button class="pill-btn" data-rp-jump="ideas">Add ${term.key === "entry" ? "entry" : term.key === "target" ? "exit" : "stop"} ideas</button>
          <button class="link-btn" data-rp-rule="${escapeHtml(term.key)}">or write the rule</button>`;
      } else {
        actions = `<button class="pill-btn" data-rp-rule="${escapeHtml(term.key)}">Write the rule</button>`;
      }
      actions += `<button class="link-btn" data-rp-term="${escapeHtml(term.key)}" data-status="not_needed">Not needed</button>`;
    } else {
      actions = `<button class="link-btn" data-rp-term="${escapeHtml(term.key)}" data-status="open">Reopen</button>`;
    }
    const resolved = term.status === "not_needed" ? `<span class="rp-resolved muted">Marked not needed</span>`
      : def ? `<span class="rp-resolved">Defined by <button class="link-btn" data-rp-jump="definitions">${escapeHtml(def.name)}</button></span>`
      : term.rule_text ? `<span class="rp-resolved">Rule: ${escapeHtml(term.rule_text)}</span>` : "";
    const writing = RP.ruleFor === term.key ? `<div class="rp-inline-form"><textarea data-rp-rule-text rows="2" maxlength="1200" placeholder="Say it precisely, e.g. “The sweep must happen in the same Globex session, before the 5m FVG forms.”"></textarea>
        <button class="pill-btn" data-rp-save-rule="${escapeHtml(term.key)}">Save rule</button><button class="link-btn" data-rp-cancel-rule>Cancel</button></div>` : "";
    return `<div class="rp-term ${done ? "done" : ""} kind-${term.kind}">
      <span class="rp-dot" aria-hidden="true">${done ? "●" : "○"}</span>
      <div class="rp-term-main">
        <div class="rp-term-label">${escapeHtml(term.label)} ${term.phrase ? `<span class="rp-phrase">“${escapeHtml(term.phrase)}”</span>` : term.kind === "rule" ? `<span class="rp-phrase muted">not mentioned</span>` : ""}</div>
        <div class="rp-term-why">${escapeHtml(term.why)}</div>${resolved}${writing}
      </div>
      <div class="rp-term-actions">${actions}</div>
    </div>`;
  }).join("");
  return `
    <div class="rp-formal-summary">
      <span><span class="label">Instrument</span><b>${escapeHtml(p.instrument.symbol)}</b></span>
      ${["context", "setup", "confirmation", "execution"].map((role) => {
        const v = (t.roles || {})[role];
        const text = Array.isArray(v) ? v.join(" / ") : v;
        return `<span><span class="label">${role}</span><b>${text ? escapeHtml(text) : "not set"}</b></span>`;
      }).join("")}
    </div>
    <p class="rp-lead">Trading language is vague on purpose; tests can't be. Each line below is a word in your observation that a computer would have to guess at. Until it is defined, it can't be tested.</p>
    <div class="rp-progress"><span style="width:${p.terms.length ? Math.round(100 * p.terms.filter((x) => x.status !== "open").length / p.terms.length) : 0}%"></span></div>
    <div class="rp-terms">${rows || `<p class="rp-fine">Nothing detected. Add definitions directly below.</p>`}</div>
    <button class="link-btn" data-rp-rescan>${icon("rerun", 13)}Scan the observation again</button>`;
}

function definitionsHtml(p) {
  const cards = p.definitions.map((d) => {
    if (RP.editor && RP.editor.id === d.id) return editorHtml();
    const c = conceptOf(d.concept);
    return `<article class="rp-def glass-flat">
      <div class="rp-def-head"><b>${escapeHtml(d.name)}</b><span class="rp-chip">${escapeHtml(c?.label || d.concept)}</span>
        ${d.params.timeframe ? `<span class="rp-chip">${escapeHtml(tfLabel(d.params.timeframe))}</span>` : ""}
        <span class="rp-spacer"></span>
        <button class="small-btn" data-rp-edit-def="${d.id}">${icon("edit", 13)}Edit</button>
        <button class="small-btn" data-rp-del-def="${d.id}" aria-label="Delete definition">${icon("trash", 13)}</button></div>
      <ol class="rp-explain">${d.explanation.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ol>
      ${d.notes ? `<p class="rp-def-notes">${escapeHtml(d.notes)}</p>` : ""}
      <div class="rp-def-foot"><p class="rp-status-line">${c?.detection ? "Detected automatically, look-ahead safe." : "Can't be measured yet (see the concept library)."}${d.source && d.source.startsWith("saved") ? ` · ${escapeHtml(d.source)}` : ""}</p>
        ${personalFormHtml(d)}</div>
    </article>`;
  }).join("");
  const newEditor = RP.editor && !RP.editor.id ? editorHtml() : "";
  return `${newEditor}${cards || (newEditor ? "" : `<p class="rp-fine">No definitions yet. Use “Define” above, or pick a concept:</p>`)}
    <div class="rp-add-def"><select data-rp-new-def><option value="">+ Define a concept from the library…</option>${RP.meta.concepts.map((c) =>
      `<option value="${c.id}">${escapeHtml(c.category)} · ${escapeHtml(c.label)}</option>`).join("")}</select>
      <button class="link-btn" data-rp-library>${icon("method", 13)}Browse the concept library</button></div>`;
}

function paramControl(name, spec, value) {
  const label = `<span>${escapeHtml(spec.label || name)}${spec.unit ? ` <i>(${escapeHtml(spec.unit)})</i>` : ""}</span>`;
  if (spec.kind === "choice") {
    const options = spec.options.map((o) => `<option value="${escapeHtml(String(o))}" ${String(o) === String(value) ? "selected" : ""}>${escapeHtml(name === "session" ? (RP.meta.sessions[o]?.label || o) : optLabel(o))}</option>`).join("");
    return `<label class="rp-param">${label}<select data-rp-param="${name}" data-kind="choice">${options}</select></label>`;
  }
  if (spec.kind === "number") {
    return `<label class="rp-param">${label}<input type="number" data-rp-param="${name}" data-kind="number" value="${escapeHtml(String(value))}"
      ${spec.min != null ? `min="${spec.min}"` : ""} ${spec.max != null ? `max="${spec.max}"` : ""} step="${spec.step || 1}"></label>`;
  }
  if (spec.kind === "bool") {
    return `<label class="rp-param rp-check"><input type="checkbox" data-rp-param="${name}" data-kind="bool" ${value ? "checked" : ""}>${escapeHtml(spec.label)}</label>`;
  }
  if (spec.kind === "multi") {
    return `<fieldset class="rp-param rp-multi"><legend>${escapeHtml(spec.label)}</legend>${spec.options.map((o) =>
      `<label class="rp-tick"><input type="checkbox" data-rp-param="${name}" data-kind="multi" value="${escapeHtml(o)}" ${(value || []).includes(o) ? "checked" : ""}>${escapeHtml((spec.labels || {})[o] || o)}</label>`).join("")}</fieldset>`;
  }
  if (spec.kind === "time") {
    return `<label class="rp-param">${label}<input type="time" data-rp-param="${name}" data-kind="time" value="${escapeHtml(value || "")}"></label>`;
  }
  return "";
}

function editorHtml() {
  const e = RP.editor;
  const c = conceptOf(e.concept);
  const shown = (c.param_order || Object.keys(c.params)).map((name) => [name, c.params[name]]).filter(([name]) => !(e.concept === "time_window" && ["start", "end"].includes(name) && e.params.session !== "custom")
    && !(e.concept === "fvg" && ["min_body_ratio", "displacement_atr"].includes(name) && !e.params.require_displacement));
  return `<article class="rp-def rp-editor glass-flat" data-rp-editor>
    <div class="rp-def-head"><input class="rp-def-name" data-rp-def-name value="${escapeHtml(e.name)}" maxlength="120" aria-label="Definition name">
      <span class="rp-chip">${escapeHtml(c.label)}</span></div>
    ${e.termLabel ? `<p class="rp-fine">Defines: <b>${escapeHtml(e.termLabel)}</b></p>` : ""}
    <div class="rp-params">${shown.map(([name, spec]) => paramControl(name, spec, e.params[name])).join("")}</div>
    <div class="rp-explain-box"><div class="label">What MarketLab will call “${escapeHtml(e.name)}”</div><ol class="rp-explain" data-rp-explain><li>…</li></ol></div>
    ${c.states ? `<details class="rp-states"><summary>${c.states.length} states this concept moves through</summary>
      <table><thead><tr><th>State</th><th>Rule</th><th>Known at</th></tr></thead><tbody>${c.states.map((s) =>
        `<tr><td><b>${s.id.replace(/_/g, " ")}</b></td><td>${escapeHtml(s.rule)}</td><td>${escapeHtml(s.known)}</td></tr>`).join("")}</tbody></table></details>` : ""}
    <label class="rp-param wide"><span>Your notes on this definition</span><textarea data-rp-def-notes rows="2" maxlength="2000" placeholder="Why this version? What are you unsure about?">${escapeHtml(e.notes || "")}</textarea></label>
    <div class="rp-editor-foot"><button class="pill-btn primary-action" data-rp-save-def>${e.id ? "Save changes" : "Save definition"}</button>
      <button class="link-btn" data-rp-cancel-def>Cancel</button></div>
  </article>`;
}

function hypothesisHtml(p) {
  const conds = p.hypothesis.conditions;
  const req = RP.meta.requirements;
  const rows = conds.map((c, i) => {
    const d = p.definitions.find((x) => x.id === c.definition_id);
    const options = req[d?.concept] || req._default;
    return `<div class="rp-cond">
      ${i ? `<span class="rp-join">${p.hypothesis.sequence === "ordered" ? "THEN" : "AND"}</span>` : `<span class="rp-join">WHEN</span>`}
      <span class="rp-letter">${escapeHtml(c.letter)}</span>
      <b>${escapeHtml(d?.name || "missing definition")}</b>
      <select data-rp-cond="${c.id}" data-field="requirement">${options.map((o) => `<option ${o === c.requirement ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")}</select>
      <select data-rp-cond="${c.id}" data-field="timing">${RP.meta.timings.map((o) => `<option ${o === c.timing ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")}</select>
      ${c.timing === "within the last N minutes" ? `<label class="rp-within">N = <input type="number" min="1" data-rp-cond="${c.id}" data-field="within" value="${c.within || ""}"> min</label>` : ""}
      <span class="rp-cond-tools">
        <button class="small-btn" data-rp-move="${c.id}" data-dir="-1" ${i === 0 ? "disabled" : ""} aria-label="Move up">${icon("chevron-up", 12)}</button>
        <button class="small-btn" data-rp-move="${c.id}" data-dir="1" ${i === conds.length - 1 ? "disabled" : ""} aria-label="Move down">${icon("chevron-down", 12)}</button>
        <button class="small-btn" data-rp-del-item="conditions" data-id="${c.id}" aria-label="Remove condition">${icon("close", 12)}</button></span>
    </div>`;
  }).join("");
  const canAdd = p.definitions.length;
  return `
    <div class="rp-seq"><span class="label">Order</span>
      <label class="rp-tick"><input type="radio" name="rp-seq" data-rp-seq="ordered" ${p.hypothesis.sequence === "ordered" ? "checked" : ""}>In this order (A, then B, then C)</label>
      <label class="rp-tick"><input type="radio" name="rp-seq" data-rp-seq="any" ${p.hypothesis.sequence === "any" ? "checked" : ""}>All true, any order</label>
      <span class="label" style="margin-left:10px">Direction</span>
      <label class="rp-tick" data-tip="A bullish FVG only combines with a sell-side sweep and other bullish events"><input type="radio" name="rp-dir" data-rp-dir="same" ${(p.hypothesis.direction_rule || "same") === "same" ? "checked" : ""}>All conditions point the same way</label>
      <label class="rp-tick"><input type="radio" name="rp-dir" data-rp-dir="ignore" ${p.hypothesis.direction_rule === "ignore" ? "checked" : ""}>Ignore direction</label></div>
    <div class="rp-conds">${rows || `<p class="rp-fine">${canAdd ? "Add conditions from your definitions." : "Define at least one concept first; conditions are built only from definitions, so nothing vague slips in."}</p>`}</div>
    ${canAdd ? `<div class="rp-add-cond"><select data-rp-add-cond><option value="">+ Add a condition…</option>${p.definitions.map((d) =>
      `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join("")}</select></div>` : ""}
    <label class="rp-param wide"><span>What counts as “it worked”? (the outcome you will measure)</span>
      <textarea data-rp-outcome rows="2" maxlength="1200" placeholder="e.g. Price reaches 2R before the stop, within 60 minutes.">${escapeHtml(p.hypothesis.outcome || "")}</textarea></label>
    <p class="rp-fine">Look-ahead rule: every condition is checked only with candles that had closed at that moment. A condition counts from its <b>confirmation</b> time, not from when it first started to happen.</p>`;
}

function ideaParamsHtml(collection, kind) {
  const n = (name, label, value, step = 1) => `<label class="rp-param small"><span>${label}</span><input type="number" min="0" step="${step}" data-rp-idea-param="${name}" value="${value}"></label>`;
  if (kind === "custom") return `<label class="rp-param wide"><span>Rule</span><input data-rp-idea-text maxlength="600" placeholder="Describe the rule precisely"></label>`;
  if (collection === "exits" && kind === "fixed_points") return n("points", "Points", 20, 0.25);
  if (collection === "exits" && kind === "r_multiple") return n("r", "R multiple", 2, 0.25);
  if (collection === "exits" && kind === "structure") return `<label class="rp-param"><span>Target</span><select data-rp-idea-param="target">${RP.meta.structure_targets.map((t) => `<option>${escapeHtml(t)}</option>`).join("")}</select></label>`;
  if (kind === "time") return n("minutes", "Minutes", 60);
  if (collection === "stops" && kind === "fixed_points") return n("points", "Points", 20, 0.25);
  if (collection === "stops" && kind === "atr") return n("atr_mult", "× ATR", 1.5, 0.1) + n("atr_period", "ATR period", 14);
  return "";
}

function ideasHtml(p) {
  const column = (collection, title, kinds) => {
    const draft = RP.ideaDraft[collection];
    return `<div class="rp-ideas-col">
      <h4>${title}</h4>
      ${p[collection].map((i) => `<div class="rp-idea"><span class="rp-letter">${escapeHtml(i.letter)}</span><span>${escapeHtml(i.description)}</span>
        <button class="small-btn" data-rp-del-item="${collection}" data-id="${i.id}" aria-label="Remove">${icon("close", 12)}</button></div>`).join("") || `<p class="rp-fine">None yet.</p>`}
      <div class="rp-idea-add" data-rp-idea-form="${collection}">
        <select data-rp-idea-kind="${collection}">${Object.entries(kinds).map(([k, label]) => `<option value="${k}" ${draft.kind === k ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select>
        ${ideaParamsHtml(collection, draft.kind)}
        <button class="pill-btn" data-rp-add-idea="${collection}">${icon("plus", 12)}Add</button>
      </div></div>`;
  };
  return `<p class="rp-lead">No entry, stop or exit is assumed to be correct. Each one becomes a row or column in the comparison once testing is connected (entries × exits × stops).</p>
    <div class="rp-ideas">${column("entries", "Entries", RP.meta.entry_kinds)}${column("stops", "Stops / invalidation", RP.meta.stop_kinds)}${column("exits", "Exits / targets", RP.meta.exit_kinds)}</div>
    <p class="rp-fine">${p.entries.length * p.stops.length * p.exits.length} combinations so far. More combinations means more chances to find something that looks good by luck; MarketLab will count them.</p>`;
}

function settingsHtml(p) {
  const s = p.settings;
  const session = s.session || { session: "", start: "09:30", end: "11:00" };
  const costs = s.costs;
  const contract = RP.meta.instruments[s.contract || p.instrument.symbol];
  return `<div class="rp-settings">
    <label class="rp-param"><span>Allowed session</span><select data-rp-setting="session"><option value="">Any time</option>${Object.entries(RP.meta.sessions).map(([id, x]) =>
      `<option value="${id}" ${session.session === id ? "selected" : ""}>${escapeHtml(x.label)}${x.start ? ` (${x.start}–${x.end} ET)` : ""}</option>`).join("")}</select></label>
    ${session.session === "custom" ? `<label class="rp-param small"><span>From (ET)</span><input type="time" data-rp-setting="start" value="${escapeHtml(session.start || "")}"></label>
      <label class="rp-param small"><span>To (ET)</span><input type="time" data-rp-setting="end" value="${escapeHtml(session.end || "")}"></label>` : ""}
    <label class="rp-param small"><span>Max. holding time <i>(minutes)</i></span><input type="number" min="1" data-rp-setting="max_holding_minutes" value="${s.max_holding_minutes || ""}" placeholder="none"></label>
    <label class="rp-param small"><span>Contract for P&amp;L</span><select data-rp-setting="contract">${Object.entries(RP.meta.instruments).filter(([, i]) => i.available).map(([sym]) =>
      `<option ${s.contract === sym ? "selected" : ""}>${sym}</option>`).join("")}</select></label>
  </div>
  ${contract ? `<p class="rp-fine">${escapeHtml(s.contract || p.instrument.symbol)}: tick ${contract.tick_size} points = $${contract.tick_value.toFixed(2)}; 1 point = $${contract.point_value.toFixed(2)}. NQ and MNQ share one market (same signals); only the dollars differ.</p>` : ""}
  <div class="rp-settings">
    <label class="rp-param small"><span>Commission <i>($ per side)</i></span><input type="number" min="0" step="0.01" data-rp-cost="commission_per_side" value="${costs.commission_per_side}"></label>
    <label class="rp-param small"><span>Exchange fees <i>($ per side)</i></span><input type="number" min="0" step="0.01" data-rp-cost="exchange_fees_per_side" value="${costs.exchange_fees_per_side}"></label>
    <label class="rp-param small"><span>Slippage <i>(ticks per side)</i></span><input type="number" min="0" step="0.5" data-rp-cost="slippage_ticks" value="${costs.slippage_ticks}"></label>
    <label class="rp-param small"><span>Spread <i>(ticks)</i></span><input type="number" min="0" step="0.5" data-rp-cost="spread_ticks" value="${costs.spread_ticks}"></label>
  </div>
  <p class="rp-fine">Results will be shown both gross and net of these assumptions. Enter your broker's actual commission and fees; MarketLab doesn't guess them.</p>`;
}

function rolesHtml(p) {
  const roles = p.timeframes.roles || {};
  const tfs = RP.meta.timeframes;
  const select = (role, label) => `<label data-tip="${escapeHtml(ROLE_HELP[role])}">${label} <select data-rp-role="${role}"><option value="">—</option>${tfs.map((tf) =>
    `<option value="${tf}" ${roles[role] === tf ? "selected" : ""}>${tf}</option>`).join("")}</select></label>`;
  return `<span class="rp-roles">${select("context", "Context")}${select("setup", "Setup")}
    <span class="rp-exec" data-tip="${escapeHtml(ROLE_HELP.confirmation)}"><span class="label">Confirmation</span>${["1m", "5m", "15m"].map((tf) =>
      `<label class="rp-tick"><input type="checkbox" data-rp-confirm="${tf}" ${(roles.confirmation || []).includes(tf) ? "checked" : ""}>${tf}</label>`).join("")}</span>
    ${select("execution", "Execution")}</span>`;
}
const ROLE_HELP = {
  context: "The higher timeframe that sets direction (e.g. the 1H FVG).",
  setup: "Where the setup forms (e.g. the 5m sweep and FVG).",
  confirmation: "Timeframes allowed to confirm (e.g. 5m or 1m validation).",
  execution: "Candles used to simulate entries and exits. Finer is more realistic; seconds need a second-level data source.",
};

const STATUS_CLASS = { LIVE: "live", DELAYED: "delayed", "LATEST AVAILABLE": "latest", HISTORICAL: "historical" };

function datasetHtml(p) {
  const data = p.settings.data || { provider: "auto", base: "auto" };
  const controls = `<div class="rp-ds-controls">
      <label class="rp-param small"><span>Data source</span><select data-rp-data-setting="provider">
        <option value="auto" ${data.provider === "auto" ? "selected" : ""}>Automatic (all free sources)</option>
        <option value="yahoo" ${data.provider === "yahoo" ? "selected" : ""}>Yahoo continuous only (free, delayed)</option>
        <option value="firstrate" ${data.provider === "firstrate" ? "selected" : ""}>FirstRate Data sample only (~2 weeks)</option>
        <option value="local" ${data.provider === "local" ? "selected" : ""}>Local files (data/intraday/)</option></select></label>
      <label class="rp-param small"><span>Base candles</span><select data-rp-data-setting="base">${RP.meta.data_bases.map((b) =>
        `<option value="${b}" ${data.base === b ? "selected" : ""}>${b === "auto" ? "Automatic" : b}</option>`).join("")}</select></label>
      <button class="small-btn" data-rp-ds-refresh>${icon("rerun", 13)}Refresh data</button></div>`;
  const d = RP.dataset;
  if (RP.datasetError) return controls + `<p class="rp-error">${escapeHtml(RP.datasetError)}</p>`;
  if (!d) return controls + `<p class="rp-fine">${RP.datasetLoading ? "Loading candles and checking their quality… (the first download takes about 10 seconds)" : "Not loaded yet."}</p>`;
  const ago = (ts) => ts ? `${Math.max(0, Math.round((Date.now() / 1000 - ts) / 60))} min ago` : "—";
  const counts = Object.entries(d.anomaly_counts || {});
  return `${controls}
    <div class="rp-ds glass-flat">
      <div class="rp-ds-top"><span class="rp-status ${STATUS_CLASS[d.status.label] || ""}">${escapeHtml(d.status.label)}</span>
        <span class="rp-fine">${escapeHtml(d.status.why)}</span></div>
      <div class="rp-ds-grid">
        <div><span class="label">Provider</span><b>${escapeHtml(d.provider)}</b></div>
        <div><span class="label">Instrument</span><b>${escapeHtml(d.instrument)}</b></div>
        <div><span class="label">First candle</span><b>${escapeHtml(d.first_label || "—")}</b></div>
        <div><span class="label">Last candle</span><b>${escapeHtml(d.last_label || "—")}</b></div>
        <div><span class="label">Candles</span><b>${(d.candles || 0).toLocaleString("en-US")}</b> <span class="rp-fine">${d.trading_days} trading days</span></div>
        <div><span class="label">Missing candles</span><b>${d.missing_bars}</b> <span class="rp-fine">in ${d.missing_ranges} gaps</span></div>
        <div><span class="label">Excluded as suspect</span><b>${(d.suspect_bars || 0).toLocaleString("en-US")}</b></div>
        <div><span class="label">Freshness</span><b>${ago(d.last + 60)}</b> <span class="rp-fine">downloaded ${ago(d.fetched_at)}</span></div>
      </div>
      <p class="rp-fine">${escapeHtml(d.methodology || "")}. ${escapeHtml(d.history_note || "")}.</p>
      <div class="rp-intervals">${d.intervals.map((iv) => `<span class="rp-iv ${iv.available ? "" : "off"}" data-tip="${escapeHtml(iv.why)}">${iv.interval}</span>`).join("")}</div>
      ${provenanceHtml(d)}
      ${counts.length ? `<details class="rp-anoms"><summary>${counts.map(([k, v]) => `<span class="rp-chip">${escapeHtml(k)} · ${v}</span>`).join(" ")}</summary>
        <table><thead><tr><th>Type</th><th>From</th><th>To</th><th>Candles</th><th>What it means</th></tr></thead><tbody>${d.anomalies.map((a) =>
          `<tr class="sev-${a.severity}"><td>${escapeHtml(a.type)}</td><td>${escapeHtml(a.start_label)}</td><td>${escapeHtml(a.end_label)}</td><td>${a.bars}</td><td>${escapeHtml(a.detail)}</td></tr>`).join("")}</tbody></table>
        <p class="rp-fine">Nothing is filled in or smoothed. Suspect candles can't create FVGs, sweeps, levels or entries; trades that touch them are excluded from results.</p></details>`
        : `<p class="rp-fine">No anomalies detected.</p>`}
    </div>`;
}

async function loadDataset({ refresh = false } = {}) {
  const p = RP.project;
  if (!p || RP.datasetLoading) return;
  RP.datasetLoading = true; RP.datasetError = null;
  redrawPart("[data-rp-dataset]", () => datasetHtml(p));
  try {
    RP.dataset = await fetchJson(rpUrl(`/dataset${refresh ? "?refresh=1" : ""}`));
  } catch (error) { RP.datasetError = error.message; }
  RP.datasetLoading = false;
  if (RP.project && RP.project.id === p.id) redrawPart("[data-rp-dataset]", () => datasetHtml(p));
}

function redrawPart(selector, html) {
  const el = labState.el.querySelector(selector);
  if (el) el.innerHTML = html();
}

function experimentsHtml(p) {
  const checks = p.readiness.checks;
  const ready = checks.every((c) => c.ok);
  const f = p.formalization;
  const blocked = p.mode === "quick" && f && !(f.status.ready && !f.ambiguities.some((a) => !a.answer) && !f.unsupported.some((u) => !u.answer));
  return `<div class="rp-ready glass-flat">
    <div class="rp-ready-head"><b>Ready to test?</b><span>${checks.filter((c) => c.ok).length} of ${checks.length}</span></div>
    <ul>${checks.map((c) => `<li class="${c.ok ? "ok" : ""}">${c.ok ? icon("check", 13) : "○"} ${escapeHtml(c.label)}${c.note ? ` <i>${escapeHtml(c.note)}</i>` : ""}</li>`).join("")}</ul>
    <div class="rp-run-row"><button class="run-btn ${RP.running ? "loading" : ""}" data-rp-run ${RP.running || blocked ? "disabled" : ""}>Find every setup <span class="arrow">→</span></button>
      <span class="rp-fine">${blocked ? "Answer the open questions in “Describe my trade” first: the rules aren't deterministic yet." : ready ? "Tests every entry × stop × exit combination on the dataset above." : "You can run it now; unfinished parts are reported plainly."}</span></div>
  </div>
  <div data-rp-results>${RP.result ? resultHtml(RP.result) : RP.runError ? `<p class="rp-error">${escapeHtml(RP.runError)}</p>` : ""}</div>
  ${projectHistoryHtml(p)}
  ${p.hypothesis.conditions.length ? `<div data-dmt-after>${dmtAfterResults(p)}</div>` : ""}`;
}

const fmtR = (v, unit = "R", digits = 2) => v == null ? "—" : unit === "R" ? `${v >= 0 ? "+" : ""}${v.toFixed(digits)}R` : `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(0)}`;
const pct = (v) => v == null ? "—" : `${Math.round(v * 100)}%`;

function resultHtml(r) {
  const m = r.primary.metrics, st = r.primary.stats, cell = r.primary.cell;
  const unit = m.unit || "R";
  const p = RP.project;
  const idea = (col, id) => p[col].find((x) => x.id === id);
  const e = idea("entries", cell.entry), s = idea("stops", cell.stop), x = idea("exits", cell.exit);
  const log = r.search_log || {};
  const tile = (label, value, sub = "") => `<div class="rp-tile"><span class="label">${label}</span><b>${value}</b>${sub ? `<span>${sub}</span>` : ""}</div>`;
  const dropped = [["no_fill", "not filled"], ["skipped_overlap", "skipped (already in a trade)"], ["bad_stop", "invalidated before entry (price was already past the stop level)"],
                   ["tiny_risk", "risk under 1 point"], ["no_target", "no target level beyond entry"], ["open_at_end", "still open when data ends"]]
    .filter(([k]) => st[k]).map(([k, label]) => `${st[k]} ${label}`);
  const sens = outlierSensitivity(r.primary.trades, unit);
  return `
    ${tierBannerHtml(r)}
    <div class="rp-result-hero glass-flat">
      <div class="rp-sample"><b>${m.n || 0}</b><span>trades</span></div>
      <div class="rp-sample-text">
        <p><b>${r.setups}</b> setups matched in <b>${r.data.trading_days}</b> trading days of ${escapeHtml(r.data.symbol)} ${escapeHtml(r.data.base)} data
          (${escapeHtml((r.dataset?.status?.label || "").toLowerCase())}).</p>
        ${r.warnings.map((w) => `<p class="rp-warn">${icon("warn", 13)} ${escapeHtml(w)}</p>`).join("")}
        ${log.experiments > 3 || log.variants > 30 ? `<p class="rp-warn">${icon("warn", 13)} ${log.experiments} rule sets and ${log.variants} combinations tested in this project${log.ladder_variants ? `, plus ${log.ladder_variants} ladder variants counted` : ""}. With that many tries, some strong-looking results appear by chance.</p>` : ""}
        ${dropped.length ? `<p class="rp-fine">Setups without a counted trade: ${dropped.join(" · ")}.</p>` : ""}
        ${m.excluded_suspect ? `<p class="rp-fine">${m.excluded_suspect} trade${m.excluded_suspect === 1 ? "" : "s"} touched suspect data and ${m.excluded_suspect === 1 ? "is" : "are"} excluded.</p>` : ""}
        ${st.untestable ? `<p class="rp-error">${escapeHtml(st.untestable)}</p>` : ""}
      </div>
    </div>
    ${m.n ? `<div class="rp-tiles">
      ${tile("Win rate", pct(m.win_rate), `${m.wins} won · ${m.losses} lost`)}
      ${tile("Expectancy (net)", fmtR(m.expectancy, unit), unit === "R" ? `${fmtR(m.expectancy_dollars, "$")} per contract` : "")}
      ${tile("Median trade", fmtR(m.median, unit), `average ${fmtR(m.mean, unit)}`)}
      ${tile("Average win / loss", `${fmtR(m.avg_win, unit)} / ${fmtR(m.avg_loss, unit)}`)}
      ${tile("Max drawdown", fmtR(m.max_drawdown, unit), `${m.max_consec_losses} losses in a row at worst`)}
      ${tile("95% interval of the mean", m.ci95 ? `${fmtR(m.ci95[0], unit)} to ${fmtR(m.ci95[1], unit)}` : "—", m.n < 30 ? "wide: few trades" : "bootstrap; past sample only")}
    </div>
    ${distributionHtml(r.primary.trades, unit)}` : `<p class="rp-fine">No completed trades for this combination${dropped.length ? " (see above)" : ""}.</p>`}
    ${typeof luckHtml === "function" ? luckHtml(r) : ""}
    ${r.primary.trades.length ? browseHtml(r, unit) : ""}
    <details class="rp-advanced"${RP.advancedOpen ? " open" : ""} data-rp-adv>
      <summary>Advanced statistics <span>combinations · breakdowns · funnel · outliers · MFE/MAE</span></summary>
      <p class="rp-combo">Showing <b>Entry ${escapeHtml(e?.letter || "")}</b> ${escapeHtml(e?.description || "")} · <b>Stop ${escapeHtml(s?.letter || "")}</b> ${escapeHtml(s?.description || "")} ·
        <b>Exit ${escapeHtml(x?.letter || "")}</b> ${escapeHtml(x?.description || "")}. Trigger: condition ${escapeHtml(r.trigger_letter || "?")} (${r.triggers} candidates).</p>
      ${m.n ? `<div class="rp-tiles">
        ${tile("Profit factor", m.profit_factor == null ? "—" : m.profit_factor.toFixed(2))}
        ${tile("Holding time", `${Math.round(m.holding_median)} min`, `median · mean ${Math.round(m.holding_mean)} min`)}
        ${tile("MFE / MAE", `${(m.mfe_mean || 0).toFixed(2)} / ${(m.mae_mean || 0).toFixed(2)}`, `average, in ${m.mfe_unit}`)}
        ${tile("Gross expectancy", fmtR(m.gross_mean, unit), "before costs")}
        ${sens ? tile("Without the best trade", fmtR(sens.withoutBest, unit), `without the best 10%: ${fmtR(sens.withoutTop, unit)}`) : ""}
        ${m.sharpe_like != null ? tile("Per-trade Sharpe-like", m.sharpe_like.toFixed(2), `Sortino-like ${m.sortino_like == null ? "—" : m.sortino_like.toFixed(2)}`) : ""}
      </div>` : ""}
      ${typeof funnelHtml === "function" ? funnelHtml(r) : ""}
      ${matrixHtml(r)}
      ${m.n ? `<div class="rp-breakdowns">${groupHtml("By hour of entry (ET)", m.by_hour, (k) => `${k}:00`, unit)}${groupHtml("By weekday", m.by_weekday, (k) => k, unit)}${groupHtml("By exit reason", m.by_reason, (k) => k, unit)}</div>` : ""}
      ${typeof quickNoteHtml === "function" ? quickNoteHtml() : ""}
      <div class="dmt-ai-row"><button class="link-btn" data-dmt-ai="result">${icon("ask", 13)}Explain this result in plain words (AI, optional)</button></div>
      ${typeof DMT !== "undefined" && DMT.ai.result ? `<div class="dmt-ai glass-flat"><p>${escapeHtml(DMT.ai.result.text)}</p><p class="rp-fine">${escapeHtml(DMT.ai.result.note || "")}</p></div>` : ""}
    </details>`;
}

function tierBannerHtml(r) {
  const tier = r.tier || {};
  const name = (tier.name || "exact").toUpperCase();
  if (!tier.relaxations || !tier.relaxations.length) return `<div class="rp-tier exact"><span class="rp-tier-tag">EXACT</span><span>Every rule exactly as described.</span></div>`;
  return `<div class="rp-tier ${name.toLowerCase()}"><span class="rp-tier-tag">${name}</span><div><b>This is not the exact strategy.</b> Relaxed:
    <ul>${tier.relaxations.map((x) => `<li><span>${escapeHtml(x.original)}</span> → <b>${escapeHtml(x.relaxed)}</b><small>${escapeHtml(x.why || "")}</small></li>`).join("")}</ul></div></div>`;
}

function outlierSensitivity(trades, unit) {
  const key = unit === "R" ? "r_net" : "net";
  const v = trades.filter((t) => !t.suspect && t[key] != null).map((t) => t[key]).sort((a, b) => b - a);
  if (v.length < 5) return null;
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  return { withoutBest: mean(v.slice(1)), withoutTop: mean(v.slice(Math.max(1, Math.round(v.length * 0.1)))) };
}

function distributionHtml(trades, unit) {
  const key = unit === "R" ? "r_net" : "net";
  const vals = trades.filter((t) => !t.suspect && t[key] != null).map((t) => t[key]);
  if (vals.length < 3) return "";
  const step = unit === "R" ? 0.5 : Math.max(50, Math.round((Math.max(...vals) - Math.min(...vals)) / 12 / 50) * 50);
  const lo = Math.floor(Math.min(...vals) / step) * step, hi = Math.ceil(Math.max(...vals) / step) * step;
  const counts = [];
  for (let b = lo; b <= hi + 1e-9; b += step) counts.push([b, vals.filter((v) => v >= b && v < b + step).length]);
  const max = Math.max(1, ...counts.map(([, n]) => n));
  return `<div class="rp-dist"><div class="label">Distribution of results (${unit === "R" ? "R" : "$"} per trade)</div>
    <div class="dmt-hist">${counts.map(([b, n]) => `<div class="dmt-hbar ${b < 0 ? "neg" : "pos"} ${n ? "" : "empty"}" data-tip="${fmtR(b, unit)} to ${fmtR(b + step, unit)}: ${n} trade${n === 1 ? "" : "s"}">
      <i style="height:${n ? Math.max(6, Math.round(100 * n / max)) : 0}%"></i>${Math.abs(b % (unit === "R" ? 1 : step * 2)) < 1e-9 ? `<span>${unit === "R" ? b + "R" : "$" + b}</span>` : ""}</div>`).join("")}</div></div>`;
}

function matrixHtml(r) {
  const p = RP.project;
  const cols = [];
  for (const s of p.stops) for (const x of p.exits) cols.push([s, x]);
  const current = r.primary.cell;
  const cell = (e, s, x) => {
    const c = r.matrix.find((m) => m.entry === e.id && m.stop === s.id && m.exit === x.id);
    if (!c) return "<td>—</td>";
    const on = current.entry === e.id && current.stop === s.id && current.exit === x.id;
    const tone = c.expectancy == null ? "" : c.expectancy > 0 ? "pos" : "neg";
    return `<td><button class="rp-cell ${on ? "on" : ""} ${tone}" data-rp-combo="${e.id}|${s.id}|${x.id}"
      data-tip="${escapeHtml(c.stats.untestable || `${c.n} trades · win ${pct(c.win_rate)} · median ${fmtR(c.median, c.unit)}`)}">
      <b>${c.stats.untestable ? "n/a" : fmtR(c.expectancy, c.unit)}</b><span>n ${c.n}</span></button></td>`;
  };
  return `<h4 class="rp-h4">Every combination <span class="rp-fine">(expectancy per trade, net of costs)</span></h4>
    <div class="rp-matrix-wrap"><table class="rp-matrix"><thead><tr><th>Entry ↓ / Stop · Exit →</th>${cols.map(([s, x]) =>
      `<th data-tip="${escapeHtml(`${s.description} · ${x.description}`)}">S${escapeHtml(s.letter)} · X${escapeHtml(x.letter)}</th>`).join("")}</tr></thead>
    <tbody>${p.entries.map((e) => `<tr><th data-tip="${escapeHtml(e.description)}">Entry ${escapeHtml(e.letter)}</th>${cols.map(([s, x]) => cell(e, s, x)).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="rp-fine">Not ranked and not optimized. The best-looking cell in a table this size is partly luck; compare neighbours and sample sizes.</p>`;
}

function groupHtml(title, groups, label, unit) {
  const rows = Object.entries(groups || {});
  if (!rows.length) return "";
  const max = Math.max(...rows.map(([, g]) => Math.abs(g.mean))) || 1;
  return `<div class="rp-group"><h5>${title}</h5>${rows.map(([k, g]) => `<div class="rp-group-row"><span>${escapeHtml(label(k))}</span>
    <span class="rp-bar"><i class="${g.mean >= 0 ? "pos" : "neg"}" style="width:${Math.round(48 * Math.abs(g.mean) / max)}%;${g.mean >= 0 ? "left:50%" : `right:50%`}"></i></span>
    <b>${fmtR(g.mean, unit)}</b><span class="rp-fine">n ${g.n} · ${pct(g.win_rate)}</span></div>`).join("")}</div>`;
}

function tradesTable(trades, unit) {
  return `<div class="rp-trades-wrap"><table class="rp-trades"><thead><tr><th>Entry (ET)</th><th>Dir.</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit</th><th>Why it ended</th>
    <th>Result</th><th>MFE / MAE</th><th>Min</th><th></th></tr></thead><tbody>${trades.map((t) => {
      const v = unit === "R" ? t.r_net : t.net;
      return `<tr class="${t.suspect ? "suspect" : ""}"><td>${escapeHtml(t.entry_label)}</td><td>${t.direction === "bullish" ? "Long" : "Short"}</td>
      <td>${t.entry.toFixed(2)}</td><td>${escapeHtml(t.stop_text || "—")}</td><td>${t.target != null ? t.target.toFixed(2) : "—"}</td><td>${t.exit.toFixed(2)}</td>
      <td>${escapeHtml(t.reason)}</td><td class="${v >= 0 ? "pos" : "neg"}">${fmtR(v, unit)}</td>
      <td>${unit === "R" ? `${(t.mfe_r ?? 0).toFixed(2)} / ${(t.mae_r ?? 0).toFixed(2)}R` : `${t.mfe_points} / ${t.mae_points} pts`}</td><td>${t.minutes}</td>
      <td class="rp-actions-cell"><button class="small-btn" data-rp-replay="${t.setup_time}">${icon("replay", 12)}Replay</button>
        <button class="small-btn" data-rp-save-trade="${t.setup_time}" data-tip="Save as an example in this project">${icon("save", 12)}</button></td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function projectHistoryHtml(p) {
  const runs = (p.experiments || []).slice(-8).reverse();
  if (!runs.length) return "";
  const log = p.search_log || {};
  return `<details class="rp-history"><summary>Earlier runs (${(p.experiments || []).length}) · ${log.experiments || 0} distinct rule sets, ${log.variants || 0} combinations tested</summary>
    <table><thead><tr><th>When</th><th>Combination</th><th>Setups</th><th>Trades</th><th>Win</th><th>Expectancy</th><th>Rules</th></tr></thead><tbody>${runs.map((x) =>
      `<tr><td>${escapeHtml(fmtDateTime(x.ran_at))}</td><td>${escapeHtml(x.combo)}</td><td>${x.setups}</td><td>${x.trades}</td><td>${pct(x.win_rate)}</td>
      <td>${fmtR(x.expectancy, x.unit)}</td><td><code>${escapeHtml(x.rules_hash)}</code></td></tr>`).join("")}</tbody></table></details>`;
}

async function runTest(combo = null) {
  if (RP.running) return;
  RP.running = true; RP.runError = null;
  drawResearch();
  try {
    const r = await postJson(rpUrl("/run"), { combo });
    RP.result = r;
    RP.dataset = r.dataset;
    const fresh = await fetchJson(rpUrl()).catch(() => null);
    if (fresh) RP.project = fresh;
  } catch (error) { RP.runError = error.message; RP.result = null; }
  RP.running = false;
  drawResearch();
  if (RP.result) labState.el.querySelector("[data-rp-results]")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function sectionInner(id) {
  const p = RP.project;
  const head = labState.el.querySelector(`#rp-${id} .section-head`)?.outerHTML || "";
  return head + (id === "experiments" ? experimentsHtml(p) : "");
}

async function saveTradeAsExample(setupTime) {
  const r = RP.result;
  const t = r.primary.trades.find((x) => x.setup_time === setupTime) || r.counterexamples.find((x) => x.setup_time === setupTime);
  const setup = r.setup_list.find((x) => x.time === setupTime);
  if (!t) return;
  const v = t.r_net ?? null;
  const when = new Date((t.entry_time) * 1000);
  const et = when.toLocaleString("en-US", { timeZone: "America/New_York", hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const [mdy, hm] = et.split(", ");
  const [mm, dd, yy] = mdy.split("/");
  const body = { kind: (v ?? t.net) > 0 ? "successful" : "failed", date: `${yy}-${mm}-${dd}`, time: hm.replace(/^24/, "00"),
    timeframe: r.execution, setup: (setup?.timeline || []).filter((x) => !x.detail).map((x) => `${x.time_label.split(" ").slice(-2, -1)[0]} ${x.label}`).join(" → "),
    entry: `${t.direction === "bullish" ? "Long" : "Short"} ${t.entry} (${r.primary.cell.label})`, exit: `${t.exit} · ${t.reason}`,
    result_r: v, notes: `Saved from a test run (rules ${r.rules_hash}).` };
  if (await rpCall(rpUrl("/items/examples"), body)) { showToast("Saved to Examples"); drawResearch(); }
}

function questionsHtml(p) {
  return `<div class="rp-questions">${p.questions.map((q) => `
      <div class="rp-question status-${q.status}">
        <select data-rp-qstatus="${q.id}" aria-label="Status">${RP.meta.question_statuses.map((s) => `<option value="${s}" ${q.status === s ? "selected" : ""}>${s}</option>`).join("")}</select>
        <span class="rp-q-text">${escapeHtml(q.text)}</span>
        <button class="small-btn" disabled data-tip="Testing a question creates a child experiment once data is connected">Test question</button>
        <button class="small-btn" data-rp-del-item="questions" data-id="${q.id}" aria-label="Delete question">${icon("close", 12)}</button>
      </div>`).join("") || `<p class="rp-fine">No questions yet.</p>`}</div>
    <div class="rp-inline-form row"><input data-rp-new-question maxlength="600" placeholder="e.g. Does this only work before 11:00 ET?"><button class="pill-btn" data-rp-add-question>${icon("plus", 12)}Add question</button></div>`;
}

function examplesHtml(p) {
  const count = (kind) => p.examples.filter((e) => e.kind === kind).length;
  const items = p.examples.slice().sort((a, b) => (b.date || "").localeCompare(a.date || "")).map((e) => `
    <article class="rp-example kind-${e.kind}">
      <div class="rp-example-head"><span class="rp-kind">${escapeHtml(e.kind)}</span><b>${escapeHtml(e.date || "no date")}${e.time ? ` ${escapeHtml(e.time)} ET` : ""}</b>
        ${e.timeframe ? `<span class="rp-chip">${escapeHtml(e.timeframe)}</span>` : ""}
        ${e.result_r != null ? `<span class="rp-r ${e.result_r >= 0 ? "up" : "down"}">${e.result_r >= 0 ? "+" : ""}${e.result_r}R</span>` : ""}
        <span class="rp-spacer"></span>
        <button class="small-btn" disabled data-tip="Replay opens once intraday data is connected">Replay this day</button>
        <button class="small-btn" data-rp-del-item="examples" data-id="${e.id}" aria-label="Delete example">${icon("close", 12)}</button></div>
      ${e.setup ? `<p>${escapeHtml(e.setup)}</p>` : ""}
      ${e.entry || e.exit ? `<p class="rp-fine">${e.entry ? `Entry: ${escapeHtml(e.entry)}` : ""}${e.entry && e.exit ? " · " : ""}${e.exit ? `Exit: ${escapeHtml(e.exit)}` : ""}</p>` : ""}
      ${e.notes ? `<p class="rp-def-notes">${escapeHtml(e.notes)}</p>` : ""}
    </article>`).join("");
  return `<p class="rp-lead">Keep the failures too. A notebook of only winners is how confirmation bias starts. <span class="rp-counts">Successful ${count("successful")} · Failed ${count("failed")} · Interesting ${count("interesting")}</span></p>
    <div class="rp-example-form glass-flat">
      <div class="rp-kinds">${RP.meta.example_kinds.map((k, i) => `<label class="rp-tick kind-${k}"><input type="radio" name="rp-ex-kind" value="${k}" ${i === 0 ? "checked" : ""}>${k}</label>`).join("")}</div>
      <div class="rp-settings">
        <label class="rp-param small"><span>Date</span><input type="date" data-rp-ex="date"></label>
        <label class="rp-param small"><span>Time (ET)</span><input type="time" data-rp-ex="time"></label>
        <label class="rp-param small"><span>Chart timeframe</span><select data-rp-ex="timeframe"><option value="">—</option>${RP.meta.timeframes.map((tf) => `<option value="${tf}">${tf}</option>`).join("")}</select></label>
        <label class="rp-param small"><span>Result <i>(R)</i></span><input type="number" step="0.1" data-rp-ex="result_r" placeholder="e.g. -1"></label>
      </div>
      <label class="rp-param wide"><span>What happened (conditions you saw)</span><input data-rp-ex="setup" maxlength="1200"></label>
      <div class="rp-settings"><label class="rp-param"><span>Entry</span><input data-rp-ex="entry" maxlength="300"></label><label class="rp-param"><span>Exit</span><input data-rp-ex="exit" maxlength="300"></label></div>
      <label class="rp-param wide"><span>Notes</span><input data-rp-ex="notes" maxlength="2000"></label>
      <button class="pill-btn" data-rp-add-example>${icon("plus", 12)}Save example</button>
    </div>
    <div class="rp-examples">${items}</div>`;
}

function notesHtml(p) {
  return `<div class="rp-inline-form"><textarea data-rp-new-note rows="3" maxlength="8000" placeholder="Anything: what you saw today, doubts, ideas for the next version…"></textarea>
      <button class="pill-btn" data-rp-add-note>${icon("plus", 12)}Add note</button></div>
    <div class="rp-notes">${p.notes.slice().reverse().map((n) => `<article class="rp-note"><span class="rp-when">${escapeHtml(fmtDateTime(n.created_at))}</span>
      <p>${escapeHtml(n.text)}</p><button class="small-btn" data-rp-del-item="notes" data-id="${n.id}" aria-label="Delete note">${icon("close", 12)}</button></article>`).join("")}</div>`;
}

function versionsHtml(p) {
  const byParent = {};
  for (const v of p.versions) (byParent[v.parent_id || "root"] = byParent[v.parent_id || "root"] || []).push(v);
  const ids = new Set(p.versions.map((v) => v.id));
  const roots = p.versions.filter((v) => !v.parent_id || !ids.has(v.parent_id));
  const node = (v) => {
    const snap = v.snapshot;
    return `<li><div class="rp-version ${p.current_version === v.id ? "current" : ""}">
        <b>${escapeHtml(v.label)}</b>${p.current_version === v.id ? `<span class="rp-chip">current</span>` : ""}
        <span class="rp-fine">${escapeHtml(fmtDateTime(v.saved_at))} · ${snap.definitions.length} definitions · ${snap.hypothesis.conditions.length} conditions · ${snap.entries.length}/${snap.stops.length}/${snap.exits.length} entries/stops/exits</span>
        ${v.note ? `<span class="rp-version-note">${escapeHtml(v.note)}</span>` : ""}
        ${p.current_version === v.id ? "" : `<button class="link-btn" data-rp-restore="${v.id}">Load these rules</button>`}
      </div>${(byParent[v.id] || []).length ? `<ul>${byParent[v.id].map(node).join("")}</ul>` : ""}</li>`;
  };
  return `<div class="rp-tree"><div class="rp-version root"><b>Original idea</b><span class="rp-fine">the observation above</span></div>
      ${roots.length ? `<ul>${roots.map(node).join("")}</ul>` : ""}</div>
    <div class="rp-inline-form row" id="rp-version-form"><input data-rp-version-label maxlength="60" placeholder="v${p.versions.length + 1}">
      <input data-rp-version-note maxlength="600" placeholder="What changed? e.g. + 5m validation, NY morning only">
      <button class="pill-btn" data-rp-save-version>${icon("save", 12)}Save version</button></div>
    <p class="rp-fine">A version freezes the definitions, hypothesis, ideas and assumptions. Loading an older version and saving again starts a new branch, so v3A and v3B can both grow from v2.</p>`;
}


// =========================================================
// Drawers: concept library and data status
// =========================================================
function openLibrary() {
  const groups = {};
  for (const c of RP.meta.concepts) (groups[c.category] = groups[c.category] || []).push(c);
  openDrawer(`<h3 class="rp-drawer-title">Concept library</h3>
    <p class="rp-fine">There is no single accepted definition for most trading concepts. These are MarketLab's starting definitions; every parameter can be changed per project, and the exact rule is always shown.</p>
    ${Object.entries(groups).map(([cat, list]) => `<h4 class="rp-lib-cat">${escapeHtml(cat)}</h4>${list.map((c) => `
      <details class="rp-lib-item"><summary><b>${escapeHtml(c.label)}</b> <span class="rp-fine">${c.detection ? "detected automatically" : "not measurable yet"}</span></summary>
        <ol class="rp-explain">${c.default_explanation.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ol></details>`).join("")}`).join("")}`,
    { label: "Concept library" });
}

function openDataStatus() {
  const d = RP.meta.data_status;
  openDrawer(`<h3 class="rp-drawer-title">Intraday data status</h3>
    <p>${escapeHtml(d.summary)}</p>
    <h4 class="rp-lib-cat">What Yahoo Finance provides for NQ=F / MNQ=F</h4>
    <ul class="rp-list">${d.yahoo_findings.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>
    <p><b>Verdict:</b> ${escapeHtml(d.verdict)}</p>
    <p class="rp-fine">The provider decision is yours (see the report in the chat). MarketLab won't silently use low-quality data for tests.</p>`, { label: "Data status" });
}


// =========================================================
// Events
// =========================================================
let rpAnalyzeTimer = null;
let rpExplainTimer = null;

function rpInput(event) {
  if (labState.mode !== "projects") return;
  if (dmtInput(event)) return;
  if (event.target.matches("[data-rp-obs]")) {
    clearTimeout(rpAnalyzeTimer);
    rpAnalyzeTimer = setTimeout(async () => {
      const box = labState.el.querySelector("[data-rp-detect]");
      if (!box) return;
      const text = event.target.value;
      if (!text.trim()) { box.innerHTML = ""; return; }
      try { box.innerHTML = detectHtml(await postJson("/api/research/analyze", { text })); } catch (error) { /* ignore */ }
    }, 350);
  }
  if (event.target.matches("[data-rp-param], [data-rp-def-name], [data-rp-def-notes]") && RP.editor) {
    readEditor();
    if (event.target.matches("[data-rp-param]")) { clearTimeout(rpExplainTimer); rpExplainTimer = setTimeout(loadExplanation, 250); }
  }
}

function readEditor() {
  const root = labState.el.querySelector("[data-rp-editor]");
  if (!root || !RP.editor) return;
  const params = { ...RP.editor.params };
  root.querySelectorAll("[data-rp-param]").forEach((el) => {
    const name = el.dataset.rpParam;
    if (el.dataset.kind === "multi") return;
    if (el.dataset.kind === "bool") params[name] = el.checked;
    else if (el.dataset.kind === "number") params[name] = el.value === "" ? params[name] : Number(el.value);
    else params[name] = el.value;
  });
  const multis = {};
  root.querySelectorAll('[data-kind="multi"]').forEach((el) => { (multis[el.dataset.rpParam] = multis[el.dataset.rpParam] || []); if (el.checked) multis[el.dataset.rpParam].push(el.value); });
  Object.assign(params, multis);
  RP.editor.params = params;
  const name = root.querySelector("[data-rp-def-name]");
  if (name) RP.editor.name = name.value;
  const notes = root.querySelector("[data-rp-def-notes]");
  if (notes) RP.editor.notes = notes.value;
}

async function loadExplanation() {
  const list = labState.el.querySelector("[data-rp-explain]");
  if (!list || !RP.editor) return;
  try {
    const data = await postJson("/api/research/explain", { concept: RP.editor.concept, params: RP.editor.params });
    list.innerHTML = data.explanation.map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  } catch (error) { list.innerHTML = `<li>${escapeHtml(error.message)}</li>`; }
}

function openEditor({ concept, termKey = null, termLabel = null, definition = null, timeframes = [] }) {
  const c = conceptOf(concept);
  const params = definition ? { ...definition.params } : Object.fromEntries(Object.entries(c.params).map(([k, s]) => [k, s.default]));
  if (!definition && timeframes.length && "timeframe" in params) params.timeframe = timeframes.includes("5m") ? "5m" : timeframes[0];
  RP.editor = { id: definition?.id || null, concept, termKey, termLabel, params,
                name: definition?.name || termLabel || c.label, notes: definition?.notes || "" };
  drawResearch();
  requestAnimationFrame(() => labState.el.querySelector("[data-rp-editor]")?.scrollIntoView({ behavior: "smooth", block: "start" }));
}

function jumpTo(id) {
  labState.el.querySelector(`#rp-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function resetProjectState() {
  Object.assign(RP, { editor: null, confirmDelete: false, revising: false, ruleFor: null, result: null, runError: null,
                      dataset: null, datasetError: null, showAllTrades: false });
  dmtResetProject();
}

async function openProject(id) {
  const project = await fetchJson(`/api/research/projects/${encodeURIComponent(id)}`).catch((error) => { showToast(error.message); return null; });
  if (!project) return;
  resetProjectState();
  Object.assign(RP, { project, view: "project" });
  drawResearch({ keepScroll: false });
  loadDataset();
  labState.el.querySelector(".window-body").scrollTop = 0;
}

async function goHome() {
  Object.assign(RP, { view: "home", project: null, editor: null, confirmDelete: false });
  RP.projects = (await fetchJson("/api/research/projects").catch(() => ({ projects: [] }))).projects;
  drawResearch({ keepScroll: false });
}

async function rpClick(event) {
  if (labState.mode !== "projects") return;
  if (await dmtClick(event)) return;
  if (event.target.closest("[data-rp-duplicate]") && RP.project) {
    const f = await postJson(rpUrl("/duplicate"), {}).catch((error) => { showToast(error.message); return null; });
    if (f) { showToast(`Duplicated: “${f.name}”`); resetProjectState(); RP.project = f; drawResearch({ keepScroll: false }); loadDataset(); }
    return;
  }
  const t = event.target;
  const el = (sel) => t.closest(sel);
  let hit;
  if ((hit = el("[data-rp-open]"))) return openProject(hit.dataset.rpOpen);
  if (el("[data-rp-home]")) return goHome();
  if (el("[data-rp-new]")) { RP.view = "new"; drawResearch({ keepScroll: false }); labState.el.querySelector("[data-rp-obs]")?.focus(); return; }
  if (el("[data-rp-library]")) return openLibrary();
  if (el("[data-rp-data]")) return openDataStatus();
  if ((hit = el("[data-rp-template]"))) {
    resetProjectState();
    const p = await rpCall("/api/research/projects", { template: hit.dataset.rpTemplate });
    if (p) { RP.view = "project"; drawResearch({ keepScroll: false }); loadDataset(); }
    return;
  }
  if (el("[data-rp-create]")) {
    const root = labState.el;
    const observation = root.querySelector("[data-rp-obs]").value.trim();
    if (!observation) { showToast("Write the observation first. It's the heart of the project."); return; }
    resetProjectState();
    const p = await rpCall("/api/research/projects", { observation, name: root.querySelector("[data-rp-name]").value, instrument: root.querySelector("[data-rp-inst]").value });
    if (p) { RP.view = "project"; drawResearch({ keepScroll: false }); loadDataset(); }
    return;
  }
  if (!RP.project) return;

  if ((hit = el("[data-rp-jump]"))) return jumpTo(hit.dataset.rpJump);
  if (el("[data-rp-run]")) return runTest(RP.result ? { entry: RP.result.primary.cell.entry, stop: RP.result.primary.cell.stop, exit: RP.result.primary.cell.exit } : null);
  if ((hit = el("[data-rp-combo]"))) {
    const [entry, stop, exit] = hit.dataset.rpCombo.split("|");
    return runTest({ entry, stop, exit });
  }
  if ((hit = el("[data-rp-replay]"))) return openReplay(Number(hit.dataset.rpReplay));
  if ((hit = el("[data-rp-save-trade]"))) return saveTradeAsExample(Number(hit.dataset.rpSaveTrade));
  if (el("[data-rp-more-trades]")) { RP.showAllTrades = true; drawResearch(); return; }
  if (el("[data-rp-ds-refresh]")) { RP.dataset = null; return loadDataset({ refresh: true }); }
  if (el("[data-rp-fork]")) {
    const f = await postJson(rpUrl("/fork"), {}).catch((error) => { showToast(error.message); return null; });
    if (f) { showToast(`Forked: “${f.name}”`); resetProjectState(); RP.project = f; drawResearch({ keepScroll: false }); loadDataset(); }
    return;
  }
  if (el("[data-rp-delete]")) {
    if (!RP.confirmDelete) { RP.confirmDelete = true; drawResearch(); return; }
    await postJson(rpUrl(), undefined, "DELETE").catch((error) => showToast(error.message));
    showToast("Project deleted");
    return goHome();
  }
  if (el("[data-rp-revise]")) { RP.revising = true; drawResearch(); labState.el.querySelector("[data-rp-revision]")?.focus(); return; }
  if (el("[data-rp-cancel-revision]")) { RP.revising = false; drawResearch(); return; }
  if (el("[data-rp-save-revision]")) {
    const text = labState.el.querySelector("[data-rp-revision]").value;
    if (await rpCall(rpUrl("/revisions"), { text })) { RP.revising = false; drawResearch(); }
    return;
  }
  if (el("[data-rp-rescan]")) { if (await rpCall(rpUrl("/rescan"), {})) { drawResearch(); showToast("Observation scanned again"); } return; }
  if ((hit = el("[data-rp-define]"))) {
    const term = RP.project.terms.find((x) => x.key === hit.dataset.rpDefine);
    return openEditor({ concept: term.concept, termKey: term.key, termLabel: term.label, timeframes: term.timeframes || [] });
  }
  if ((hit = el("[data-rp-term]"))) {
    if (await rpCall(rpUrl(), { term: { key: hit.dataset.rpTerm, status: hit.dataset.status } }, "PATCH")) drawResearch();
    return;
  }
  if ((hit = el("[data-rp-rule]"))) { RP.ruleFor = hit.dataset.rpRule; drawResearch(); labState.el.querySelector("[data-rp-rule-text]")?.focus(); return; }
  if (el("[data-rp-cancel-rule]")) { RP.ruleFor = null; drawResearch(); return; }
  if ((hit = el("[data-rp-save-rule]"))) {
    const rule = labState.el.querySelector("[data-rp-rule-text]").value;
    if (await rpCall(rpUrl(), { term: { key: hit.dataset.rpSaveRule, status: "defined", rule_text: rule } }, "PATCH")) { RP.ruleFor = null; drawResearch(); }
    return;
  }
  if ((hit = el("[data-rp-edit-def]"))) {
    const d = RP.project.definitions.find((x) => x.id === hit.dataset.rpEditDef);
    return openEditor({ concept: d.concept, definition: d });
  }
  if (el("[data-rp-cancel-def]")) { RP.editor = null; drawResearch(); return; }
  if (el("[data-rp-save-def]")) {
    readEditor();
    const e = RP.editor;
    const body = { concept: e.concept, params: e.params, name: e.name, notes: e.notes, term_key: e.termKey };
    const ok = e.id ? await rpCall(rpUrl(`/items/definitions/${e.id}`), body, "PATCH") : await rpCall(rpUrl("/items/definitions"), body);
    if (ok) { RP.editor = null; drawResearch(); showToast("Definition saved"); }
    return;
  }
  if ((hit = el("[data-rp-del-def]"))) {
    if (await rpCall(rpUrl(`/items/definitions/${hit.dataset.rpDelDef}`), undefined, "DELETE")) drawResearch();
    return;
  }
  if ((hit = el("[data-rp-move]"))) {
    if (await rpCall(rpUrl(`/items/conditions/${hit.dataset.rpMove}`), { move: Number(hit.dataset.dir) }, "PATCH")) drawResearch();
    return;
  }
  if ((hit = el("[data-rp-del-item]"))) {
    if (await rpCall(rpUrl(`/items/${hit.dataset.rpDelItem}/${hit.dataset.id}`), undefined, "DELETE")) drawResearch();
    return;
  }
  if ((hit = el("[data-rp-add-idea]"))) {
    const collection = hit.dataset.rpAddIdea;
    const form = labState.el.querySelector(`[data-rp-idea-form="${collection}"]`);
    const params = {};
    form.querySelectorAll("[data-rp-idea-param]").forEach((input) => { params[input.dataset.rpIdeaParam] = input.tagName === "SELECT" ? input.value : Number(input.value); });
    const text = form.querySelector("[data-rp-idea-text]")?.value || "";
    if (RP.ideaDraft[collection].kind === "custom" && !text.trim()) { showToast("Describe the custom rule first."); return; }
    if (await rpCall(rpUrl(`/items/${collection}`), { kind: RP.ideaDraft[collection].kind, params, text })) drawResearch();
    return;
  }
  if (el("[data-rp-add-question]")) {
    const input = labState.el.querySelector("[data-rp-new-question]");
    if (!input.value.trim()) return;
    if (await rpCall(rpUrl("/items/questions"), { text: input.value })) drawResearch();
    return;
  }
  if (el("[data-rp-add-note]")) {
    const input = labState.el.querySelector("[data-rp-new-note]");
    if (!input.value.trim()) return;
    if (await rpCall(rpUrl("/items/notes"), { text: input.value })) drawResearch();
    return;
  }
  if (el("[data-rp-add-example]")) {
    const root = labState.el.querySelector(".rp-example-form");
    const body = { kind: root.querySelector('input[name="rp-ex-kind"]:checked')?.value || "successful" };
    root.querySelectorAll("[data-rp-ex]").forEach((input) => { body[input.dataset.rpEx] = input.value; });
    if (await rpCall(rpUrl("/items/examples"), body)) { drawResearch(); showToast("Example saved"); }
    return;
  }
  if (el("[data-rp-save-version]")) {
    const label = labState.el.querySelector("[data-rp-version-label]").value;
    const note = labState.el.querySelector("[data-rp-version-note]").value;
    if (await rpCall(rpUrl("/versions"), { label, note })) { drawResearch(); showToast("Version saved"); }
    return;
  }
  if ((hit = el("[data-rp-restore]"))) {
    if (await rpCall(rpUrl(`/versions/${hit.dataset.rpRestore}/restore`), {})) { drawResearch(); showToast("Rules loaded from that version. Saving now starts a new branch."); }
  }
}

async function rpChange(event) {
  if (labState.mode !== "projects") return;
  if (dmtChange(event)) return;
  if (!RP.project) return;
  const t = event.target;
  const p = RP.project;
  if (t.matches("[data-rp-field]")) {
    const field = t.dataset.rpField;
    const body = field === "name" ? { name: t.value }
      : field === "instrument" ? { instrument: { symbol: t.value } }
      : { timeframes: { primary: t.value || null, execution: p.timeframes.execution } };
    if (await rpCall(rpUrl(), body, "PATCH") && field !== "name") drawResearch();
    return;
  }
  if (t.matches("[data-rp-role], [data-rp-confirm]")) {
    const roles = { ...(p.timeframes.roles || {}) };
    labState.el.querySelectorAll("[data-rp-role]").forEach((sel) => { roles[sel.dataset.rpRole] = sel.value || null; });
    roles.confirmation = [...labState.el.querySelectorAll("[data-rp-confirm]:checked")].map((x) => x.dataset.rpConfirm);
    if (await rpCall(rpUrl(), { timeframes: { roles } }, "PATCH")) { RP.result = null; drawResearch(); }
    return;
  }
  if (t.matches("[data-rp-dir]")) { if (await rpCall(rpUrl(), { hypothesis: { direction_rule: t.dataset.rpDir } }, "PATCH")) drawResearch(); return; }
  if (t.matches("[data-rp-data-setting]")) {
    if (await rpCall(rpUrl(), { settings: { data: { [t.dataset.rpDataSetting]: t.value } } }, "PATCH")) {
      RP.dataset = null; RP.result = null; drawResearch(); loadDataset();
    }
    return;
  }
  if (t.matches("[data-rp-new-def]") && t.value) return openEditor({ concept: t.value, timeframes: p.timeframes.execution.concat(p.timeframes.primary || []) });
  if (t.matches("[data-rp-link-term]") && t.value) {
    if (await rpCall(rpUrl(), { term: { key: t.dataset.rpLinkTerm, status: "defined", definition_id: t.value } }, "PATCH")) drawResearch();
    return;
  }
  if (t.matches("[data-rp-param]") && RP.editor) {
    readEditor();
    if (["require_displacement", "session"].includes(t.dataset.rpParam)) drawResearch(); else loadExplanation();
    return;
  }
  if (t.matches("[data-rp-seq]")) { if (await rpCall(rpUrl(), { hypothesis: { sequence: t.dataset.rpSeq } }, "PATCH")) drawResearch(); return; }
  if (t.matches("[data-rp-outcome]")) { await rpCall(rpUrl(), { hypothesis: { outcome: t.value } }, "PATCH"); return; }
  if (t.matches("[data-rp-add-cond]") && t.value) { if (await rpCall(rpUrl("/items/conditions"), { definition_id: t.value })) drawResearch(); return; }
  if (t.matches("[data-rp-cond]")) {
    if (await rpCall(rpUrl(`/items/conditions/${t.dataset.rpCond}`), { [t.dataset.field]: t.value }, "PATCH")) drawResearch();
    return;
  }
  if (t.matches("[data-rp-idea-kind]")) { RP.ideaDraft[t.dataset.rpIdeaKind] = { kind: t.value }; drawResearch(); return; }
  if (t.matches("[data-rp-qstatus]")) {
    const q = p.questions.find((x) => x.id === t.dataset.rpQstatus);
    if (await rpCall(rpUrl(`/items/questions/${q.id}`), { status: t.value }, "PATCH")) drawResearch();
    return;
  }
  if (t.matches("[data-rp-setting]")) {
    const key = t.dataset.rpSetting;
    const s = p.settings;
    let settings;
    if (key === "session" || key === "start" || key === "end") {
      const current = s.session || { session: "custom", start: "09:30", end: "11:00" };
      const next = key === "session" ? (t.value ? { ...current, session: t.value } : null) : { ...current, [key]: t.value };
      settings = { session: next };
    } else settings = { [key]: t.value };
    if (await rpCall(rpUrl(), { settings }, "PATCH")) drawResearch();
    return;
  }
  if (t.matches("[data-rp-cost]")) { await rpCall(rpUrl(), { settings: { costs: { [t.dataset.rpCost]: t.value } } }, "PATCH"); }
}

function rpKey(event) {
  if (labState.mode !== "projects") return;
  if (event.key === "Enter" && event.target.matches("[data-rp-field=name]")) { event.preventDefault(); event.target.blur(); }
  if (event.key === "Enter" && event.target.matches("[data-rp-new-question]")) { event.preventDefault(); labState.el.querySelector("[data-rp-add-question]").click(); }
}


// =========================================================
// Replay: one setup, candle by candle, future hidden
// =========================================================
const REPLAY = { data: null, chart: null, series: null, cursor: 0, timer: null, speed: 1, lines: [], linePrices: [], show: { zones: true, levels: true, trade: true } };

async function openReplay(setupTime) {
  let data;
  try {
    data = await postJson(rpUrl("/replay"), { setup_time: setupTime, combo: RP.result ? { entry: RP.result.primary.cell.entry, stop: RP.result.primary.cell.stop, exit: RP.result.primary.cell.exit } : null,
                                              relax_ids: RP.result?.tier?.relax_ids || [] });
  } catch (error) { showToast(error.message); return; }
  closeReplay();
  REPLAY.data = data;
  const firstFact = Math.min(...data.conditions.map((c) => c.known_at));
  const startIdx = Math.max(0, data.candles.findIndex((c) => c.close_t >= firstFact - 15 * 60));
  REPLAY.cursor = Math.max(5, startIdx);
  const overlay = document.createElement("div");
  overlay.className = "rp-replay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-label", "Replay");
  overlay.innerHTML = `
    <div class="rp-replay-card glass">
      <header class="rp-replay-head"><div><span class="label">Replay · ${escapeHtml(data.interval)} candles · ET</span>
        <h3>${escapeHtml(data.setup.direction === "bullish" ? "Bullish" : data.setup.direction === "bearish" ? "Bearish" : "")} setup · ${escapeHtml(data.setup.time_label)}</h3></div>
        <button class="icon-btn" data-replay-close aria-label="Close replay">${icon("close", 15)}</button></header>
      <div class="rp-replay-main">
        <div class="rp-replay-chart" data-replay-chart></div>
        <aside class="rp-replay-side">
          <div class="label">Conditions (known so far)</div><ul class="rp-checklist" data-replay-checks></ul>
          <div class="label" style="margin-top:14px">What happened</div><ol class="rp-replay-log" data-replay-log></ol>
          <div data-replay-trade class="rp-replay-trade"></div>
        </aside>
      </div>
      <footer class="rp-replay-controls">
        <button class="small-btn" data-replay-step="-1" aria-label="Previous candle">◀</button>
        <button class="small-btn" data-replay-play>${icon("replay", 12)}Play</button>
        <button class="small-btn" data-replay-step="1">Next candle →</button>
        <span class="rp-speeds">${[1, 2, 5, 10].map((x) => `<button class="rp-speed ${x === 1 ? "on" : ""}" data-replay-speed="${x}">${x}×</button>`).join("")}</span>
        <span class="rp-replay-now" data-replay-now></span>
        <span class="rp-spacer"></span>
        ${["zones", "levels", "trade"].map((k) => `<label class="rp-tick"><input type="checkbox" data-replay-show="${k}" checked>${k === "zones" ? "FVG zones" : k === "levels" ? "Levels" : "Entry / stop / target"}</label>`).join("")}
      </footer>
    </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener("click", replayClick);
  overlay.addEventListener("change", (event) => {
    const box = event.target.closest("[data-replay-show]");
    if (box) { REPLAY.show[box.dataset.replayShow] = box.checked; drawReplay(); }
  });
  const box = overlay.querySelector("[data-replay-chart]");
  REPLAY.chart = LightweightCharts.createChart(box, { ...baseChartOptions(true),
    localization: { locale: "en-US", priceFormatter: (p) => p.toFixed(2),
      timeFormatter: (t) => new Date(t * 1000).toLocaleString("en-US", { timeZone: "UTC", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 6 } });
  REPLAY.series = REPLAY.chart.addCandlestickSeries({ upColor: CC().up, downColor: CC().down, borderVisible: false,
    wickUpColor: CC().up, wickDownColor: CC().down, priceLineVisible: false,
    // keep visible FVG / level / trade lines on screen
    autoscaleInfoProvider: (original) => {
      const r = original();
      if (!r || !REPLAY.linePrices.length) return r;
      return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...REPLAY.linePrices),
                                    maxValue: Math.max(r.priceRange.maxValue, ...REPLAY.linePrices) } };
    } });
  drawReplay(true);
  overlay.querySelector("[data-replay-step='1']").focus();
}

function closeReplay() {
  clearInterval(REPLAY.timer); REPLAY.timer = null;
  if (REPLAY.chart) { try { REPLAY.chart.remove(); } catch (e) { /* gone */ } }
  REPLAY.chart = null;
  document.querySelector(".rp-replay")?.remove();
}

function replayClick(event) {
  const t = event.target;
  if (t.closest("[data-replay-close]") || t.classList.contains("rp-replay")) { closeReplay(); return; }
  const step = t.closest("[data-replay-step]");
  if (step) { stopReplay(); moveReplay(Number(step.dataset.replayStep)); return; }
  if (t.closest("[data-replay-play]")) { REPLAY.timer ? stopReplay() : startReplay(); return; }
  const speed = t.closest("[data-replay-speed]");
  if (speed) {
    REPLAY.speed = Number(speed.dataset.replaySpeed);
    document.querySelectorAll(".rp-speed").forEach((b) => b.classList.toggle("on", b === speed));
    if (REPLAY.timer) { stopReplay(); startReplay(); }
  }
}

function startReplay() {
  REPLAY.timer = setInterval(() => { if (!moveReplay(1)) stopReplay(); }, 1000 / REPLAY.speed);
  document.querySelector("[data-replay-play]").innerHTML = `${icon("close", 12)}Pause`;
}
function stopReplay() {
  clearInterval(REPLAY.timer); REPLAY.timer = null;
  const b = document.querySelector("[data-replay-play]");
  if (b) b.innerHTML = `${icon("replay", 12)}Play`;
}
function moveReplay(delta) {
  const next = Math.min(REPLAY.data.candles.length - 1, Math.max(0, REPLAY.cursor + delta));
  if (next === REPLAY.cursor) return false;
  REPLAY.cursor = next;
  drawReplay();
  return true;
}

function drawReplay(fit = false) {
  const d = REPLAY.data;
  const visible = d.candles.slice(0, REPLAY.cursor + 1);
  const now = visible[visible.length - 1].close_t;
  const toX = (closeTs) => {   // the chart time of the candle that closes at (or first after) a moment
    const c = d.candles.find((k) => k.close_t >= closeTs) || d.candles[d.candles.length - 1];
    return c.x;
  };
  REPLAY.series.setData(visible.map((c) => ({ time: c.x, open: c.o, high: c.h, low: c.l, close: c.c,
    ...(c.suspect ? { color: CC().suspect, wickColor: CC().suspect } : {}) })));
  for (const line of REPLAY.lines) REPLAY.series.removePriceLine(line);
  REPLAY.lines = [];
  REPLAY.linePrices = [];
  const add = (price, title, color, style = 2) => {
    REPLAY.linePrices.push(price);
    REPLAY.lines.push(REPLAY.series.createPriceLine({ price, title, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true }));
  };
  if (REPLAY.show.levels) for (const l of d.levels) if (l.available_from <= now && now < l.valid_until) add(l.price, l.kind.toUpperCase(), CC().level);
  if (REPLAY.show.zones) for (const z of d.zones) if (z.created_at <= now && (z.end_at == null || now < z.end_at)) {
    const c = z.direction === "bullish" ? CC().upZone : CC().downZone;
    add(z.top, `${z.tf} FVG top`, c, 0); add(z.bottom, `${z.tf} FVG bottom`, c, 0);
  }
  const tr = d.trade;
  const markers = [];
  if (tr && REPLAY.show.trade && tr.entry_time <= now) {
    add(tr.entry, "Entry", CC().ink, 0);
    if (tr.stop != null) add(tr.stop, "Stop", CC().down);
    if (tr.target != null) add(tr.target, "Target", CC().up);
    markers.push({ time: toX(tr.entry_time), position: tr.direction === "bullish" ? "belowBar" : "aboveBar", color: CC().ink, shape: tr.direction === "bullish" ? "arrowUp" : "arrowDown", text: "Entry" });
    if (tr.exit_time <= now) markers.push({ time: toX(tr.exit_time), position: "aboveBar", color: tr.points >= 0 ? CC().up : CC().down, shape: "circle", text: `Exit ${tr.reason}` });
  }
  for (const sw of d.sweeps) if (sw.confirmed_at <= now) markers.push({ time: toX(sw.confirmed_at), position: "belowBar", color: CC().blue, shape: "circle", text: "Sweep" });
  markers.sort((a, b) => a.time - b.time);
  REPLAY.series.setMarkers(markers);
  REPLAY.chart.priceScale("right").applyOptions({ autoScale: true });
  if (fit) REPLAY.chart.timeScale().fitContent();
  const checks = document.querySelector("[data-replay-checks]");
  checks.innerHTML = d.conditions.map((c) => {
    const known = c.known_at <= now;
    return `<li class="${known ? "ok" : ""} ${c.letter ? "" : "eligible"}">${known ? "✓" : "○"} ${escapeHtml(c.label)}</li>`;
  }).join("");
  document.querySelector("[data-replay-log]").innerHTML = d.setup.timeline.filter((r) => r.time <= now)
    .map((r) => `<li><span>${escapeHtml(r.time_label.split(" ").slice(-2).join(" "))}</span>${escapeHtml(r.label)}</li>`).join("");
  document.querySelector("[data-replay-now]").textContent = new Date(visible[visible.length - 1].x * 1000).toLocaleString("en-US", { timeZone: "UTC", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }) + " ET";
  const box = document.querySelector("[data-replay-trade]");
  box.innerHTML = !tr ? `<p class="rp-fine">No trade was taken for this setup with the selected combination.</p>`
    : tr.exit_time <= now ? `<p><b class="${tr.points >= 0 ? "pos" : "neg"}">${tr.r_net != null ? fmtR(tr.r_net) : fmtR(tr.net, "$")}</b> · ${escapeHtml(tr.reason)} after ${tr.minutes} min</p>`
    : tr.entry_time <= now ? `<p>In the trade since ${escapeHtml(tr.entry_label.split(" ").slice(-2).join(" "))}. Result hidden until it ends.</p>`
    : `<p class="rp-fine">Not in a trade yet.</p>`;
}

document.addEventListener("keydown", (event) => {
  const overlay = document.querySelector(".rp-replay");
  if (!overlay || !overlay.getClientRects().length) return;
  // Never swallow keys meant for a text field (space, arrows) — typing must always work
  if (event.target.closest?.("input, textarea, select, [contenteditable='true']")) return;
  if (event.key === "Escape") { event.stopPropagation(); closeReplay(); }
  if (event.key === "ArrowRight") { event.preventDefault(); stopReplay(); moveReplay(1); }
  if (event.key === "ArrowLeft") { event.preventDefault(); stopReplay(); moveReplay(-1); }
  if (event.key === " ") { event.preventDefault(); REPLAY.timer ? stopReplay() : startReplay(); }
}, true);
