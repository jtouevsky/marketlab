// =========================================================
// notebook.js — the Notebook environment: saved research.
// Each entry keeps its exact ExperimentDefinition, the result summary,
// Challenge findings, notes, its parent (forks) and earlier versions.
// Data: /api/notebook (notebook.py → notebook/experiments.json)
// =========================================================

let notebookDirty = true;
let notebookEntries = [];
const notebookUi = { selected: null, confirmDelete: null, busy: {}, editingNotes: false };

async function showNotebook(force = false) {
  const body = document.querySelector("#notebook-env .env-body");
  if (!notebookDirty && !force) return drawNotebook();
  if (!notebookEntries.length) body.innerHTML = '<div class="skeleton"><span class="label">Loading your notebook…</span><div class="sk big"></div><div class="sk"></div></div>';
  try {
    notebookEntries = (await fetchJson("/api/notebook")).experiments;
    notebookDirty = false;
  } catch (error) {
    body.innerHTML = `<div class="inline-error">${escapeHtml(error.message)}<br><button class="pill-btn" data-nb-retry>Try again</button></div>`;
    return;
  }
  drawNotebook();
}

function shortConditions(def) {
  return def.conditions.map((c) => conditionText(c, def.instrument.symbol).replace(def.instrument.symbol + " ", "")).join(" + ");
}

function drawNotebook() {
  const body = document.querySelector("#notebook-env .env-body");
  const head = `
    <div class="env-head">
      <div class="lab-kicker">${icon("notebook", 18)} Notebook</div>
      <h1>Your research</h1>
      <p class="aside">Every saved experiment keeps its exact definition and results, so you can reopen it, re-run it on new data, or fork it into a new hypothesis.</p>
    </div>`;
  if (!notebookEntries.length) {
    body.innerHTML = head + `
      <div class="empty-note">
        <p><b>No experiments saved yet.</b> Run an experiment in the Lab and save anything worth revisiting.</p>
        <button class="action-btn glass-flat" data-env-go="lab">${icon("lab", 16)}Open Lab</button>
      </div>`;
    return;
  }
  const byId = new Map(notebookEntries.map((e) => [e.id, e]));
  const roots = notebookEntries.filter((e) => !e.parent_id || !byId.has(e.parent_id));
  const branch = (entry, depth) => cardHtml(entry, depth, byId) +
    notebookEntries.filter((c) => c.parent_id === entry.id).sort((a, b) => a.created_at.localeCompare(b.created_at)).map((c) => branch(c, depth + 1)).join("");
  const selected = notebookUi.selected && byId.get(notebookUi.selected);
  body.innerHTML = head + `
    <div class="nb-layout ${selected ? "with-detail" : ""}">
      <div class="nb-list">${roots.map((r) => branch(r, 0)).join("")}</div>
      ${selected ? `<aside class="nb-detail glass-flat" aria-label="Experiment details">${detailHtml(selected, byId)}</aside>` : ""}
    </div>
    <p class="data-foot">Saved in notebook/experiments.json inside the MarketLab folder. Forks sit under the experiment they came from.</p>`;
}

function cardHtml(e, depth, byId) {
  const d = e.definition;
  const s = e.summary;
  const delta = s && isNum(s.mean) && isNum(s.baseline_mean) ? s.mean - s.baseline_mean : null;
  const ch = e.challenge;
  return `
    <article class="nb-card ${notebookUi.selected === e.id ? "on" : ""}" style="--depth:${depth}" data-entry="${e.id}" tabindex="0" role="button" aria-label="Open details for ${escapeHtml(e.name)}">
      ${depth ? '<span class="nb-branch" aria-hidden="true"></span>' : ""}
      <div class="nb-title">${logoHtml(d.instrument.symbol, "", 22)}<h3>${escapeHtml(e.name)}</h3>
        ${e.version > 1 ? `<span class="chip-flat" data-tip="${e.version - 1} earlier version${e.version > 2 ? "s" : ""} kept">v${e.version}</span>` : ""}
        ${e.parent_id ? `<span class="chip-flat" data-tip="Forked from “${escapeHtml(byId.get(e.parent_id)?.name || "a deleted entry")}”">${icon("fork", 12)} fork</span>` : ""}</div>
      <p class="nb-when"><b>${escapeHtml(d.instrument.symbol)}</b> · ${escapeHtml(shortConditions(d))} <span class="na">→ next ${d.outcome.horizon}d</span></p>
      ${s && s.n ? `<div class="nb-stats">
          <span><small>n</small>${s.n}</span>
          <span><small>mean</small><b class="${signClass(s.mean)}">${fmtPct(s.mean, true, 2)}</b></span>
          <span><small>median</small><b class="${signClass(s.median)}">${fmtPct(s.median, true, 2)}</b></span>
          ${isNum(delta) ? `<span><small>vs normal</small><span class="${signClass(delta)}">${delta >= 0 ? "+" : "−"}${Math.abs(delta * 100).toFixed(2)} pts</span></span>` : ""}
          ${ch ? `<span><small>challenge</small><span class="up">✓ ${ch.survived.length}</span> <span class="warn-text">⚠ ${ch.caution.length}</span></span>` : ""}
        </div>` : `<div class="nb-stats"><span class="na">${s ? "No episodes on the last run." : "Not run since it was changed."}</span></div>`}
    </article>`;
}

