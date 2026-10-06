// =========================================================
// tab_risk_intel.js — Risk tab sections built from sourced evidence:
//   08 Dependencies & Exposures (interactive graph + disclosed concentration)
//   09 Government & Policy
//   10 Controversies & Material Events
//   11 Guidance History
//   13 Sources & Methodology
// Data: /api/stock/<T>/risk/intel (risk_intel.py), loaded lazily after the
// rest of the Risk tab, because it reads SEC filings and the News layer.
// Each section has a state: ok | no_evidence | not_run | error.
// =========================================================

const RI_STATUS_TONE = {
  "ALLEGATION": "soft", "REPORTED": "soft", "DISCLOSED": "soft", "STATEMENT": "soft", "PROPOSAL": "soft",
  "INVESTIGATION": "warn", "LAWSUIT FILED": "warn",
  "CHARGE": "bad", "JUDGMENT": "bad", "REGULATORY ACTION": "bad", "CONFIRMED INCIDENT": "bad",
  "SETTLED": "done", "DISMISSED": "done", "RESOLVED": "done",
  "AGENCY ACTION": "warn", "COURT RULING": "warn", "LEGISLATION": "warn", "IMPLEMENTED POLICY": "warn",
  "GOVERNMENT CONTRACT / AWARD": "good",
  "INITIATED": "soft", "REITERATED": "soft", "RAISED": "good", "LOWERED": "bad", "WITHDRAWN": "bad", "MIXED": "warn",
};
const riBadge = (status) => `<span class="ri-badge ${RI_STATUS_TONE[status] || "soft"}">${escapeHtml(status)}</span>`;
const riSource = (src) => src && src.url ? `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">${escapeHtml(src.name || "source")} ↗</a>` : escapeHtml(src?.name || "");

function riskIntelHtml(win) {
  const ri = win.riskIntel;
  if (win.riskIntelError) {
    return section("08", "Dependencies & exposures", "", `<p class="slot-msg">${escapeHtml(win.riskIntelError)} <button class="link-btn" data-ri-retry>Try again</button></p>`);
  }
  if (!ri) {
    return ["Dependencies & exposures", "Government & policy", "Controversies & material events", "Guidance history"].map((title, i) =>
      section(String(8 + i).padStart(2, "0"), title, "reading filings…", `<div class="ri-loading"><span class="ri-pulse"></span>
        ${i === 0 ? "Reading the latest annual report, recent SEC filings and the News layer. The first time takes about 20 seconds; after that it's cached." : "Waiting for the same sources…"}</div>`)).join("");
  }
  return dependenciesSection(win, ri) + policySection(ri) + controversySection(win, ri) + guidanceSection(ri);
}

function riEmpty(block) {
  return `<p class="slot-msg">${escapeHtml(block.message || "Nothing found in connected sources.")}</p>`;
}

// ---------- 08 Dependencies & exposures ----------
function dependencyNodes(ri) {
  const d = ri.dependencies;
  const groups = [];
  const byRel = {};
  for (const e of d.entities || []) (byRel[e.relationship] = byRel[e.relationship] || []).push(e);
  const order = ["Manufacturer / supplier", "Customer", "Cloud / infrastructure", "Distributor / channel", "Partner", "Competitor"];
  for (const rel of order) {
    if (!byRel[rel]) continue;
    groups.push({ id: rel, label: rel === "Manufacturer / supplier" ? "Suppliers" : rel === "Customer" ? "Customers" : rel === "Competitor" ? "Competitors" : rel.split(" / ")[0],
      nodes: byRel[rel].slice(0, rel === "Competitor" ? 4 : 6).map((e) => ({ key: `e:${e.name}`, label: e.name, item: e, type: "entity" })) });
  }
  if ((d.geographies || []).length) groups.push({ id: "geo", label: "Geographies", nodes: d.geographies.slice(0, 5).map((g) => ({ key: `g:${g.name}`, label: g.name, item: g, type: "geo" })) });
  if ((d.macro || []).length) groups.push({ id: "macro", label: "Macro (stated)", nodes: d.macro.slice(0, 4).map((m) => ({ key: `m:${m.name}`, label: m.name, item: m, type: "macro" })) });
  const pol = ri.policy?.disclosures || [];
  if (pol.length) groups.push({ id: "gov", label: "Government & policy", nodes: pol.slice(0, 4).map((p) => ({ key: `p:${p.topic}`, label: p.topic, item: p, type: "policy" })) });
  if (d.industry?.industry) groups.push({ id: "industry", label: "Industry", nodes: [{ key: "i:industry", label: d.industry.industry, item: d.industry, type: "industry" }] });
  return groups;
}

