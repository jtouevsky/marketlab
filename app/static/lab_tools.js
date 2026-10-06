// =========================================================
// lab_tools.js — everything AROUND an experiment result:
//   sample-size preview · origin banner · breadcrumbs
//   Challenge this result · episode inspector · methodology
//   Save to Notebook (versions / forks) · Test elsewhere
// The engine (experiments.py, challenge.py) does every calculation;
// this file only displays what it returns.
// =========================================================

// ---------- condition list helpers ----------
function newConditionId(lab) {
  const used = new Set(lab.def.conditions.map((c) => c.id));
  let i = lab.def.conditions.length + 1;
  while (used.has(`c${i}`)) i++;
  return `c${i}`;
}

function conditionsChanged(lab) {
  closeTray(lab);
  drawSentence(lab);
  saveLabState();
  schedulePreview(lab);
  lab.challenge = null;
  if (lab.result) {
    clearTimeout(lab.timer);
    lab.timer = setTimeout(() => runExperiment(lab, { quick: true }), 350);
  }
}


// ---------- Sample-size preview: how many episodes survive each added condition ----------
function schedulePreview(lab, delay = 450) {
  clearTimeout(lab.previewTimer);
  lab.previewTimer = setTimeout(() => loadPreview(lab), delay);
}

async function loadPreview(lab) {
  const holder = lab.el.querySelector("[data-preview]");
  if (!holder) return;
  const token = (lab.previewToken || 0) + 1;
  lab.previewToken = token;
  holder.classList.add("busy");
  let data;
  try {
    data = await postJson("/api/lab/preview", lab.def);
  } catch (error) {
    if (lab.previewToken === token) { holder.classList.remove("busy"); holder.innerHTML = `<span class="na">${escapeHtml(error.message)}</span>`; }
    return;
  }
  if (lab.previewToken !== token) return;
  holder.classList.remove("busy");
  const steps = data.steps;
  const last = steps[steps.length - 1];
  holder.innerHTML = `
    <span class="label">Sample</span>
    ${steps.map((st, i) => `${i ? '<span class="arrow-sep">→</span>' : ""}<span class="pv-step ${st.episodes < 10 ? "tiny" : st.episodes < 30 ? "small" : ""}"
       data-tip="${st.qualifying_days.toLocaleString("en-US")} qualifying days → ${st.episodes} non-overlapping episodes with ${st.conditions} condition${st.conditions === 1 ? "" : "s"}">
       <small>${st.conditions === 1 ? "1 condition" : `+ condition ${st.conditions}`}</small>n ≈ ${st.episodes.toLocaleString("en-US")}</span>`).join("")}
    ${last.episodes < 30 ? `<span class="pv-warn">${last.episodes < 10 ? "Very few episodes: results will be dominated by chance." : "Under 30 episodes: substantial sampling uncertainty."}</span>` : ""}`;
}


// ---------- Where this experiment came from (Research → Lab) ----------
function drawOrigin(lab) {
  const holder = lab.el.querySelector("[data-origin]");
  if (!holder) return;
  if (!lab.origin) { holder.innerHTML = ""; return; }
  holder.innerHTML = `
    <div class="origin-bar">${icon("research", 15)} Started from <b>${escapeHtml(lab.origin.ticker)} · ${escapeHtml(lab.origin.from)}</b>
      ${lab.origin.note ? `<span class="na">${escapeHtml(lab.origin.note)}</span>` : ""}
      ${lab.previous ? '<button class="link-btn" data-restore-previous>Restore previous experiment</button>' : ""}</div>`;
}

function restorePrevious(lab) {
  if (!lab.previous) return;
  Object.assign(lab, { def: lab.previous.def, result: lab.previous.result, entry: lab.previous.entry, origin: null, previous: null, challenge: null });
  saveLabState();
  renderLab(lab);
}