function detailHtml(e, byId) {
  const d = e.definition, s = e.summary;
  const busy = notebookUi.busy[e.id];
  const children = notebookEntries.filter((c) => c.parent_id === e.id);
  return `
    <div class="nb-detail-head"><h3>${escapeHtml(e.name)}</h3>
      <button class="icon-btn" data-nb="close" aria-label="Close details">${icon("close", 15)}</button></div>
    <div class="nb-actions">
      <button class="pill-btn active" data-nb="open">${icon("lab", 14)}Open in Lab</button>
      <button class="pill-btn ${busy === "rerun" ? "busy" : ""}" data-nb="rerun">${icon("rerun", 14)}${busy === "rerun" ? "Re-running…" : "Re-run on latest data"}</button>
      <button class="pill-btn" data-nb="fork" data-tip="A child experiment: change one thing and compare">${icon("fork", 14)}Fork</button>
      <button class="pill-btn" data-nb="duplicate">${icon("duplicate", 14)}Duplicate</button>
      <button class="pill-btn ${notebookUi.confirmDelete === e.id ? "danger" : ""}" data-nb="delete">${icon("trash", 14)}${notebookUi.confirmDelete === e.id ? "Click again to delete" : "Delete"}</button>
    </div>
    <div class="label">Definition</div>
    <p class="nb-def">When <b>${escapeHtml(d.instrument.symbol)}</b> ${escapeHtml(d.conditions.map((c) => conditionText(c, d.instrument.symbol).replace(d.instrument.symbol + " ", "")).join(" AND "))}, what happens over the next ${d.outcome.horizon} trading days?</p>
    ${e.hypothesis ? `<div class="label">Hypothesis</div><p class="nb-hyp">${escapeHtml(e.hypothesis)}</p>` : ""}
    ${s && s.n ? `<div class="label">Result (${s.tested_from ? `${fmtDay(s.tested_from)} – ${fmtDay(s.tested_to)}` : ""})</div>
      <dl class="facts one-col">
        <div class="fact"><dt>Episodes</dt><dd>${s.n}</dd></div>
        <div class="fact"><dt>Mean · median</dt><dd>${fmtPct(s.mean, true, 2)} · ${fmtPct(s.median, true, 2)}</dd></div>
        ${isNum(s.trimmed_mean) ? `<div class="fact"><dt>Trimmed mean</dt><dd>${fmtPct(s.trimmed_mean, true, 2)}</dd></div>` : ""}
        <div class="fact"><dt>Positive</dt><dd>${fmtPct(s.positive_rate, false, 0)}</dd></div>
        ${s.ci && s.ci[0] !== null ? `<div class="fact"><dt>95% interval</dt><dd>${fmtPct(s.ci[0], true, 2)} to ${fmtPct(s.ci[1], true, 2)}</dd></div>` : ""}
        ${isNum(s.baseline_mean) ? `<div class="fact"><dt>Normal period</dt><dd>${fmtPct(s.baseline_mean, true, 2)}</dd></div>` : ""}
      </dl>` : ""}
    ${e.challenge ? `<div class="label">Challenge findings</div>
      <ul class="mini-checks">${e.challenge.survived.map((t) => `<li class="ok">✓ ${escapeHtml(t)}</li>`).join("")}${e.challenge.caution.map((t) => `<li class="bad">⚠ ${escapeHtml(t)}</li>`).join("")}</ul>` : ""}
    <div class="label">Notes</div>
    <textarea class="nb-notes-edit" data-nb-notes="${e.id}" rows="4" placeholder="What did you learn? What would you test next?">${escapeHtml(e.notes || "")}</textarea>
    <div class="nb-meta">Created ${fmtDay(e.created_at.slice(0, 10))}${e.last_run_at ? ` · last run ${fmtRelative(e.last_run_at)}` : ""}
      ${e.parent_id ? ` · forked from <button class="link-btn small" data-nb-select="${e.parent_id}">${escapeHtml(byId.get(e.parent_id)?.name || "a deleted entry")}</button>` : ""}
      ${children.length ? ` · ${children.length} fork${children.length === 1 ? "" : "s"}` : ""}</div>
    ${(e.versions || []).length ? `<div class="label" style="margin-top:16px">Earlier versions</div>
      <div class="versions">${[...e.versions].reverse().map((v, i) => `
        <div class="version-row"><span>v${v.version}</span><span class="na">${fmtRelative(v.saved_at)}</span>
          <span>${v.summary && v.summary.n ? `n ${v.summary.n} · mean ${stripTags(fmtPct(v.summary.mean, true, 2))}` : "not run"}</span>
          <button class="link-btn small" data-nb-version="${e.versions.length - 1 - i}">Open in Lab</button></div>`).join("")}</div>` : ""}`;
}

