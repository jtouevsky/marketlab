// =========================================================
// describe.js — Describe My Trade (quick formalize) and the research
// tools around results: funnel, why matched / why not, browsing winners
// AND losers, "was my trade just luck?", variations and version comparison.
//
// Everything here edits the SAME project structure as Manual mode and runs
// the SAME deterministic engine. AI is optional and never calculates.
// =========================================================

const DMT = { busy: false, browse: "all", openWhy: new Set(), whyNot: null, compare: null, compareSel: new Set(["current"]),
              variation: { kind: "timeframe" }, ai: {}, personalFor: null, quickPreview: null, newKind: "quick", aiBusy: false };
const DMT_ROLE_JUMP = { MARKET: null, DIRECTION: "hypothesis", CONTEXT: "hypothesis", PREREQUISITE: "hypothesis", SETUP: "hypothesis",
                        FILTERS: "hypothesis", CONFIRMATION: "hypothesis", ENTRY: "ideas", STOP: "ideas", TARGET: "ideas", TIME: "settings" };
const DMT_PLACEHOLDER = "Describe exactly how you would take this trade…\n\nFor example: On NQ, I'm looking for a bullish 1H FVG. I want sell-side liquidity such as ONL or PDL swept first. During NY morning, after bullish displacement and a market structure break, if a 5m FVG forms with the 1H direction and price enters/rejects it, enter long. Stop below the sweep low, target nearest major buy-side liquidity, no entries after 11:30 ET.";

const dmtDraftKey = (id) => `marketlab-describe-draft-${id}`;
function dmtLoadDraft(id) { try { return localStorage.getItem(dmtDraftKey(id)); } catch (e) { return null; } }
function dmtSaveDraft(id, text) { try { if (text) localStorage.setItem(dmtDraftKey(id), text); else localStorage.removeItem(dmtDraftKey(id)); } catch (e) { /* storage unavailable */ } }

function statusChip(status) {
  if (!status) return "";
  const help = { IDEA: "Only the idea so far", DEFINING: "Rules still being defined", READY: "Every rule is deterministic; ready to test",
                 TESTED: "Tested with the current rules", REFINING: "Several rule sets tested" }[status] || "";
  return `<span class="rp-pstatus s-${status.toLowerCase()}" data-tip="${escapeHtml(help)}">${escapeHtml(status)}</span>`;
}

function modeToggleHtml(p) {
  const b = (mode, label, sub) => `<button class="rp-mode ${p.mode === mode ? "on" : ""}" data-dmt-mode="${mode}" aria-pressed="${p.mode === mode}"><b>${label}</b><small>${sub}</small></button>`;
  return `<div class="rp-modes" role="group" aria-label="How to define the strategy">${b("quick", "Quick formalize", "Describe the trade; MarketLab structures it")}${b("manual", "Manual", "Define every concept and rule yourself")}</div>`;
}

// ---------------------------------------------------------------- the Describe section
function describeSectionHtml(p) {
  const td = p.trade_description || {};
  const f = p.formalization;
  const latest = (td.revisions || []).length ? td.revisions[td.revisions.length - 1].text : td.original || "";
  const draft = dmtLoadDraft(p.id);
  const value = draft != null ? draft : latest;
  const unsaved = draft != null && draft.trim() && draft.trim() !== (latest || "").trim();
  return `
    ${p.kind === "trade_check" ? actualTradeHtml(p) : ""}
    ${td.original ? `<blockquote class="rp-quote">${escapeHtml(td.original)}<footer>ORIGINAL DESCRIPTION · saved ${escapeHtml(fmtDateTime(td.saved_at))} · never changed</footer></blockquote>
      ${(td.revisions || []).map((r, i) => `<blockquote class="rp-quote rev">${escapeHtml(r.text)}<footer>Revised wording ${i + 1} · ${escapeHtml(fmtDateTime(r.saved_at))}</footer></blockquote>`).join("")}` : ""}
    <div class="dmt-input">
      <label class="rp-big-label" for="dmt-text">${td.original ? "Change the wording" : "Describe exactly how you would take this trade"}</label>
      <textarea id="dmt-text" class="rp-obs-input" data-dmt-text rows="${td.original ? 4 : 7}" maxlength="12000" placeholder="${escapeHtml(DMT_PLACEHOLDER)}">${escapeHtml(value)}</textarea>
      <div class="dmt-input-foot">
        <button class="run-btn ${DMT.busy ? "loading" : ""}" data-dmt-parse ${DMT.busy ? "disabled" : ""}>${td.original ? "Save wording and turn it into rules" : "Turn it into rules"} <span class="arrow">→</span></button>
        <span class="rp-fine" data-dmt-draft-state>${unsaved ? "Unsaved changes (kept as a draft on this computer)" : td.original ? "Any length is fine. The original above is never changed; a new wording is added beside it." : "Any length is fine. Saved exactly as written."}</span>
        <span class="rp-spacer"></span>
        <button class="link-btn" data-dmt-ai="wording" data-tip="Optional. AI suggests clearer wording; you decide whether to use it. It never calculates results.">${icon("ask", 13)}Suggest testable wording</button>
      </div>
      ${DMT.ai.wording ? `<div class="dmt-ai glass-flat"><p>${escapeHtml(DMT.ai.wording.text)}</p><p class="rp-fine">${escapeHtml(DMT.ai.wording.note || "")}</p>
        <button class="pill-btn" data-dmt-use-ai>Put this in the box</button> <button class="link-btn" data-dmt-close-ai="wording">Dismiss</button></div>` : ""}
    </div>
    ${f ? formalizationHtml(p, f) : ""}`;
}