// ---------- Breadcrumbs: Lab › experiment › Challenge › Nearby parameters ----------
const CRUMB_LABELS = { result: "Result", challenge: "Challenge", "ch-outliers": "Outliers", "ch-time": "Time split",
                       "ch-recency": "Recent vs full", "ch-neighbors": "Parameter sensitivity" };

function experimentName(lab) {
  if (lab.entry) return lab.entry.name;
  const d = lab.def;
  return `${d.instrument.symbol} · ${d.conditions.length} condition${d.conditions.length === 1 ? "" : "s"} → ${d.outcome.horizon}d`;
}

function drawCrumbs(lab) {
  const nav = lab.el.querySelector("[data-crumbs]");
  if (!nav) return;
  if (!lab.result) { nav.innerHTML = ""; return; }
  const trail = ["result"];
  if (lab.challenge) trail.push("challenge");
  if (lab.crumb && lab.crumb.startsWith("ch-")) trail.push(lab.crumb);
  nav.innerHTML = `<button class="crumb" data-crumb="top">Lab</button><span>›</span>
    <button class="crumb" data-crumb="result">${escapeHtml(experimentName(lab))}</button>
    ${trail.slice(1).map((c) => `<span>›</span><button class="crumb ${c === lab.crumb ? "on" : ""}" data-crumb="${c}">${CRUMB_LABELS[c]}</button>`).join("")}`;
}

function goCrumb(lab, id) {
  const body = lab.el.querySelector(".window-body");
  if (id === "top") { body.scrollTo({ top: 0, behavior: "smooth" }); return; }
  const target = id === "result" ? lab.el.querySelector("#lab-result") : lab.el.querySelector(`#${id}`);
  lab.crumb = id;
  drawCrumbs(lab);
  if (target) body.scrollTo({ top: target.offsetTop - 70, behavior: "smooth" });
}


// =========================================================
// Challenge this result
// =========================================================
async function runChallenge(lab) {
  const holder = lab.el.querySelector("[data-challenge-out]");
  if (lab.challenge && lab.challengeKey === JSON.stringify(lab.result.definition)) { goCrumb(lab, "challenge"); return; }
  holder.innerHTML = `<div class="sequence" id="challenge"><div class="seq-line active">Trying to break this result: removing extremes, splitting history, checking recent years, testing nearby parameters…</div></div>`;
  goCrumb(lab, "challenge");
  try {
    lab.challenge = await postJson("/api/lab/challenge", lab.result.definition);
    lab.challengeKey = JSON.stringify(lab.result.definition);
  } catch (error) {
    holder.innerHTML = `<div class="warning" id="challenge">${escapeHtml(error.message)} <button class="link-btn" data-challenge>Try again</button></div>`;
    return;
  }
  renderChallenge(lab);
  lab.crumb = "challenge";
  drawCrumbs(lab);
}