document.getElementById("notebook-env").addEventListener("click", async (event) => {
  if (event.target.closest("[data-nb-retry]")) { showNotebook(true); return; }
  const select = event.target.closest("[data-nb-select]");
  if (select) { notebookUi.selected = select.dataset.nbSelect; drawNotebook(); writeHash(); return; }
  const card = event.target.closest(".nb-card");
  if (card) { notebookUi.selected = notebookUi.selected === card.dataset.entry ? null : card.dataset.entry; notebookUi.confirmDelete = null; drawNotebook(); writeHash(); return; }
  const versionBtn = event.target.closest("[data-nb-version]");
  const entry = notebookEntries.find((e) => e.id === notebookUi.selected);
  if (versionBtn && entry) {
    const v = entry.versions[Number(versionBtn.dataset.nbVersion)];
    openEntryInLab({ ...entry, definition: v.definition }, { note: `version ${v.version}` });
    return;
  }
  const button = event.target.closest("[data-nb]");
  if (!button || !entry) return;
  const action = button.dataset.nb;
  const id = entry.id;
  if (action !== "delete") notebookUi.confirmDelete = null;
  try {
    if (action === "close") { notebookUi.selected = null; drawNotebook(); writeHash(); }
    else if (action === "open") openEntryInLab(entry);
    else if (action === "rerun") {
      notebookUi.busy[id] = "rerun"; drawNotebook();
      await postJson(`/api/notebook/${id}/rerun`);
      delete notebookUi.busy[id];
      await showNotebook(true);
      showToast(`Re-ran “${entry.name}” on the latest data (the previous result is kept as a version).`);
    } else if (action === "fork") {
      const child = await postJson(`/api/notebook/${id}/fork`, {});
      notebookDirty = true;
      openEntryInLab(child);
      showToast("Forked. Change one thing, run it, then save to update the fork.");
    } else if (action === "duplicate") {
      await postJson("/api/notebook", { name: `${entry.name} (copy)`, hypothesis: entry.hypothesis, notes: entry.notes, definition: entry.definition, summary: entry.summary, challenge: entry.challenge });
      await showNotebook(true);
    } else if (action === "delete") {
      if (notebookUi.confirmDelete !== id) { notebookUi.confirmDelete = id; drawNotebook(); return; }
      notebookUi.confirmDelete = null;
      await postJson(`/api/notebook/${id}`, undefined, "DELETE");
      if (labState.entry?.id === id) { labState.entry = null; saveLabState(); }
      notebookUi.selected = null;
      await showNotebook(true);
    }
  } catch (error) {
    delete notebookUi.busy[id];
    showToast(error.message);
    drawNotebook();
  }
});

// Notes save automatically when you leave the box
document.getElementById("notebook-env").addEventListener("focusout", async (event) => {
  const box = event.target.closest("[data-nb-notes]");
  if (!box) return;
  const entry = notebookEntries.find((e) => e.id === box.dataset.nbNotes);
  if (!entry || (entry.notes || "") === box.value) return;
  try {
    const updated = await postJson(`/api/notebook/${entry.id}`, { notes: box.value }, "PATCH");
    entry.notes = updated.notes;
    showToast("Notes saved.");
  } catch (error) { showToast(error.message); }
});

document.getElementById("notebook-env").addEventListener("keydown", (event) => {
  if ((event.key === "Enter" || event.key === " ") && event.target.matches(".nb-card")) { event.preventDefault(); event.target.click(); }
});

function openEntryInLab(entry, { note = null } = {}) {
  labState.previous = null;
  labState.def = JSON.parse(JSON.stringify(entry.definition));
  labState.entry = { id: entry.id, name: entry.name, hypothesis: entry.hypothesis, notes: entry.notes, version: entry.version };
  labState.result = null;
  labState.challenge = null;
  labState.origin = note ? { ticker: entry.definition.instrument.symbol, from: `Notebook · ${note}` } : null;
  labState.mode = "quick";
  switchEnv("lab");
  renderLab(labState);
  labState.rendered = true;
  saveLabState();
  runExperiment(labState);
}