function dependencyGraph(win, ri) {
  const groups = dependencyNodes(ri);
  if (!groups.length) return "";
  // Two-sided tree: the company in the middle, branches (Suppliers, Customers, Geographies…) left and right,
  // one row per node so labels never overlap.
  const ROW = 24, GAP = 18, W = 920, cx = W / 2;
  const sides = [[], []];
  const load = [0, 0];
  for (const g of groups) {
    const side = load[0] <= load[1] ? 0 : 1;
    sides[side].push(g);
    load[side] += g.nodes.length * ROW + GAP + 18;
  }
  const H = Math.max(220, Math.max(...load) + 20);
  const cy = H / 2;
  let svg = "";
  const index = {};
  sides.forEach((list, side) => {
    const dir = side === 0 ? -1 : 1;
    const hubX = cx + dir * 150, nodeX = cx + dir * 245;
    let y = (H - load[side]) / 2 + 18;
    for (const g of list) {
      const top = y + 14;
      const hubY = top + ((g.nodes.length - 1) * ROW) / 2;
      svg += `<path class="dep-edge" d="M${cx + dir * 30},${cy} C${cx + dir * 90},${cy} ${hubX - dir * 60},${hubY} ${hubX},${hubY}"/>`;
      svg += `<g class="dep-hub"><circle cx="${hubX}" cy="${hubY}" r="4"/><text x="${hubX}" y="${top - 12}" text-anchor="middle">${escapeHtml(g.label)}</text></g>`;
      g.nodes.forEach((node, i) => {
        const ny = top + i * ROW;
        index[node.key] = node;
        const rel = node.item.relationship || g.label;
        svg += `<path class="dep-edge leaf" d="M${hubX},${hubY} C${hubX + dir * 40},${hubY} ${nodeX - dir * 40},${ny} ${nodeX},${ny}"/>
          <g class="dep-node ${node.type} ${win.depSelected === node.key ? "on" : ""}" data-dep-node="${escapeHtml(node.key)}" tabindex="0" role="button"
             aria-label="${escapeHtml(`${node.label}: ${rel}`)}"><rect x="${dir < 0 ? nodeX - 200 : nodeX - 8}" y="${ny - 10}" width="208" height="20" rx="6" class="hit"/>
            <circle cx="${nodeX}" cy="${ny}" r="6"/>
            <text x="${nodeX + dir * 12}" y="${ny + 4}" text-anchor="${dir < 0 ? "end" : "start"}">${escapeHtml(node.label.length > 28 ? node.label.slice(0, 27) + "…" : node.label)}</text></g>`;
      });
      y += g.nodes.length * ROW + GAP + 18;
    }
  });
  win.depIndex = index;
  return `<div class="dep-graph-wrap"><svg class="dep-graph" viewBox="0 0 ${W} ${H}" role="group" aria-label="Dependency graph">
      ${svg}<g class="dep-center"><circle cx="${cx}" cy="${cy}" r="30"/><text x="${cx}" y="${cy + 5}" text-anchor="middle">${escapeHtml(ri.ticker)}</text></g></svg></div>
    <div class="dep-detail" data-dep-detail>${depDetail(win, index[win.depSelected] || index[Object.keys(index)[0]], ri)}</div>`;
}