function renderChallenge(lab) {
  const c = lab.challenge;
  const holder = lab.el.querySelector("[data-challenge-out]");
  if (!holder || !c) return;
  if (!c.available) { holder.innerHTML = `<div class="warning" id="challenge">${escapeHtml(c.reason)}</div>`; return; }
  const H = c.definition.outcome.horizon;
  const check = (x, ok) => `<li>${ok ? `<span class="chk ok">${icon("check", 14)}</span>` : `<span class="chk bad">${icon("warn", 14)}</span>`}
      <div><span>${escapeHtml(x.text)}</span> <button class="link-btn small" data-calc aria-expanded="false">Show calculation</button><p class="calc" hidden>${escapeHtml(x.calc)}</p></div></li>`;

  holder.innerHTML = `
    <section class="section challenge" id="challenge">
      <header class="section-head"><span class="idx">${icon("challenge", 16)}</span><h3>Challenge this result</h3><span class="source">deterministic tests · no score</span></header>
      <p class="aside">Each test asks a question a skeptic would ask. Nothing is combined into a score: one serious caution can matter more than several passes.</p>
      <div class="challenge-summary">
        <div><span class="label">What survived</span><ul class="checks">${c.summary.survived.map((x) => check(x, true)).join("") || '<li class="na">Nothing yet.</li>'}</ul></div>
        <div><span class="label">What should make you cautious</span><ul class="checks">${c.summary.caution.map((x) => check(x, false)).join("") || '<li class="na">No cautions from these tests.</li>'}</ul></div>
      </div>

      <div class="ch-test" id="ch-outliers">
        <h4>1 · Does it survive without the extremes?</h4>
        ${outlierTable(c.outliers)}
      </div>

      <div class="ch-test" id="ch-time">
        <h4>2 · Earlier vs later episodes</h4>
        ${c.time_split.available ? periodCards(c.time_split.parts, H) : `<p class="slot-msg">${escapeHtml(c.time_split.reason)}</p>`}
        <p class="note">The episodes are split into two halves with equal counts (not equal years), so neither half is starved of data. Each half is compared with normal ${H}-day returns from its own years.</p>
      </div>

      <div class="ch-test" id="ch-recency">
        <h4>3 · Recent years vs the full history</h4>
        ${c.recency.available ? periodTable(c.recency.parts) : '<p class="slot-msg">The history is too short to compare recent years separately.</p>'}
      </div>

      <div class="ch-test" id="ch-neighbors">
        <h4>4 · Small changes to the parameters</h4>
        ${c.neighbors.available ? neighborGrid(c.neighbors, H) : `<p class="slot-msg">${escapeHtml(c.neighbors.reason)}</p>`}
        <p class="note">Your exact setting is outlined. A broad region of similar results is more convincing than one setting that stands out from its neighbours.
          Nothing here is a significance test, and testing many settings on the same history will always turn up some that look good by chance.</p>
      </div>
    </section>`;
  observeChallengeSections(lab);
}

function outlierTable(o) {
  const vals = o.rows.map((r) => r.value).filter(isNum);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), span = hi - lo || 0.01;
  const pos = (v) => ((v - lo) / span) * 100;
  return `<div class="sens">${o.rows.map((r) => `
      <div class="sens-row"><div class="sens-label">${escapeHtml(r.label)}<small>n = ${r.n}</small></div>
        <div class="sens-value ${signClass(r.value)}">${isNum(r.value) ? fmtPct(r.value, true, 2) : NA}</div>
        <div class="sens-track"><span class="zero" style="left:${pos(0)}%"></span>${isNum(r.value) ? `<span class="dot ${signClass(r.value)}" style="left:${pos(r.value)}%"></span>` : ""}</div></div>`).join("")}</div>
    <p class="note">The most extreme episode (${fmtDay(o.most_extreme.date)}, ${fmtPct(o.most_extreme.value, true, 1)}) is never deleted from the results; these rows show what the average would be without it.</p>`;
}

function periodCards(parts, H) {
  return `<div class="period-cards">${parts.map((p) => `
    <div class="period-card ${p.enough ? "" : "thin"}">
      <span class="label">${escapeHtml(p.label)}</span>
      <div class="period-years">${fmtDay(p.from)} – ${fmtDay(p.to)}</div>
      ${p.stats ? `<div class="period-main ${signClass(p.stats.mean)}">${fmtPct(p.stats.mean, true, 2)}</div>
        <div class="period-sub">median ${stripTags(fmtPct(p.stats.median, true, 2))} · ${fmtPct(p.stats.positive_rate, false, 0)} positive · n = ${p.stats.n}</div>
        <div class="period-sub">normal ${H}-day: ${stripTags(fmtPct(p.baseline?.mean, true, 2))} → <b class="${signClass(p.difference)}">${isNum(p.difference) ? `${p.difference >= 0 ? "+" : "−"}${Math.abs(p.difference * 100).toFixed(2)} pts` : "n/a"}</b></div>
        ${p.enough ? "" : '<div class="period-sub na">Too few episodes to judge.</div>'}` : '<div class="na">No episodes.</div>'}
    </div>`).join("")}</div>`;
}