function formalizationHtml(p, f) {
  const openQ = f.ambiguities.filter((a) => !a.answer);
  const openU = f.unsupported.filter((u) => !u.answer);
  const notices = [];
  if (f.description_changed) notices.push(`<div class="dmt-notice">${icon("info", 14)}<span>The latest wording hasn't been turned into rules yet.</span><button class="pill-btn" data-dmt-reparse>Re-parse the latest wording</button></div>`);
  if (f.diverged) notices.push(`<div class="dmt-notice">${icon("warn", 14)}<span><b>Edited by hand.</b> The rules no longer match what was parsed from the description. That's fine; re-parsing would first save the current rules as a version.</span></div>`);
  return `${notices.join("")}
    <div class="dmt-grid">
      <div class="dmt-recipe glass-flat">
        <div class="dmt-card-head"><b>Trade recipe</b><span class="rp-fine">The final reading of your description. Click a line to edit it.</span></div>
        ${f.recipe.filter((r) => r.values.length || r.pending || ["ENTRY", "STOP", "TARGET"].includes(r.key)).map((r) => `
          <div class="dmt-row ${r.pending ? "pending" : ""}">
            <span class="dmt-key">${escapeHtml(r.key)}</span>
            <span class="dmt-vals">${r.values.length ? r.values.map((v) => `<span>${escapeHtml(v)}</span>`).join("") : `<i>${r.pending ? "needs an answer below" : "—"}</i>`}</span>
            ${DMT_ROLE_JUMP[r.key] ? `<button class="small-btn" data-rp-jump="${DMT_ROLE_JUMP[r.key]}" aria-label="Edit ${escapeHtml(r.key)}">${icon("edit", 12)}</button>` : ""}
          </div>`).join("")}
        ${f.personal_used && f.personal_used.length ? `<p class="rp-fine">Using your saved definitions: ${f.personal_used.map((n) => `<b>${escapeHtml(n)}</b>`).join(", ")}.</p>` : ""}
      </div>
      <div class="dmt-side">
        ${strategyStatusHtml(p, f, openQ, openU)}
        <div class="dmt-timeline glass-flat"><div class="dmt-card-head"><b>Timeline</b><span class="rp-fine">What must happen, in order</span></div>
          <ol>${f.timeline.map((s) => `<li class="role-${s.role}"><span class="dmt-step">${s.step}</span><div><span class="dmt-role">${escapeHtml(s.role)}</span>${escapeHtml(s.text)}${s.known ? `<small>${escapeHtml(s.known)}</small>` : ""}</div></li>`).join("")}</ol></div>
      </div>
    </div>
    ${openQ.length || f.ambiguities.length ? questionsBlockHtml(f) : ""}
    ${f.unsupported.length ? unsupportedHtml(f) : ""}
    ${f.notes && f.notes.length ? `<details class="dmt-notes"><summary>${f.notes.length} interpretation${f.notes.length === 1 ? "" : "s"} made from your words</summary><ul>${f.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul></details>` : ""}`;
}

function strategyStatusHtml(p, f, openQ, openU) {
  const ready = f.status.ready && !openQ.length && !openU.length;
  const rows = f.status.checks.map((c) => `<li class="${c.ok ? "ok" : ""}">${c.ok ? icon("check", 13) : "○"} ${escapeHtml(c.label)}</li>`).join("");
  return `<div class="dmt-status glass-flat ${ready ? "ready" : ""}">
    <div class="dmt-card-head"><b>Strategy status</b>${ready ? `<span class="dmt-ready">READY TO TEST</span>` : `<span class="dmt-notready">Not ready</span>`}</div>
    <ul>${rows}
      ${openQ.length ? `<li class="warn">${icon("warn", 13)} ${openQ.length} meaningful ambiguit${openQ.length === 1 ? "y" : "ies"} to answer</li>` : `<li class="ok">${icon("check", 13)} No open ambiguities</li>`}
      ${openU.length ? `<li class="warn">${icon("warn", 13)} ${openU.length} concept${openU.length === 1 ? "" : "s"} MarketLab can't measure yet</li>` : ""}
    </ul>
    ${ready ? `<button class="pill-btn primary-action" data-rp-jump="experiments">Go to testing ${icon("chevron-down", 12)}</button>` : ""}
  </div>`;
}

function questionsBlockHtml(f) {
  const open = f.ambiguities.filter((a) => !a.answer);
  const withSuggestion = open.filter((a) => a.suggested);
  return `<div class="dmt-questions">
    <div class="dmt-card-head"><b>Only the questions that change the results</b>
      ${withSuggestion.length ? `<button class="pill-btn" data-dmt-suggested>Use the common reading for ${withSuggestion.length === open.length ? "all" : withSuggestion.length} open question${withSuggestion.length === 1 ? "" : "s"}</button>` : ""}</div>
    ${f.ambiguities.map((a) => `<div class="dmt-q ${a.answer ? "answered" : ""}">
      <div class="dmt-q-head"><span class="dmt-q-label">${escapeHtml(a.label)}</span>${a.answer ? `<span class="rp-chip">${icon("check", 11)} answered</span>` : ""}
        <span class="rp-spacer"></span><button class="link-btn" data-dmt-ai="ambiguity" data-id="${escapeHtml(a.id)}">${icon("ask", 12)}Explain</button></div>
      <p class="dmt-q-text">${escapeHtml(a.question)}</p>
      ${a.why ? `<p class="rp-fine">${escapeHtml(a.why)}</p>` : ""}
      <div class="dmt-options">${a.options.map((o) => `<button class="dmt-opt ${a.answer === o.value ? "on" : ""}" data-dmt-answer="${escapeHtml(a.id)}" data-value="${escapeHtml(o.value)}">
        ${escapeHtml(o.label)}${a.suggested === o.value ? `<small>common reading</small>` : ""}</button>`).join("")}</div>
      ${DMT.ai[`ambiguity:${a.id}`] ? `<div class="dmt-ai"><p>${escapeHtml(DMT.ai[`ambiguity:${a.id}`].text)}</p><p class="rp-fine">AI explanation; it doesn't choose for you.</p></div>` : ""}
    </div>`).join("")}
  </div>`;
}

function unsupportedHtml(f) {
  return `<div class="dmt-questions unsupported">
    <div class="dmt-card-head"><b>Recognised, but not measurable yet</b><span class="rp-fine">Never dropped silently: choose what to do with each.</span></div>
    ${f.unsupported.map((u) => `<div class="dmt-q ${u.answer ? "answered" : ""}">
      <div class="dmt-q-head"><span class="dmt-q-label">${escapeHtml(u.label)}</span><span class="rp-phrase">“${escapeHtml(u.phrase)}”</span></div>
      <p class="rp-fine">Needs ${escapeHtml(u.needs)}.${u.nearest ? ` Nearest measurable: ${escapeHtml(u.nearest)}.` : ""}</p>
      <div class="dmt-options">${u.options.map((o) => `<button class="dmt-opt ${u.answer === o.value ? "on" : ""}" data-dmt-answer="${escapeHtml(u.id)}" data-value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</button>`).join("")}</div>
    </div>`).join("")}</div>`;
}

// ---------------------------------------------------------------- was my trade just luck?
function actualTradeHtml(p) {
  const a = p.actual_trade || {};
  const v = (k, d = "") => escapeHtml(a[k] != null ? String(a[k]) : d);
  return `<div class="dmt-actual glass-flat">
    <div class="dmt-card-head"><b>Your trade</b><span class="rp-fine">The trade you actually took. Its reasoning goes in the description below.</span>
      ${a.result_r != null ? `<span class="rp-r ${a.result_r >= 0 ? "up" : "down"}">${a.result_r >= 0 ? "+" : ""}${a.result_r.toFixed(2)}R</span>` : ""}</div>
    <div class="rp-settings">
      <label class="rp-param small"><span>Date</span><input type="date" data-dmt-actual="date" value="${v("date")}"></label>
      <label class="rp-param small"><span>Entry time (ET)</span><input type="time" data-dmt-actual="time" value="${v("time")}"></label>
      <label class="rp-param small"><span>Direction</span><select data-dmt-actual="direction"><option value="long" ${a.direction !== "short" ? "selected" : ""}>Long</option><option value="short" ${a.direction === "short" ? "selected" : ""}>Short</option></select></label>
      <label class="rp-param small"><span>Entry price</span><input type="number" step="0.25" data-dmt-actual="entry" value="${v("entry")}"></label>
      <label class="rp-param small"><span>Stop price</span><input type="number" step="0.25" data-dmt-actual="stop" value="${v("stop")}"></label>
      <label class="rp-param small"><span>Exit price</span><input type="number" step="0.25" data-dmt-actual="exit" value="${v("exit")}"></label>
    </div>
    <button class="pill-btn" data-dmt-save-actual>${icon("save", 12)}Save my trade</button>
    <span class="rp-fine">Result in R = (exit − entry) ÷ (entry − stop), for a short the other way round.</span>
  </div>`;
}

function luckHtml(r) {
  const L = r.luck;
  if (!L) return "";
  const counts = Object.fromEntries((L.histogram || []).map(([b, n]) => [Number(b), n]));
  const mineBin = L.your_r != null ? Math.max(-3, Math.min(5, Math.round(L.your_r * 2) / 2)) : null;
  const keys = Object.keys(counts).map(Number).concat(mineBin != null ? [mineBin] : []);
  const lo = Math.min(-1.5, ...keys), hi = Math.max(2, ...keys);
  const max = Math.max(1, ...Object.values(counts));
  let bars = "";
  for (let b = lo; b <= hi + 1e-9; b += 0.5) {
    const n = counts[b] || 0;
    const mine = mineBin === b;
    bars += `<div class="dmt-hbar ${mine ? "mine" : ""} ${n ? "" : "empty"}" data-tip="${b >= 0 ? "+" : ""}${b}R: ${n} historical trade${n === 1 ? "" : "s"}${mine ? " · your trade is here" : ""}">
      <i style="height:${n ? Math.max(6, Math.round(100 * n / max)) : 0}%"></i>${mine ? `<em>you</em>` : ""}${Number.isInteger(b) ? `<span>${b}R</span>` : ""}</div>`;
  }
  return `<div class="dmt-luck glass-flat">
    <div class="dmt-card-head"><b>Was my trade just luck?</b><span class="rp-fine">Your trade vs. every historical setup that matched the same rules</span></div>
    <div class="dmt-luck-grid">
      <div class="rp-tile"><span class="label">Your trade</span><b>${fmtR(L.your_r)}</b><span>${escapeHtml(L.actual?.date || "")} ${escapeHtml(L.actual?.time || "")} ET</span></div>
      <div class="rp-tile"><span class="label">Historical matches</span><b>${L.n}</b><span>${L.n ? `win rate ${pct(L.win_rate)}` : "none"}</span></div>
      ${L.n ? `<div class="rp-tile"><span class="label">Mean / median</span><b>${fmtR(L.mean)} / ${fmtR(L.median)}</b><span>net of costs</span></div>
      <div class="rp-tile"><span class="label">Your result beats</span><b>${L.better_than} of ${L.n}</b><span>${Math.round(100 * L.percentile)}th percentile</span></div>` : ""}
    </div>
    ${L.n ? `<div class="dmt-hist" aria-label="Distribution of historical results in R">${bars}</div>` : ""}
    <p class="dmt-luck-text">${escapeHtml(L.text)}</p>
    <p class="rp-fine">${L.matched_setup ? `MarketLab's rules also found a setup at ${escapeHtml(L.matched_setup.time_label)}, close to your entry.` :
      L.in_data ? "MarketLab's rules did not find a setup within 30 minutes of your entry — use “Why wasn't this a match?” below to see which condition failed." :
      "Your trade is outside the dataset's dates, so it can't be checked against the rules directly."}
      An unusual outcome is not proof of luck or skill; it says how this one trade compares with the rule's history.</p>
  </div>`;
}

// ---------------------------------------------------------------- results extras
function funnelHtml(r) {
  if (!r.funnel || !r.funnel.length) return "";
  const top = Math.max(1, r.funnel[0].remaining);
  return `<details class="dmt-funnel" ${r.setups === 0 ? "open" : ""}><summary>How the ${r.triggers} trigger candidates became ${r.setups} setup${r.setups === 1 ? "" : "s"}</summary>
    <ol>${r.funnel.map((s) => `<li><span class="dmt-fbar"><i style="width:${Math.max(1, Math.round(100 * s.remaining / top))}%"></i></span>
      <b>${s.remaining}</b><span>${s.letter ? `${escapeHtml(s.letter)}: ` : ""}${escapeHtml(s.name)}${s.requirement ? ` — ${escapeHtml(s.requirement)}` : ""}${s.trigger ? " (trigger)" : ""}</span></li>`).join("")}</ol>
    <p class="rp-fine">Each line keeps only the candidates that also satisfy that condition (checked with what was known at the time). The biggest drop shows which rule is the most restrictive.</p></details>`;
}

function browseHtml(r, unit) {
  const trades = r.primary.trades.filter((t) => !t.suspect);
  const key = unit === "R" ? "r_net" : "net";
  const sets = {
    best: trades.slice().sort((a, b) => b[key] - a[key]).slice(0, 15),
    worst: trades.slice().sort((a, b) => a[key] - b[key]).slice(0, 15),
    recent: trades.slice().sort((a, b) => b.entry_time - a.entry_time).slice(0, 15),
    all: r.primary.trades.slice(0, RP.showAllTrades ? 400 : 40),
  };
  const tab = (id, label) => `<button class="rp-tab ${DMT.browse === id ? "on" : ""}" data-dmt-browse="${id}">${label}</button>`;
  const list = sets[DMT.browse] || sets.all;
  return `<h4 class="rp-h4">Matching trades</h4>
    <div class="dmt-browse-tabs">${tab("best", "Best")}${tab("worst", "Worst")}${tab("recent", "Recent")}${tab("all", `All ${r.primary.trades.length}`)}</div>
    ${DMT.browse === "worst" ? `<p class="rp-fine">Setups that matched every rule and lost the most. Look at these before trusting the averages.</p>` : ""}
    ${list.length ? dmtTradesTable(list, unit) : `<p class="rp-fine">No trades.</p>`}
    ${DMT.browse === "all" && r.primary.trades.length > 40 && !RP.showAllTrades ? `<button class="link-btn" data-rp-more-trades>Show all ${r.primary.trades.length}</button>` : ""}`;
}

function dmtTradesTable(trades, unit) {
  return `<div class="rp-trades-wrap"><table class="rp-trades"><thead><tr><th>Entry (ET)</th><th>Dir.</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit</th><th>Why it ended</th>
    <th>Result</th><th>MFE / MAE</th><th>Min</th><th></th></tr></thead><tbody>${trades.map((t) => {
      const v = unit === "R" ? t.r_net : t.net;
      const open = DMT.openWhy.has(t.setup_time);
      return `<tr class="${t.suspect ? "suspect" : ""}"><td>${escapeHtml(t.entry_label)}${t.contract ? `<small class="dmt-contract">${escapeHtml(t.contract)}</small>` : ""}</td><td>${t.direction === "bullish" ? "Long" : "Short"}</td>
      <td>${t.entry.toFixed(2)}</td><td>${escapeHtml(t.stop_text || "—")}</td><td>${t.target != null ? t.target.toFixed(2) : "—"}</td><td>${t.exit.toFixed(2)}</td>
      <td>${escapeHtml(t.reason)}</td><td class="${v >= 0 ? "pos" : "neg"}">${fmtR(v, unit)}</td>
      <td>${unit === "R" ? `${(t.mfe_r ?? 0).toFixed(2)} / ${(t.mae_r ?? 0).toFixed(2)}R` : `${t.mfe_points} / ${t.mae_points} pts`}</td><td>${t.minutes}</td>
      <td class="rp-actions-cell"><button class="small-btn ${open ? "on" : ""}" data-dmt-why="${t.setup_time}" aria-expanded="${open}">Why</button>
        <button class="small-btn" data-rp-replay="${t.setup_time}">${icon("replay", 12)}Replay</button>
        <button class="small-btn" data-rp-save-trade="${t.setup_time}" data-tip="Save as an example in this project">${icon("save", 12)}</button></td></tr>
      ${open ? `<tr class="dmt-why-row"><td colspan="11"><b>Why this matched</b><ul>${(t.why || []).map((w) => `<li>${icon("check", 12)} ${w.time_label ? `<span class="dmt-at">${escapeHtml(w.time_label)}</span>` : ""}${escapeHtml(w.text)}</li>`).join("")}</ul></td></tr>` : ""}`;
    }).join("")}</tbody></table></div>`;
}

function whyNotHtml() {
  const w = DMT.whyNot;
  return `<div class="dmt-whynot glass-flat">
    <div class="dmt-card-head"><b>Why wasn't this a match?</b><span class="rp-fine">Pick a New York date and time; every condition is checked with what was known then.</span></div>
    <div class="rp-settings"><label class="rp-param small"><span>Date</span><input type="date" data-dmt-wn="date" value="${escapeHtml(w?.date || "")}"></label>
      <label class="rp-param small"><span>Time (ET)</span><input type="time" data-dmt-wn="time" value="${escapeHtml(w?.time || "")}"></label>
      <button class="pill-btn" data-dmt-whynot>Check</button></div>
    ${w && w.result ? `<p class="rp-fine">Evaluated at ${escapeHtml(w.result.evaluated_label)}${w.result.evaluated_at !== w.result.time ? " (the latest trigger in the hour before the time you picked)" : ""}.</p>
      <ul class="dmt-checks">${w.result.rows.map((row) => `<li class="${row.ok ? "ok" : "no"}">${row.ok ? icon("check", 13) : icon("close", 13)} ${row.letter ? `<b>${escapeHtml(row.letter)}</b> ` : ""}${escapeHtml(row.text)}</li>`).join("")}</ul>
      <p class="${w.result.matched ? "dmt-ok" : "rp-fine"}">${w.result.matched ? "Every condition holds here: this is a match." : "At least one condition fails, so it isn't a match."}</p>` : w && w.error ? `<p class="rp-error">${escapeHtml(w.error)}</p>` : ""}
  </div>`;
}

function quickNoteHtml() {
  return `<div class="dmt-quicknote"><input data-dmt-quicknote maxlength="2000" placeholder="Note something about these results (saved to Notes)…"><button class="pill-btn" data-dmt-add-note>${icon("plus", 12)}Add note</button></div>`;
}

function variationHtml(p) {
  const v = DMT.variation;
  const defs = p.definitions;
  const tfDefs = defs.filter((d) => d.params.timeframe);
  const numberParams = (d) => {
    const c = conceptOf(d.concept);
    return Object.entries(c.params).filter(([n, s]) => (s.kind === "number" || s.kind === "choice") && n !== "timeframe");
  };
  let form = "";
  if (v.kind === "timeframe") {
    const lastCond = p.hypothesis.conditions[p.hypothesis.conditions.length - 1];
    const chosen = tfDefs.find((d) => d.id === v.definition_id) || tfDefs.find((d) => lastCond && d.id === lastCond.definition_id) || tfDefs[0];
    if (chosen) v.definition_id = chosen.id;
    const now = chosen ? chosen.params.timeframe : null;
    form = `<label class="rp-param"><span>Definition</span><select data-dmt-var="definition_id">${tfDefs.map((d) => `<option value="${d.id}" ${chosen && chosen.id === d.id ? "selected" : ""}>${escapeHtml(d.name)} (${d.params.timeframe})</option>`).join("")}</select></label>
      <label class="rp-param small"><span>New timeframe</span><select data-dmt-var="value">${RP.meta.timeframes.map((tf) => `<option ${(v.value || now) === tf ? "selected" : ""} ${tf === now ? "disabled" : ""}>${tf}${tf === now ? " (now)" : ""}</option>`).join("")}</select></label>`;
  } else if (v.kind === "execution") {
    const now = (p.timeframes.roles || {}).execution;
    form = `<label class="rp-param small"><span>Execution timeframe</span><select data-dmt-var="value">${["1m", "5m", "15m"].map((tf) => `<option ${(v.value || (now === "1m" ? "5m" : "1m")) === tf ? "selected" : ""} ${tf === now ? "disabled" : ""}>${tf}${tf === now ? " (now)" : ""}</option>`).join("")}</select></label>
      <p class="rp-fine">Entries and exits are simulated on these candles. If no step is finer than 5m, 5m execution lets the test use ≈ 60 days (about 40 trading days) of free history instead of ≈ 29 (less precise inside each candle; stops are still assumed first).</p>`;
  } else if (v.kind === "requirement") {
    const conds = p.hypothesis.conditions;
    const c = conds.find((x) => x.id === v.condition_id) || conds[0];
    if (c) v.condition_id = c.id;
    const d = c && p.definitions.find((x) => x.id === c.definition_id);
    const reqs = d ? (RP.meta.requirements[d.concept] || RP.meta.requirements._default) : [];
    form = `<label class="rp-param"><span>Condition</span><select data-dmt-var="condition_id">${conds.map((x) => { const dd = p.definitions.find((y) => y.id === x.definition_id);
      return `<option value="${x.id}" ${c && c.id === x.id ? "selected" : ""}>${escapeHtml(x.letter)}: ${escapeHtml(dd?.name || "")} — ${escapeHtml(x.requirement)}</option>`; }).join("")}</select></label>
      <label class="rp-param"><span>Change to</span><select data-dmt-var="value">${reqs.filter((r) => r !== c?.requirement).map((r) => `<option value="${escapeHtml(r)}">must be: ${escapeHtml(r)}</option>`).join("")}
        ${["any time earlier in the same session", "since 18:00 ET"].filter((x) => x !== c?.timing).map((x) => `<option value="${escapeHtml(x)}">timing: ${escapeHtml(x)}</option>`).join("")}
        ${[30, 60, 120].map((n) => `<option value="${n}">timing: within the last ${n} minutes</option>`).join("")}</select></label>`;
  } else if (v.kind === "param") {
    const d = defs.find((x) => x.id === v.definition_id) || defs[0];
    const params = d ? numberParams(d) : [];
    const name = v.param && params.some(([n]) => n === v.param) ? v.param : params[0]?.[0];
    const spec = d && name ? conceptOf(d.concept).params[name] : null;
    form = `<label class="rp-param"><span>Definition</span><select data-dmt-var="definition_id">${defs.map((x) => `<option value="${x.id}" ${d && d.id === x.id ? "selected" : ""}>${escapeHtml(x.name)}</option>`).join("")}</select></label>
      ${params.length ? `<label class="rp-param"><span>Setting</span><select data-dmt-var="param">${params.map(([n, s]) => `<option value="${n}" ${n === name ? "selected" : ""}>${escapeHtml(s.label || n)} (now ${escapeHtml(String(d.params[n]))})</option>`).join("")}</select></label>
        ${spec && spec.kind === "choice" ? `<label class="rp-param small"><span>New value</span><select data-dmt-var="value">${spec.options.map((o) => `<option value="${escapeHtml(String(o))}">${escapeHtml(optLabel(o))}</option>`).join("")}</select></label>`
          : `<label class="rp-param small"><span>New value</span><input type="number" step="${spec?.step || 1}" data-dmt-var="value" value="${escapeHtml(String(v.value ?? d.params[name] ?? ""))}"></label>`}` : `<p class="rp-fine">This definition has no adjustable settings.</p>`}`;
  } else if (v.kind === "stop") {
    form = `<label class="rp-param"><span>New stop</span><select data-dmt-var="idea_kind">${Object.entries(RP.meta.stop_kinds).filter(([k]) => k !== "custom" && k !== "opposing_structure").map(([k, l]) => `<option value="${k}" ${v.idea_kind === k ? "selected" : ""}>${escapeHtml(l)}</option>`).join("")}</select></label>
      ${["fixed_points"].includes(v.idea_kind) ? `<label class="rp-param small"><span>Points</span><input type="number" step="0.25" data-dmt-var="points" value="${v.points || 20}"></label>` : ""}
      ${v.idea_kind === "atr" ? `<label class="rp-param small"><span>× ATR</span><input type="number" step="0.1" data-dmt-var="atr_mult" value="${v.atr_mult || 1.5}"></label>` : ""}`;
  } else if (v.kind === "exit") {
    form = `<label class="rp-param"><span>New target</span><select data-dmt-var="idea_kind">${Object.entries(RP.meta.exit_kinds).filter(([k]) => k !== "custom").map(([k, l]) => `<option value="${k}" ${v.idea_kind === k ? "selected" : ""}>${escapeHtml(l)}</option>`).join("")}</select></label>
      ${v.idea_kind === "r_multiple" ? `<label class="rp-param small"><span>R</span><input type="number" step="0.25" data-dmt-var="r" value="${v.r || 2}"></label>` : ""}
      ${v.idea_kind === "fixed_points" ? `<label class="rp-param small"><span>Points</span><input type="number" step="0.25" data-dmt-var="points" value="${v.points || 20}"></label>` : ""}
      ${v.idea_kind === "time" ? `<label class="rp-param small"><span>Minutes</span><input type="number" data-dmt-var="minutes" value="${v.minutes || 60}"></label>` : ""}
      ${v.idea_kind === "structure" ? `<fieldset class="rp-param rp-multi"><legend>Liquidity that counts</legend>${Object.entries(RP.meta.target_pools).map(([k, l]) =>
        `<label class="rp-tick"><input type="checkbox" data-dmt-pool="${k}" ${(v.pools || ["pdhl", "onhl"]).includes(k) ? "checked" : ""}>${escapeHtml(l)}</label>`).join("")}</fieldset>` : ""}`;
  } else {
    form = `<label class="rp-param small"><span>${v.kind === "cutoff" ? "No entries after (ET)" : "Flat by (ET)"}</span><input type="time" data-dmt-var="value" value="${escapeHtml(v.value || "11:00")}"></label>`;
  }
  const kinds = [["timeframe", "Timeframe of a step"], ["param", "A definition's threshold"], ["requirement", "A condition's rule or timing"], ["stop", "Stop"], ["exit", "Target"],
                 ["execution", "Execution timeframe"], ["cutoff", "Entry cutoff time"], ["flat_time", "Flat-by time"]];
  return `<div class="dmt-variation glass-flat">
    <div class="dmt-card-head"><b>Test a variation</b><span class="rp-fine">Saved as a new version branching from the current rules; nothing is overwritten.</span></div>
    <div class="dmt-var-kinds">${kinds.map(([k, l]) => `<button class="dmt-opt ${v.kind === k ? "on" : ""}" data-dmt-var-kind="${k}">${l}</button>`).join("")}</div>
    <div class="rp-settings">${form}</div>
    <button class="run-btn ${DMT.busy ? "loading" : ""}" data-dmt-make-variation ${DMT.busy ? "disabled" : ""}>Save variation and test it <span class="arrow">→</span></button>
    <span class="rp-fine">${(p.test_counts || {}).variations || 0} variations and ${(p.test_counts || {}).rule_sets || 0} rule sets tested so far in this project.</span>
  </div>`;
}

function variantCompareHtml(p) {
  const c = DMT.compare;
  const options = [["current", "Working copy"], ...p.versions.map((v) => [v.id, v.label])];
  return `<div class="dmt-compare glass-flat">
    <div class="dmt-card-head"><b>Compare versions</b><span class="rp-fine">Each version runs on its own data. No winner is declared.</span></div>
    <div class="dmt-var-kinds">${options.map(([id, label]) => `<label class="rp-tick"><input type="checkbox" data-dmt-cmp="${escapeHtml(id)}" ${DMT.compareSel.has(id) ? "checked" : ""}>${escapeHtml(label)}</label>`).join("")}</div>
    <button class="pill-btn ${DMT.busy ? "loading" : ""}" data-dmt-compare ${DMT.busy || DMT.compareSel.size < 1 ? "disabled" : ""}>Compare ${DMT.compareSel.size}</button>
    ${c ? `<div class="rp-trades-wrap"><table class="rp-trades dmt-cmp-table"><thead><tr><th>Version</th><th>Data</th><th>Setups</th><th>Trades</th><th>Win rate</th><th>Median</th><th>Expectancy</th><th>95% interval</th><th>Max drawdown</th></tr></thead>
      <tbody>${c.rows.map((row) => row.ok ? `<tr><td><b>${escapeHtml(row.label)}</b><small class="dmt-contract">${escapeHtml(row.combo)}</small></td><td>${escapeHtml(row.data)}</td><td>${row.setups}</td>
        <td class="${row.n < 30 ? "dmt-small-n" : ""}">${row.n}</td><td>${pct(row.win_rate)}</td><td>${fmtR(row.median, row.unit)}</td><td>${fmtR(row.expectancy, row.unit)}</td>
        <td>${row.ci95 ? `${fmtR(row.ci95[0], row.unit)} to ${fmtR(row.ci95[1], row.unit)}` : "—"}</td><td>${fmtR(row.max_drawdown, row.unit)}</td></tr>`
        : `<tr><td><b>${escapeHtml(row.label)}</b></td><td colspan="8" class="rp-error">${escapeHtml(row.error)}</td></tr>`).join("")}</tbody></table></div>
      <p class="rp-fine">${escapeHtml(c.note)} Trade counts under 30 are highlighted.</p>` : ""}
  </div>`;
}

function dmtResultExtras(r) {
  const p = RP.project;
  return `${funnelHtml(r)}${luckHtml(r)}${quickNoteHtml()}
    <div class="dmt-ai-row"><button class="link-btn" data-dmt-ai="result">${icon("ask", 13)}Explain this result in plain words (AI, optional)</button></div>
    ${DMT.ai.result ? `<div class="dmt-ai glass-flat"><p>${escapeHtml(DMT.ai.result.text)}</p><p class="rp-fine">${escapeHtml(DMT.ai.result.note || "")}</p></div>` : ""}`;
}

function dmtAfterResults(p) {
  return `${whyNotHtml()}${variationHtml(p)}${variantCompareHtml(p)}`;
}

// ---------------------------------------------------------------- dataset extras
function provenanceHtml(d) {
  const pv = d.provenance;
  const cs = d.contract_status;
  const fmt = (ts) => ts ? new Date(ts * 1000).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
  return `${d.base_reason ? `<p class="rp-fine"><b>Base resolution ${escapeHtml(d.base)}:</b> ${escapeHtml(d.base_reason)}</p>` : ""}
    ${cs ? `<p class="rp-fine"><b>${escapeHtml(cs.label)}.</b> ${escapeHtml(cs.why)}${cs.contracts?.length ? ` Contracts: ${cs.contracts.map(escapeHtml).join(", ")}.` : ""}</p>` : ""}
    ${pv ? `<details class="rp-anoms"><summary><span class="rp-chip">Sources · ${pv.segments.length} segment${pv.segments.length === 1 ? "" : "s"}</span> <span class="rp-chip">${pv.rolls.length} roll${pv.rolls.length === 1 ? "" : "s"}</span></summary>
      <table><thead><tr><th>Trading days</th><th>Contract</th><th>Source</th><th>Candles</th><th>Filled from</th></tr></thead><tbody>${pv.segments.map((s) =>
        `<tr><td>${escapeHtml(s.from_day)} → ${escapeHtml(s.to_day)}</td><td>${escapeHtml(s.contract || "not identified")}</td><td>${escapeHtml(s.source)}</td><td>${s.bars.toLocaleString("en-US")}</td>
         <td>${Object.entries(s.filled || {}).map(([k, n]) => `${escapeHtml(k)}: ${n}`).join("<br>") || "—"}</td></tr>`).join("")}</tbody></table>
      <p class="rp-fine"><b>Roll rule.</b> ${escapeHtml(pv.roll_rule)}</p>
      ${pv.rolls.map((r) => `<p class="rp-fine">Rolled ${escapeHtml(r.from)} → ${escapeHtml(r.to)} on ${escapeHtml(r.day)}: ${escapeHtml(r.why)}.</p>`).join("")}
      <table><thead><tr><th>Source</th><th>Status</th><th>Candles</th><th>Range</th></tr></thead><tbody>${pv.sources.map((s) =>
        `<tr><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.status)}${s.why ? `<br><small>${escapeHtml(s.why)}</small>` : ""}</td><td>${s.bars != null ? s.bars.toLocaleString("en-US") : "—"}</td><td>${fmt(s.first)} → ${fmt(s.last)}</td></tr>`).join("")}</tbody></table>
      ${pv.limitations.map((x) => `<p class="rp-fine">${icon("info", 12)} ${escapeHtml(x)}</p>`).join("")}
      ${(pv.sources || []).some((s) => s.attribution) ? `<p class="rp-fine">Includes data from FirstRate Data (free public sample, private use).</p>` : ""}</details>` : ""}
    ${d.depth ? `<details class="rp-anoms"><summary><span class="rp-chip">What's free, at which resolution</span></summary><table><thead><tr><th>Resolution</th><th>Free without an account?</th><th>History</th><th></th></tr></thead><tbody>${d.depth.map((x) =>
      `<tr><td>${escapeHtml(x.resolution)}</td><td>${x.free ? "Yes" : "No"}</td><td>${escapeHtml(x.depth || "—")}</td><td>${escapeHtml(x.note || "")}</td></tr>`).join("")}</tbody></table>
      ${d.cache && d.cache.length ? `<p class="rp-fine">Local cache: ${d.cache.filter((c) => c.provider_key !== "auto").map((c) => `${escapeHtml(c.contract || c.instrument)} ${escapeHtml(c.resolution)} (${(c.bars || 0).toLocaleString("en-US")})`).join(" · ")}. Only new candles are downloaded next time.</p>` : ""}</details>` : ""}`;
}

// ---------------------------------------------------------------- personal definitions
function personalFormHtml(d) {
  if (DMT.personalFor !== d.id) return `<button class="small-btn" data-dmt-personal="${d.id}" data-tip="Reuse this exact definition in future descriptions">${icon("save", 12)}Save as my definition</button>`;
  const c = conceptOf(d.concept);
  return `<div class="dmt-personal"><input data-dmt-personal-name maxlength="60" value="My ${escapeHtml(c?.label || d.concept)}">
    <label class="rp-tick"><input type="checkbox" data-dmt-personal-auto checked>Use automatically when I describe a ${escapeHtml((c?.label || d.concept).toLowerCase())}</label>
    <button class="pill-btn" data-dmt-personal-save="${d.id}">Save</button><button class="link-btn" data-dmt-personal-cancel>Cancel</button></div>`;
}

async function openPersonal() {
  const data = await fetchJson("/api/research/personal-definitions").catch(() => ({ personal: [] }));
  openDrawer(`<h3 class="rp-drawer-title">My definitions</h3>
    <p class="rp-fine">Saved from your projects. A definition set to “automatic” replaces the library default whenever you describe that concept; writing its name (e.g. “my displacement”) always uses it.</p>
    ${data.personal.length ? data.personal.map((d) => `<details class="rp-lib-item"><summary><b>${escapeHtml(d.name)}</b> <span class="rp-fine">${escapeHtml(conceptOf(d.concept)?.label || d.concept)}${d.auto ? " · automatic" : ""}</span></summary>
      <ol class="rp-explain">${d.explanation.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ol>
      <button class="small-btn" data-dmt-personal-del="${d.id}">${icon("trash", 12)}Delete</button></details>`).join("") : `<p>No saved definitions yet. Use “Save as my definition” on any definition in a project.</p>`}`,
    { label: "My definitions" });
}

// ---------------------------------------------------------------- new project (quick / luck)
function newQuickHtml() {
  const luck = DMT.newKind === "luck";
  const prev = DMT.quickPreview;
  return `
    <nav class="rp-crumbs"><button class="link-btn" data-rp-home>Research projects</button><span>›</span><span>${luck ? "Was my trade just luck?" : "Describe a trade"}</span></nav>
    <div class="rp-new glass">
      <label class="rp-big-label" for="dmt-new-text">${luck ? "Why did you take this trade? Describe the reasoning as rules." : "Describe exactly how you would take this trade"}</label>
      <textarea id="dmt-new-text" class="rp-obs-input" data-dmt-new-text rows="8" maxlength="12000" placeholder="${escapeHtml(DMT_PLACEHOLDER)}"></textarea>
      <p class="rp-fine">As detailed as you like. Saved exactly as written; MarketLab turns it into explicit rules and asks only what changes the results.</p>
      <div class="dmt-preview" data-dmt-preview>${prev ? previewHtml(prev) : ""}</div>
      <div class="rp-new-row">
        <label>Project name<input data-rp-name maxlength="120" placeholder="${luck ? "e.g. My NQ long on Sep 30" : "e.g. NQ 1H FVG + sweep continuation"}"></label>
        <label>Instrument<select data-rp-inst>${Object.entries(RP.meta.instruments).map(([s, i]) =>
          `<option value="${s}" ${i.available ? "" : "disabled"} ${s === "NQ" ? "selected" : ""}>${s} · ${escapeHtml(i.label)}${i.available ? "" : " (later)"}</option>`).join("")}</select></label>
      </div>
      ${luck ? `<p class="rp-fine">After creating the project, enter the trade's date, time and prices at the top.</p>` : ""}
      <div class="rp-new-foot">
        <button class="run-btn ${DMT.busy ? "loading" : ""}" data-dmt-create ${DMT.busy ? "disabled" : ""}>Create and turn into rules <span class="arrow">→</span></button>
        <button class="link-btn" data-rp-home>Cancel</button>
      </div>
    </div>`;
}

function previewHtml(d) {
  const open = d.ambiguities.filter((a) => !a.answer).length + d.unsupported.length;
  return `<div class="dmt-prev-row">${d.recipe.filter((r) => r.values.length && !["TIME"].includes(r.key) || ["CONTEXT", "SETUP"].includes(r.key) && r.values.length).slice(0, 7).map((r) =>
    `<span class="dmt-prev-item"><b>${escapeHtml(r.key)}</b> ${escapeHtml(r.values.join(" · "))}</span>`).join("")}
    ${open ? `<span class="rp-chip open">${open} question${open === 1 ? "" : "s"} to answer next</span>` : ""}</div>`;
}

// ---------------------------------------------------------------- events
let dmtPreviewTimer = null;

function dmtInput(event) {
  const t = event.target;
  if (t.matches("[data-dmt-text]") && RP.project) {
    dmtSaveDraft(RP.project.id, t.value);
    const state = labState.el.querySelector("[data-dmt-draft-state]");
    if (state) state.textContent = "Draft saved on this computer — not turned into rules yet";
    return true;
  }
  if (t.matches("[data-dmt-new-text]")) {
    clearTimeout(dmtPreviewTimer);
    dmtPreviewTimer = setTimeout(async () => {
      const box = labState.el.querySelector("[data-dmt-preview]");
      if (!box) return;
      if (!t.value.trim()) { box.innerHTML = ""; DMT.quickPreview = null; return; }
      try { DMT.quickPreview = await postJson("/api/research/parse", { text: t.value }); box.innerHTML = previewHtml(DMT.quickPreview); } catch (e) { /* preview only */ }
    }, 450);
    return true;
  }
  return false;
}

function dmtChange(event) {
  const t = event.target;
  if (t.matches("[data-dmt-var]")) {
    DMT.variation[t.dataset.dmtVar] = t.value;
    if (["definition_id", "param", "idea_kind", "condition_id"].includes(t.dataset.dmtVar)) { if (t.dataset.dmtVar !== "param") delete DMT.variation.param; redrawPart("[data-dmt-after]", () => dmtAfterResults(RP.project)); }
    return true;
  }
  if (t.matches("[data-dmt-pool]")) {
    DMT.variation.pools = [...labState.el.querySelectorAll("[data-dmt-pool]:checked")].map((x) => x.dataset.dmtPool);
    return true;
  }
  if (t.matches("[data-dmt-cmp]")) {
    if (t.checked) DMT.compareSel.add(t.dataset.dmtCmp); else DMT.compareSel.delete(t.dataset.dmtCmp);
    redrawPart("[data-dmt-after]", () => dmtAfterResults(RP.project));
    return true;
  }
  return false;
}

async function dmtBusy(fn) {
  if (DMT.busy) return null;
  DMT.busy = true;
  drawResearch();
  try { return await fn(); } catch (error) { showToast(error.message); return null; } finally { DMT.busy = false; drawResearch(); }
}

async function dmtClick(event) {
  const t = event.target;
  const el = (sel) => t.closest(sel);
  let hit;
  if (el("[data-rp-new-quick]")) { DMT.newKind = "quick"; DMT.quickPreview = null; RP.view = "new-quick"; drawResearch({ keepScroll: false }); labState.el.querySelector("[data-dmt-new-text]")?.focus(); return true; }
  if (el("[data-rp-new-luck]")) { DMT.newKind = "luck"; DMT.quickPreview = null; RP.view = "new-quick"; drawResearch({ keepScroll: false }); labState.el.querySelector("[data-dmt-new-text]")?.focus(); return true; }
  if (el("[data-dmt-my-defs]")) { await openPersonal(); return true; }
  if ((hit = el("[data-dmt-personal-del]"))) {
    await postJson(`/api/research/personal-definitions/${hit.dataset.dmtPersonalDel}`, undefined, "DELETE").catch((e) => showToast(e.message));
    await openPersonal(); return true;
  }
  if (el("[data-dmt-create]")) {
    const root = labState.el;
    const text = root.querySelector("[data-dmt-new-text]").value.trim();
    if (!text) { showToast("Describe the trade first."); return true; }
    const name = root.querySelector("[data-rp-name]").value;
    const instrument = root.querySelector("[data-rp-inst]").value;
    const created = await dmtBusy(async () => {
      const p = await postJson("/api/research/projects", { name: name || (DMT.newKind === "luck" ? "Was my trade just luck?" : ""), instrument,
                                                           kind: DMT.newKind === "luck" ? "trade_check" : "research" });
      return postJson(`/api/research/projects/${p.id}/describe`, { text });
    });
    if (created) {
      resetProjectState();
      RP.project = created; RP.view = "project";
      drawResearch({ keepScroll: false }); loadDataset();
    }
    return true;
  }
  if (!RP.project) return false;
  const p = RP.project;
  if ((hit = el("[data-dmt-mode]"))) {
    const out = await rpCall(rpUrl("/mode"), { mode: hit.dataset.dmtMode });
    if (out) drawResearch();
    return true;
  }
  if (el("[data-dmt-parse]")) {
    const text = labState.el.querySelector("[data-dmt-text]").value.trim();
    if (!text) { showToast("Describe the trade first."); return true; }
    const out = await dmtBusy(() => postJson(rpUrl("/describe"), { text }));
    if (out) { RP.project = out; dmtSaveDraft(p.id, null); RP.result = null; drawResearch(); showToast(out.formalization?.status?.ready ? "Ready to test" : "Turned into rules — a few questions left"); }
    return true;
  }
  if (el("[data-dmt-reparse]")) {
    const out = await dmtBusy(() => postJson(rpUrl("/reparse"), {}));
    if (out) { RP.project = out; RP.result = null; drawResearch(); }
    return true;
  }
  if ((hit = el("[data-dmt-answer]"))) {
    const id = hit.dataset.dmtAnswer;
    const current = [...p.formalization.ambiguities, ...p.formalization.unsupported].find((a) => a.id === id);
    const value = current && current.answer === hit.dataset.value ? "" : hit.dataset.value;
    const out = await dmtBusy(() => postJson(rpUrl("/answer"), { answers: { [id]: value } }));
    if (out) { RP.project = out; RP.result = null; drawResearch(); }
    return true;
  }
  if (el("[data-dmt-suggested]")) {
    const out = await dmtBusy(() => postJson(rpUrl("/answer"), { use_suggested: true }));
    if (out) { RP.project = out; RP.result = null; drawResearch(); }
    return true;
  }
  if (el("[data-dmt-save-actual]")) {
    const body = {};
    labState.el.querySelectorAll("[data-dmt-actual]").forEach((x) => { body[x.dataset.dmtActual] = x.value; });
    const out = await dmtBusy(() => postJson(rpUrl("/actual-trade"), body));
    if (out) { RP.project = out; drawResearch(); showToast("Trade saved. Run the test to compare it with history."); }
    return true;
  }
  if ((hit = el("[data-dmt-browse]"))) { DMT.browse = hit.dataset.dmtBrowse; drawResearch(); return true; }
  if ((hit = el("[data-dmt-why]"))) {
    const k = Number(hit.dataset.dmtWhy);
    if (DMT.openWhy.has(k)) DMT.openWhy.delete(k); else DMT.openWhy.add(k);
    drawResearch(); return true;
  }
  if (el("[data-dmt-whynot]")) {
    const date = labState.el.querySelector('[data-dmt-wn="date"]').value;
    const time = labState.el.querySelector('[data-dmt-wn="time"]').value;
    DMT.whyNot = { date, time };
    try { DMT.whyNot.result = await postJson(rpUrl("/why-not"), { date, time }); } catch (error) { DMT.whyNot.error = error.message; }
    redrawPart("[data-dmt-after]", () => dmtAfterResults(RP.project));
    return true;
  }
  if (el("[data-dmt-add-note]")) {
    const input = labState.el.querySelector("[data-dmt-quicknote]");
    if (!input.value.trim()) return true;
    const ctx = RP.result ? ` (test of rules ${RP.result.rules_hash}, ${RP.result.primary.cell.label}: ${RP.result.primary.metrics.n || 0} trades)` : "";
    if (await rpCall(rpUrl("/items/notes"), { text: input.value + ctx })) { showToast("Saved to Notes"); input.value = ""; }
    return true;
  }
  if ((hit = el("[data-dmt-var-kind]"))) {
    DMT.variation = { kind: hit.dataset.dmtVarKind };
    redrawPart("[data-dmt-after]", () => dmtAfterResults(RP.project));
    return true;
  }
  if (el("[data-dmt-make-variation]")) {
    const v = DMT.variation;
    const body = { kind: v.kind };
    if (v.kind === "timeframe" || v.kind === "param") {
      body.definition_id = v.definition_id || (v.kind === "timeframe" ? p.definitions.find((d) => d.params.timeframe)?.id : p.definitions[0]?.id);
      const d = p.definitions.find((x) => x.id === body.definition_id);
      if (v.kind === "param") {
        body.param = v.param || (d ? Object.entries(conceptOf(d.concept).params).find(([n, s]) => (s.kind === "number" || s.kind === "choice") && n !== "timeframe")?.[0] : null);
        const input = labState.el.querySelector('[data-dmt-var="value"]');
        body.value = input ? (input.type === "number" ? Number(input.value) : input.value) : v.value;
      } else body.value = v.value || labState.el.querySelector('[data-dmt-var="value"]')?.value;
    } else if (v.kind === "requirement") {
      body.condition_id = v.condition_id;
      body.value = labState.el.querySelector('[data-dmt-var="value"]')?.value;
    } else if (v.kind === "stop" || v.kind === "exit") {
      const kindSel = labState.el.querySelector('[data-dmt-var="idea_kind"]');
      const kind = v.idea_kind || kindSel?.value;
      const params = {};
      ["points", "r", "minutes", "atr_mult"].forEach((n) => { const x = labState.el.querySelector(`[data-dmt-var="${n}"]`); if (x) params[n] = Number(x.value); });
      if (kind === "structure") { params.target = "nearest opposing liquidity"; params.pools = v.pools || ["pdhl", "onhl"]; }
      body.idea = { kind, params };
    } else body.value = labState.el.querySelector('[data-dmt-var="value"]')?.value;
    const out = await dmtBusy(() => postJson(rpUrl("/variation"), body));
    if (out) { RP.project = out; RP.result = null; showToast(`Saved ${out.versions[out.versions.length - 1].label}`); runTest(); }
    return true;
  }
  if (el("[data-dmt-compare]")) {
    const ids = [...DMT.compareSel];
    const out = await dmtBusy(() => postJson(rpUrl("/compare"), { version_ids: ids }));
    if (out) DMT.compare = out;
    drawResearch();
    return true;
  }
  if ((hit = el("[data-dmt-ai]"))) {
    if (DMT.aiBusy) return true;
    const action = hit.dataset.dmtAi;
    DMT.aiBusy = true;
    showToast("Asking Claude…");
    try {
      const out = await postJson(rpUrl("/ai"), { action, id: hit.dataset.id });
      DMT.ai[action === "ambiguity" ? `ambiguity:${hit.dataset.id}` : action] = out;
      drawResearch();
    } catch (error) { showToast(error.message); }
    DMT.aiBusy = false;
    return true;
  }
  if (el("[data-dmt-use-ai]")) {
    const box = labState.el.querySelector("[data-dmt-text]");
    if (box && DMT.ai.wording) { box.value = DMT.ai.wording.text; dmtSaveDraft(p.id, box.value); }
    return true;
  }
  if ((hit = el("[data-dmt-close-ai]"))) { delete DMT.ai[hit.dataset.dmtCloseAi]; drawResearch(); return true; }
  if ((hit = el("[data-dmt-personal]"))) { DMT.personalFor = hit.dataset.dmtPersonal; drawResearch(); return true; }
  if (el("[data-dmt-personal-cancel]")) { DMT.personalFor = null; drawResearch(); return true; }
  if ((hit = el("[data-dmt-personal-save]"))) {
    const d = p.definitions.find((x) => x.id === hit.dataset.dmtPersonalSave);
    const name = labState.el.querySelector("[data-dmt-personal-name]").value;
    const auto = labState.el.querySelector("[data-dmt-personal-auto]").checked;
    try {
      await postJson("/api/research/personal-definitions", { name, concept: d.concept, params: d.params, auto });
      showToast(`Saved “${name}”. Future descriptions will ${auto ? "use it automatically" : "use it when you name it"}.`);
      DMT.personalFor = null; drawResearch();
    } catch (error) { showToast(error.message); }
    return true;
  }
  return false;
}

function dmtResetProject() {
  Object.assign(DMT, { browse: "all", openWhy: new Set(), whyNot: null, compare: null, compareSel: new Set(["current"]),
                       variation: { kind: "timeframe" }, ai: {}, personalFor: null });
}