function depDetail(win, node, ri) {
  if (!node) return "";
  win.depSelected = node.key;
  const it = node.item;
  const src = it.source || ri.dependencies.source;
  const ev = (it.evidence || []).map((e) => `<blockquote>${escapeHtml(e.text)}${e.section ? `<footer>${escapeHtml(e.section)}</footer>` : ""}</blockquote>`).join("");
  const kind = { entity: it.relationship, geo: `Geography · ${(it.why || []).join(", ")}`, macro: "Macro factor the company names as a risk", policy: "Policy area the company discusses", industry: "Industry" }[node.type];
  return `<div class="dep-detail-head"><b>${escapeHtml(node.label)}</b><span class="ri-badge soft">${escapeHtml(kind || "")}</span>
      ${it.ticker ? `<button class="small-btn" data-open-research="${escapeHtml(it.ticker)}">${icon("research", 13)}Open ${escapeHtml(it.ticker)} research</button>` : ""}</div>
    ${node.type === "industry" ? `<p>${escapeHtml(it.sector || "")} · ${escapeHtml(it.industry || "")} <span class="rp-fine">(${escapeHtml(it.source)})</span></p>` : ""}
    ${it.mentions ? `<p class="rp-fine">Mentioned in ${it.mentions} relevant sentence${it.mentions === 1 ? "" : "s"}${it.other_relationships?.length ? `; also described as ${it.other_relationships.map((r) => r.toLowerCase()).join(", ")}` : ""}.</p>` : ""}
    ${ev ? `<div class="label">Why it matters (the filing's own words)</div>${ev}` : ""}
    ${src ? `<p class="rp-fine">Source: ${riSource(src)} · last verified ${escapeHtml(src.date || "")}. No exposure size is implied unless the filing states one.</p>` : ""}`;
}

function dependenciesSection(win, ri) {
  const d = ri.dependencies;
  if (d.state !== "ok") return section("08", "Dependencies & exposures", "", riEmpty(d) + industryLine(d));
  const quant = (d.quantitative || []).map((q) => `<tr><td>${escapeHtml(q.subject)}</td><td class="num">${q.percent}%</td><td>of ${escapeHtml(q.of)}</td>
      <td><details><summary>sentence</summary><p>${escapeHtml(q.evidence)}</p></details></td></tr>`).join("");
  const geos = (d.geographies || []).map((g) => `<details class="ri-row"><summary><b>${escapeHtml(g.name)}</b> <span class="rp-fine">${escapeHtml(g.why.join(" · "))} · ${g.mentions} sentences</span></summary>
      ${g.evidence.map((e) => `<blockquote>${escapeHtml(e.text)}</blockquote>`).join("")}</details>`).join("");
  return section("08", "Dependencies & exposures", d.source ? `from ${riSource(d.source)}` : "", `
    <p class="aside">What outside companies, places and conditions could materially affect ${escapeHtml(ri.company || ri.ticker)}, and why, in the filing's own words. Click a node.</p>
    ${dependencyGraph(win, ri)}
    <div class="ri-cols">
      <div><h4 class="ri-h4">Disclosed quantitative exposure</h4>
        ${quant ? `<div class="table-scroll"><table class="data-table ri-table"><thead><tr><th>Who</th><th class="num">Share</th><th></th><th>Evidence</th></tr></thead><tbody>${quant}</tbody></table></div>
          <p class="rp-fine">Percentages exactly as stated in the filing (customers are often unnamed).</p>`
          : `<p class="slot-msg">No material customer or geographic concentration percentages found in the annual report.</p>`}</div>
      <div><h4 class="ri-h4">Geographies</h4>${geos || `<p class="slot-msg">No geography discussed often enough to list.</p>`}</div>
    </div>
    ${(d.macro || []).length ? `<h4 class="ri-h4">Macro factors the company itself names</h4><div class="ri-chips">${d.macro.map((m) =>
      `<button class="ri-chip" data-dep-node="m:${escapeHtml(m.name)}">${escapeHtml(m.name)} <span>${m.mentions}</span></button>`).join("")}</div>
      <p class="rp-fine">From the risk factors (Item 1A). This is the company's stated exposure, not a measured correlation; correlations are under “Moves with the market”.</p>` : ""}
    ${industryLine(d)}`);
}

function industryLine(d) {
  return d.industry?.industry ? `<p class="rp-fine">Industry: ${escapeHtml(d.industry.sector || "")} · ${escapeHtml(d.industry.industry)} (${escapeHtml(d.industry.source)}).</p>` : "";
}

// ---------- 09 Government & policy ----------
function policySection(ri) {
  const p = ri.policy;
  if (!p || p.state !== "ok") return section("09", "Government & policy", "", riEmpty(p || {}));
  const topics = (p.disclosures || []).map((t) => `<details class="ri-row"><summary><b>${escapeHtml(t.topic)}</b> <span class="rp-fine">${t.mentions} sentence${t.mentions === 1 ? "" : "s"} in the annual report</span></summary>
      ${t.evidence.map((e) => `<blockquote>${escapeHtml(e.text)}</blockquote>`).join("")}</details>`).join("");
  const rows = (p.actions || []).map((a) => `<tr><td class="nowrap">${escapeHtml(fmtDay(a.date))}</td><td>${escapeHtml(a.entity)}</td>
      <td>${a.source?.url ? `<a href="${escapeHtml(a.source.url)}" target="_blank" rel="noopener">${escapeHtml(a.action)} ↗</a>` : escapeHtml(a.action)}
        <div class="rp-fine">${escapeHtml(a.connection)}</div></td>
      <td>${riBadge(a.status)}</td><td class="rp-fine">${escapeHtml(a.source?.name || "")}${a.primary_source ? " · primary" : ""}${a.source_count > 1 ? ` · ${a.source_count} publishers` : ""}</td></tr>`).join("");
  return section("09", "Government & policy", "factual · no judgments", `
    ${topics ? `<h4 class="ri-h4">Exposure the company discloses</h4>${topics}` : ""}
    <h4 class="ri-h4">Recent government actions and developments</h4>
    ${rows ? `<div class="table-scroll"><table class="data-table ri-table"><thead><tr><th>Date</th><th>Who</th><th>What</th><th>Status</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<p class="slot-msg">No government developments involving the company in the last month of connected news sources.</p>`}
    <p class="rp-fine">Status separates proposals, statements, investigations, agency actions, court rulings, legislation and implemented policy.
      MarketLab doesn't characterize motives or say whether a policy or politician is good or bad for the stock.</p>`);
}

// ---------- 10 Controversies & material events ----------
function controversySection(win, ri) {
  const c = ri.controversies;
  if (!c || c.state !== "ok") return section("10", "Controversies & material events", "", riEmpty(c || {}) + (c?.coverage ? `<p class="rp-fine">Checked: ${escapeHtml(c.coverage)}</p>` : ""));
  const shown = win.riAllEvents ? c.events : c.events.slice(0, 10);
  const items = shown.map((e, i) => `<li class="ri-event">
      <details><summary><span class="ri-date">${escapeHtml(fmtDay(e.date))}${e.date_exact ? "" : "*"}</span>${riBadge(e.status)}
        <span class="ri-cat">${escapeHtml(e.category)}</span><span class="ri-title">${escapeHtml(e.title)}</span>
        <span class="ri-origin">${e.origin === "news" ? "news · as reported" : e.origin === "filing" ? "SEC filing" : "annual report"}</span></summary>
        <div class="ri-event-body"><p>${escapeHtml(e.description)}</p>
          ${e.entity ? `<p class="rp-fine">Entity: ${escapeHtml(e.entity)}</p>` : ""}
          <p class="rp-fine">${escapeHtml(e.relevance || "")}</p>
          <p class="rp-fine">Sources: ${e.sources.map(riSource).join(" · ")}</p>
          ${e.date ? `<button class="small-btn" data-view-chart="${escapeHtml(e.date)}" data-label="${escapeHtml(e.category.split(" / ")[0].slice(0, 18))}">${icon("chart", 13)}View on chart</button>` : ""}</div></details></li>`).join("");
  return section("10", "Controversies & material events", "sourced timeline", `
    <ol class="ri-timeline">${items}</ol>
    ${c.events.length > 10 && !win.riAllEvents ? `<button class="link-btn" data-ri-all-events>Show all ${c.events.length}</button>` : ""}
    <p class="rp-fine">Allegations, lawsuits and news reports are not findings. “Confirmed incident” is used only when the company itself reported it in an SEC filing.
      * = date of the filing that discloses it. Checked: ${escapeHtml(c.coverage)}</p>`);
}

// ---------- 11 Guidance ----------
function fmtGuide(v, unit) {
  if (unit === "%") return `${v.toFixed(1)}%`;
  if (unit === "$/share") return `$${v.toFixed(2)}`;
  return `$${fmtCompact(v)}`;
}
function guidanceSection(ri) {
  const g = ri.guidance;
  if (!g || g.state !== "ok") return section("11", "Guidance history", "company guidance only", riEmpty(g || {}) +
    `<p class="rp-fine">Analyst estimates are kept separately (Earnings tab), never mixed with company guidance.</p>`);
  const range = (it) => it.low === it.high ? fmtGuide(it.low, it.unit) : `${fmtGuide(it.low, it.unit)} – ${fmtGuide(it.high, it.unit)}`;
  const rows = g.items.map((it) => `<tr><td class="nowrap">${escapeHtml(fmtDay(it.date))}</td><td>${escapeHtml(it.period)}</td><td>${escapeHtml(it.metric)}</td>
      <td class="num">${range(it)}</td><td>${riBadge(it.change)}${it.release_change ? ` ${riBadge(it.release_change)}` : ""}${it.note ? ` <span class="rp-fine">${escapeHtml(it.note)}</span>` : ""}</td>
      <td class="num">${it.previous ? fmtGuide(it.previous.mid, it.unit) : "—"}</td>
      <td class="num">${it.actual ? `${fmtGuide(it.actual.value, it.unit)} <span class="${it.actual.vs_mid >= 0 ? "pos" : "neg"}">(${fmtPct(it.actual.vs_mid, true, 1)} vs mid)</span>` : "—"}</td>
      <td><details><summary>source</summary><p>${escapeHtml(it.text)}</p><a href="${escapeHtml(it.url)}" target="_blank" rel="noopener">Earnings release ↗</a></details></td></tr>`).join("");
  return section("11", "Guidance history", `${g.checked} earnings releases read`, `
    <div class="table-scroll"><table class="data-table ri-table"><thead><tr><th>Issued</th><th>For</th><th>Metric</th><th class="num">Guidance</th><th>Change</th><th class="num">Previous mid</th><th class="num">Actual</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${g.events.map((e) => `<p>${riBadge("WITHDRAWN")} ${escapeHtml(fmtDay(e.date))} · ${escapeHtml(e.text)} <a href="${escapeHtml(e.url)}" target="_blank" rel="noopener">↗</a></p>`).join("")}
    <p class="rp-fine">Raised / lowered compares the same metric for the same period; a new quarter's outlook is “initiated”. Actual = reported revenue from SEC XBRL for the quarter that followed.
      Analyst estimates are not company guidance and are shown in the Earnings tab. <button class="link-btn" data-goto="earnings">View earnings ${icon("external", 12)}</button></p>`);
}

// ---------- 13 Sources & methodology ----------
function sourcesSection(r, win) {
  const ri = win.riskIntel;
  const used = (ri?.sources_used || []).map((s) => `<li>${s.url ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)} ↗</a>` : escapeHtml(s.name)}</li>`).join("");
  return section("13", "Sources & methodology", "", `
    <ul class="ri-sources">
      <li>Prices: ${escapeHtml(r.data.provider)} (adjusted daily candles, checked by the data-quality layer).</li>
      <li>Financial position: SEC XBRL company facts.</li>
      ${used || "<li>Filings and news: loading…</li>"}
    </ul>
    <p class="rp-fine">Dependencies, policy topics and macro factors come only from sentences in the annual report that tie them to the company; percentages only where stated.
      Controversy statuses are read from the wording (filed, settled, dismissed…) and news is labeled “as reported”. Guidance comes from the company's own earnings releases.
      Nothing here is generated from a language model's background knowledge.</p>
    ${r.sector.risk_factors_filing ? `<p><a class="small-btn" href="${escapeHtml(r.sector.risk_factors_filing.url)}" target="_blank" rel="noopener">${icon("filings", 14)}Risk factors · ${escapeHtml(r.sector.risk_factors_filing.form)} filed ${fmtDay(r.sector.risk_factors_filing.filed)} ↗</a></p>` : ""}`);
}

async function loadRiskIntel(win, refresh = false) {
  if (win.riskIntelLoading) return;
  win.riskIntelLoading = true;
  win.riskIntelError = null;
  try {
    win.riskIntel = await fetchJson(`/api/stock/${encodeURIComponent(win.ticker)}/risk/intel${refresh ? "?refresh=1" : ""}`);
  } catch (error) {
    win.riskIntelError = error.message;
  }
  win.riskIntelLoading = false;
  const box = win.el.querySelector("[data-risk-intel]");
  if (box) box.innerHTML = riskIntelHtml(win);
  const glance = win.el.querySelector(".risk-glance");
  if (glance && win.data.risk && typeof riskGlanceHtml === "function") {
    const r = win.data.risk;
    const h = r.horizons.find((x) => x.label === win.riskHorizon) || r.horizons[2];
    glance.outerHTML = riskGlanceHtml(win, r, h);
  }
  renumberSections(win.el.querySelector(".window-body") || win.el);
  const src = win.el.querySelector("[data-risk-sources]");
  if (src && win.data.risk) src.innerHTML = sourcesSection(win.data.risk, win);
}

function riskIntelClick(win, event) {
  const node = event.target.closest("[data-dep-node]");
  if (node && win.depIndex) {
    const key = node.dataset.depNode;
    const target = win.depIndex[key] || (key.startsWith("m:") ? { key, label: key.slice(2), type: "macro", item: win.riskIntel.dependencies.macro.find((m) => `m:${m.name}` === key) } : null);
    if (!target || !target.item) return true;
    win.el.querySelectorAll(".dep-node.on").forEach((n) => n.classList.remove("on"));
    win.el.querySelector(`.dep-node[data-dep-node="${CSS.escape(key)}"]`)?.classList.add("on");
    const box = win.el.querySelector("[data-dep-detail]");
    if (box) box.innerHTML = depDetail(win, target, win.riskIntel);
    box?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return true;
  }
  const open = event.target.closest("[data-open-research]");
  if (open) { openResearch(open.dataset.openResearch); return true; }
  const chart = event.target.closest("[data-view-chart]");
  if (chart) {
    const st = chartState(win);
    const age = (Date.now() - new Date(chart.dataset.viewChart).getTime()) / 864e5;
    st.range = age < 330 ? "1Y" : age < 1800 ? "5Y" : "MAX";
    win.chartFocus = { date: chart.dataset.viewChart, label: chart.dataset.label || "Event" };
    showTab(win, "chart");
    showToast(`Marked ${fmtDay(chart.dataset.viewChart)} on the chart`);
    return true;
  }
  if (event.target.closest("[data-ri-all-events]")) {
    win.riAllEvents = true;
    win.el.querySelector("[data-risk-intel]").innerHTML = riskIntelHtml(win);
    renumberSections(win.el.querySelector(".window-body") || win.el);
    return true;
  }
  if (event.target.closest("[data-ri-retry]")) { loadRiskIntel(win, true); return true; }
  return false;
}

document.addEventListener("keydown", (event) => {
  if ((event.key === "Enter" || event.key === " ") && event.target.matches?.(".dep-node")) {
    event.preventDefault();
    event.target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  }
});