function periodTable(parts) {
  return `<div class="table-scroll"><table class="data-table">
    <thead><tr><th>Period</th><th>Episodes</th><th>Average</th><th>Median</th><th>Positive</th><th>Normal avg</th><th>Difference</th></tr></thead>
    <tbody>${parts.map((p) => p.stats ? `<tr class="${p.enough ? "" : "thin"}"><td>${escapeHtml(p.label)}<small>${fmtDay(p.from)} –</small></td><td>${p.stats.n}</td>
      <td class="${signClass(p.stats.mean)}">${fmtPct(p.stats.mean, true, 2)}</td><td class="${signClass(p.stats.median)}">${fmtPct(p.stats.median, true, 2)}</td>
      <td>${fmtPct(p.stats.positive_rate, false, 0)}</td><td>${fmtPct(p.baseline?.mean, true, 2)}</td>
      <td class="${signClass(p.difference)}">${isNum(p.difference) ? `${p.difference >= 0 ? "+" : "−"}${Math.abs(p.difference * 100).toFixed(2)} pts` : NA}</td></tr>`
      : `<tr><td>${escapeHtml(p.label)}</td><td colspan="6" class="na">no episodes</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Rows with fewer than 8 episodes are greyed out: too few to judge.</p>`;
}

function neighborGrid(nb, H) {
  const [A, B] = nb.axes;
  const fmtAxis = (axis, v) => `${v}${axis.unit === "%" ? "%" : axis.unit === "×" ? "×" : ""}${axis.unit === "trading days" ? "d" : ""}`;
  const name = (axis) => axis.path === "horizon" ? "forward period" : axis.name;
  return `<div class="table-scroll"><table class="grid-table">
    <thead><tr><th class="corner">${escapeHtml(name(A))} ↓ · ${escapeHtml(name(B))} →</th>${B.values.map((v) => `<th>${fmtAxis(B, v)}</th>`).join("")}</tr></thead>
    <tbody>${nb.rows.map((row, i) => `<tr><th>${fmtAxis(A, A.values[i])}</th>${row.map((cell) => {
      if (!cell.stats) return '<td class="empty">no episodes</td>';
      const d = cell.difference;
      const tone = !isNum(d) ? "" : d > 0 ? "pos" : "neg";
      return `<td class="${tone} ${cell.center ? "center" : ""} ${cell.stats.n < 10 ? "thin" : ""}" data-tip="${escapeHtml(`${name(A)} ${fmtAxis(A, cell.a)}, ${name(B)} ${fmtAxis(B, cell.b)}: average ${stripTags(fmtPct(cell.stats.mean, true, 2))}, median ${stripTags(fmtPct(cell.stats.median, true, 2))}, ${cell.stats.n} episodes, normal ${stripTags(fmtPct(cell.baseline_mean, true, 2))}`)}">
        <b class="${signClass(cell.stats.mean)}">${fmtPct(cell.stats.mean, true, 1)}</b>
        <small>${isNum(d) ? `${d >= 0 ? "+" : "−"}${Math.abs(d * 100).toFixed(1)} vs normal` : ""} · n ${cell.stats.n}</small></td>`;
    }).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function observeChallengeSections(lab) {
  if (lab.challengeObserver) lab.challengeObserver.disconnect();
  const body = lab.el.querySelector(".window-body");
  lab.challengeObserver = new IntersectionObserver((entries) => {
    const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (visible && lab.crumb !== visible.target.id) { lab.crumb = visible.target.id; drawCrumbs(lab); }
  }, { root: body, rootMargin: "-20% 0px -60% 0px" });
  lab.el.querySelectorAll(".ch-test").forEach((el) => lab.challengeObserver.observe(el));
}


// =========================================================
// Episode inspector (drawer)
// =========================================================
function inspectEpisode(lab, index) {
  const r = lab.result;
  const e = r.events[index];
  if (!e || e.status !== "complete") return;
  const H = r.definition.outcome.horizon;
  const bench = r.benchmark?.symbol || "SPY";
  const excess = isNum(e.benchmark_return) ? e.forward_return - e.benchmark_return : null;
  const fmtMeasure = (m) => m.format === "pct" ? fmtPct(m.value, true, 2) : m.format === "x" ? `${isNum(m.value) ? m.value.toFixed(2) : "n/a"}×` : isNum(m.value) ? m.value.toFixed(1) : escapeHtml(String(m.value));
  openDrawer(`
    <div class="insp-date">${fmtDay(e.trigger_date)}</div>
    <p class="na">${escapeHtml(r.ticker)} · trigger day (all conditions true at the close)</p>
    <div class="label" style="margin-top:18px">What the conditions saw</div>
    <dl class="facts one-col">${(e.measures || []).map((m) => `<div class="fact"><dt>${escapeHtml(m.label)}</dt><dd>${fmtMeasure(m)}${m.detail ? `<small>${escapeHtml(m.detail)}</small>` : ""}</dd></div>`).join("") || '<p class="na">No measurements recorded.</p>'}</dl>
    <div class="label" style="margin-top:18px">What happened next · ${H} trading days</div>
    <dl class="facts one-col">
      <div class="fact"><dt>Start (close ${fmtDay(e.trigger_date)})</dt><dd>${e.entry_price.toFixed(2)}</dd></div>
      <div class="fact"><dt>End (close ${fmtDay(e.exit_date)})</dt><dd>${e.exit_price.toFixed(2)}</dd></div>
      <div class="fact"><dt>Forward return</dt><dd class="${signClass(e.forward_return)}"><b>${fmtPct(e.forward_return, true, 2)}</b></dd></div>
      ${isNum(e.benchmark_return) ? `<div class="fact"><dt>${escapeHtml(bench)} over the same days</dt><dd class="${signClass(e.benchmark_return)}">${fmtPct(e.benchmark_return, true, 2)}</dd></div>
        <div class="fact"><dt>Difference vs ${escapeHtml(bench)}</dt><dd class="${signClass(excess)}">${excess >= 0 ? "+" : "−"}${Math.abs(excess * 100).toFixed(2)} pts</dd></div>` : ""}
    </dl>
    ${e.path ? pathSpark(e.path, H) : ""}
    ${e.flags ? `<div class="warning">Data note inside this window: ${escapeHtml(e.flags.join("; "))}. The move was kept because it looks like a real market event; see Data checks.</div>` : ""}
    <p class="note">Prices are adjusted for splits and dividends, so they may not match what was quoted on the day.</p>
    <div class="label" style="margin-top:16px">Context</div>
    <p class="note">News, earnings and market events around historical episodes will appear here as MarketLab's event history grows.</p>
    <button class="action-btn glass-flat" data-insp-chart>${icon("chart", 15)}Open ${escapeHtml(r.ticker)} chart in Research</button>`, { label: "Episode" });
  drawer.querySelector("[data-insp-chart]").addEventListener("click", () => { closeDrawer(); openResearch(r.ticker, "chart"); });
}

function pathSpark(path, H) {
  const W = 300, Hh = 90, lo = Math.min(0, ...path), hi = Math.max(0, ...path), span = hi - lo || 0.01;
  const x = (i) => (i / (path.length - 1)) * W, y = (v) => Hh - 6 - ((v - lo) / span) * (Hh - 12);
  const d = path.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = path[path.length - 1];
  return `<div class="insp-path"><svg viewBox="0 0 ${W} ${Hh}" preserveAspectRatio="none" aria-label="Price path over the ${H} days">
      <line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" class="zero"/><path d="${d}" class="${last >= 0 ? "up" : "down"}"/></svg>
    <div class="series-axis"><span>day 0</span><span>low ${stripTags(fmtPct(Math.min(...path), true, 1))} · high ${stripTags(fmtPct(Math.max(...path), true, 1))}</span><span>day ${H}</span></div></div>`;
}


// =========================================================
// Methodology (drawer)
// =========================================================
function openMethodology(lab) {
  const r = lab.result;
  if (!r) return;
  openDrawer(`
    <h3 class="drawer-title">How this result was calculated</h3>
    ${r.methodology.map((m) => m.title === "Definition"
      ? `<details class="more"><summary>Exact experiment definition (JSON)</summary><div><code class="defn">${escapeHtml(m.text)}</code></div></details>`
      : `<div class="method-block"><span class="label">${escapeHtml(m.title)}</span><p>${escapeHtml(m.text)}</p></div>`).join("")}
    <p class="note">Run on ${fmtDateTime(r.ran_at)}. Re-running the same definition on the same data gives the same numbers (the bootstrap uses a fixed seed).</p>`,
    { label: "Methodology" });
}


// =========================================================
// Save to Notebook (never silently overwrites: updates keep versions)
// =========================================================
function drawEntryBar(lab) {
  const bar = lab.el.querySelector("[data-entry-bar]");
  if (!bar) return;
  bar.innerHTML = lab.entry
    ? `<div class="entry-bar">${icon("notebook", 15)} From your notebook: <b>${escapeHtml(lab.entry.name)}</b>${lab.entry.version ? ` <span class="na">v${lab.entry.version}</span>` : ""}
        <button class="link-btn" data-detach-entry>Work on it as a new experiment</button></div>` : "";
  bar.querySelector("[data-detach-entry]")?.addEventListener("click", () => { lab.entry = null; drawEntryBar(lab); saveLabState(); });
}

function challengeSummaryForSave(lab) {
  const c = lab.challenge;
  if (!c || !c.available || JSON.stringify(c.definition) !== JSON.stringify(lab.result.definition)) return null;
  return { survived: c.summary.survived.map((x) => x.text), caution: c.summary.caution.map((x) => x.text), ran_at: lab.result.ran_at };
}

function openSaveForm(lab) {
  const holder = lab.el.querySelector("[data-save-form]");
  if (holder.innerHTML) { holder.innerHTML = ""; return; }
  const suggested = lab.entry ? lab.entry.name : "";
  holder.innerHTML = `
    <form class="save-form glass-flat">
      <label><span class="label">Name</span><input name="name" value="${escapeHtml(suggested)}" maxlength="120" placeholder="e.g. ${escapeHtml(lab.result.ticker)} high-volume selloffs"></label>
      <label><span class="label">Hypothesis</span><textarea name="hypothesis" rows="2" placeholder="What did you expect to find, and why?">${escapeHtml(lab.entry?.hypothesis || "")}</textarea></label>
      <label><span class="label">Notes</span><textarea name="notes" rows="2" placeholder="Optional">${escapeHtml(lab.entry?.notes || "")}</textarea></label>
      <p class="note">Saves the exact definition, the result summary (n, mean, median, baseline, intervals, robust statistics, checks)${lab.challenge ? " and the Challenge findings" : ""}.</p>
      <div class="save-actions">
        ${lab.entry ? `<button class="pill-btn active" name="mode" value="update" data-tip="The previous version is kept in the entry's history">${icon("save", 14)}Update “${escapeHtml(lab.entry.name.slice(0, 28))}” (keeps history)</button>
                      <button class="pill-btn" name="mode" value="fork">${icon("fork", 14)}Save as a fork</button>`
                    : `<button class="pill-btn active" name="mode" value="new">${icon("save", 14)}Save</button>`}
        <span class="na" data-save-msg></span>
      </div>
    </form>`;
  holder.querySelector("input").focus();
  const form = holder.querySelector("form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = event.submitter?.value || (lab.entry ? "update" : "new");
    const fields = Object.fromEntries(new FormData(form));
    const payload = { name: fields.name, hypothesis: fields.hypothesis, notes: fields.notes, definition: lab.result.definition,
                      summary: lab.result.summary, challenge: challengeSummaryForSave(lab) };
    if (mode === "fork" && lab.entry && (payload.name || "").trim() === (lab.entry.name || "").trim()) payload.name = `${lab.entry.name} (fork)`;
    const msg = form.querySelector("[data-save-msg]");
    msg.textContent = "Saving…";
    try {
      let entry;
      if (mode === "update") entry = await postJson(`/api/notebook/${lab.entry.id}`, payload, "PATCH");
      else if (mode === "fork") entry = await postJson(`/api/notebook/${lab.entry.id}/fork`, payload);
      else entry = await postJson("/api/notebook", payload);
      lab.entry = { id: entry.id, name: entry.name, hypothesis: entry.hypothesis, notes: entry.notes, version: entry.version };
      drawEntryBar(lab);
      drawCrumbs(lab);
      saveLabState();
      holder.innerHTML = `<div class="saved-note">${icon("check", 15)} ${mode === "update" ? `Updated (now version ${entry.version}; earlier versions kept)` : mode === "fork" ? "Saved as a fork" : "Saved"}: “${escapeHtml(entry.name)}”. <button class="link-btn" data-env-go="notebook">Open notebook</button></div>`;
      notebookDirty = true;
    } catch (error) {
      msg.textContent = error.message;
    }
  });
}


// =========================================================
// Test elsewhere: the SAME definition on other instruments, side by side
// =========================================================
async function openElsewhere(lab) {
  const holder = lab.el.querySelector("[data-elsewhere-panel]");
  if (holder.dataset.open === "1" && !lab.elsewhere) { holder.innerHTML = ""; holder.dataset.open = ""; return; }
  holder.dataset.open = "1";
  const symbol = lab.result.ticker;
  lab.peerSel = lab.peerSel && lab.peerFor === symbol ? lab.peerSel : new Set();
  lab.peerFor = symbol;
  holder.innerHTML = `<div class="elsewhere glass-flat"><span class="label">Suggestions for ${escapeHtml(symbol)}</span><div class="skeleton"><div class="sk" style="width:60%"></div></div></div>`;
  let peers = { groups: [] };
  try { peers = await fetchJson(`/api/lab/peers/${encodeURIComponent(symbol)}`); } catch (error) { /* custom tickers still work */ }
  if (!lab.peerSel.size) {
    const similar = peers.groups.find((g) => g.id === "similar") || peers.groups.find((g) => g.id === "industry");
    (similar?.symbols || []).slice(0, 3).forEach((s) => lab.peerSel.add(s));
    lab.peerSel.add(symbol === "SPY" ? "QQQ" : "SPY");
  }
  holder.innerHTML = `
    <div class="elsewhere glass-flat">
      <p class="aside" style="margin:0 0 10px">Same conditions, same forward period, each instrument's own history. Is this an ${escapeHtml(symbol)} effect, or something broader?</p>
      ${peers.groups.map((g) => `<div class="peer-group"><span class="label" data-tip="${escapeHtml(g.source)}">${escapeHtml(g.label)}</span>
        ${g.symbols.map((s) => `<button class="chip peer ${lab.peerSel.has(s) ? "on" : ""}" data-peer="${escapeHtml(s)}" aria-pressed="${lab.peerSel.has(s)}">${escapeHtml(s)}</button>`).join("")}</div>`).join("")}
      <div class="peer-group"><span class="label">Custom</span>
        <form data-custom-peer class="compare-add"><input placeholder="+ ticker" maxlength="10" aria-label="Add a ticker"></form>
        ${[...lab.peerSel].filter((s) => !peers.groups.some((g) => g.symbols.includes(s))).map((s) => `<button class="chip peer on" data-peer="${escapeHtml(s)}">${escapeHtml(s)} ×</button>`).join("")}</div>
      <div class="save-actions"><button class="pill-btn active" data-elsewhere-run>Run on ${lab.peerSel.size} selected</button><span class="na">Up to 8 at a time.</span></div>
      <div data-elsewhere-table></div>
    </div>`;
  holder.querySelector("[data-custom-peer]").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = event.target.querySelector("input").value.trim().toUpperCase();
    if (/^[A-Z0-9.\-^=]{1,10}$/.test(value) && lab.peerSel.size < 8) { lab.peerSel.add(value); lab.elsewhere = null; openElsewhere(lab); }
  });
  if (lab.elsewhere) drawElsewhere(lab);
}

function togglePeer(lab, el) {
  const s = el.dataset.peer;
  if (lab.peerSel.has(s)) lab.peerSel.delete(s);
  else if (lab.peerSel.size < 8) lab.peerSel.add(s);
  el.classList.toggle("on", lab.peerSel.has(s));
  el.setAttribute("aria-pressed", lab.peerSel.has(s));
  const run = lab.el.querySelector("[data-elsewhere-run]");
  if (run) run.textContent = `Run on ${lab.peerSel.size} selected`;
}

async function runElsewhere(lab) {
  const base = lab.result;
  const columns = [{ symbol: base.ticker, result: base, home: true },
                   ...[...lab.peerSel].filter((s) => s !== base.ticker).map((symbol) => ({ symbol }))];
  lab.elsewhere = columns;
  drawElsewhere(lab);
  await Promise.all(columns.filter((c) => !c.result).map(async (col) => {
    const def = JSON.parse(JSON.stringify(base.definition));
    def.instrument.symbol = col.symbol;
    try { col.result = await postJson("/api/lab/experiment", def); } catch (error) { col.error = error.message; }
    if (lab.elsewhere === columns) drawElsewhere(lab);
  }));
}

function drawElsewhere(lab) {
  const holder = lab.el.querySelector("[data-elsewhere-table]");
  if (!holder || !lab.elsewhere) return;
  const cols = lab.elsewhere;
  const cell = (c, f) => c.error ? `<td class="na" title="${escapeHtml(c.error)}">error</td>` : !c.result ? '<td class="na">…</td>' : !c.result.stats ? '<td class="na">—</td>' : `<td>${f(c.result)}</td>`;
  const diff = (r) => isNum(r.difference) ? `<span class="${signClass(r.difference)}">${r.difference >= 0 ? "+" : "−"}${Math.abs(r.difference * 100).toFixed(2)} pts</span>` : NA;
  const rows = [
    ["Episodes", (r) => `<b>${r.stats.n}</b>${r.stats.n < 30 ? ' <small class="na">small</small>' : ""}`],
    ["Average", (r) => signed(fmtPct(r.stats.mean, true, 2), r.stats.mean)],
    ["Median", (r) => signed(fmtPct(r.stats.median, true, 2), r.stats.median)],
    ["Trimmed mean", (r) => isNum(r.robust?.trimmed_mean) ? signed(fmtPct(r.robust.trimmed_mean, true, 2), r.robust.trimmed_mean) : NA],
    ["Positive", (r) => fmtPct(r.stats.positive_rate, false, 0)],
    ["95% interval", (r) => r.stats.ci_low !== null ? `${stripTags(fmtPct(r.stats.ci_low, true, 1))} to ${stripTags(fmtPct(r.stats.ci_high, true, 1))}` : NA],
    ["Normal average", (r) => fmtPct(r.baseline?.mean, true, 2)],
    ["Vs normal", diff],
    ["Normal inside interval?", (r) => r.baseline_inside_ci === null ? NA : r.baseline_inside_ci ? "yes" : "<b>no</b>"],
    ["History since", (r) => fmtDay(r.data.tested_from).slice(-4)],
  ];
  holder.innerHTML = `
    <div class="table-scroll"><table class="data-table side-by-side">
      <thead><tr><th></th>${cols.map((c) => `<th class="${c.home ? "home" : ""}">${logoHtml(c.symbol, "", 18)} ${escapeHtml(c.symbol)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(([label, f]) => `<tr><td>${label}</td>${cols.map((c) => cell(c, f)).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="note">Columns are in the order you chose, not ranked. Different histories have different lengths and market regimes, so compare them with that in mind.</p>`;
}
