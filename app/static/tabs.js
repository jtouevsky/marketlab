// =========================================================
// tabs.js — WHAT the Overview and Profile tabs show, plus shared
// building blocks. (Earnings, News, Risk: tab_*.js. Chart: charts.js.)
//
// Every render function receives `win`, the window's state:
//   win.ticker
//   win.data.<part>     JSON from the server (overview, summary, filings,
//                       profile, earnings, news, risk)
//   win.status.<part>   "loading" | "ok" | "error" | "off"
//
// Slow parts are drawn into "slots" (<div data-slot="summary">) that
// app.js refills when their data arrives, so a tab never waits for
// everything before showing anything.
//
// Design rule: information sits directly on the page (big type,
// hairlines, spec-sheet rows), not inside a card per number.
// =========================================================

// needs: the data part the tab can't draw without. after: runs once the HTML is in place.
// (Arrow functions, because some renderers live in files loaded after this one.)
const TABS = [
  { id: "overview", label: "Overview", icon: "overview", render: (w) => renderOverview(w), after: (w) => afterOverview(w) },
  { id: "profile",  label: "Profile",  icon: "profile",  needs: "profile",  render: (w) => renderProfile(w) },
  { id: "earnings", label: "Earnings", icon: "earnings", needs: "earnings", render: (w) => renderEarnings(w) },
  { id: "chart",    label: "Chart",    icon: "chart",    render: (w) => renderChartTab(w), after: (w) => afterChartTab(w) },
  { id: "news",     label: "News",     icon: "news",     needs: "news",     render: (w) => renderNews(w) },
  { id: "risk",     label: "Risk",     icon: "risk",     needs: "risk",     render: (w) => renderRisk(w), after: (w) => afterRisk(w) },
];

// Slots: name -> renderer. A slot re-renders whenever one of its parts arrives.
const SLOTS = {
  summary:      { parts: ["summary"], render: (win, el) => renderSummarySlot(win, el.dataset.full === "1") },
  intro:        { parts: ["summary"], render: (win) => renderIntroSlot(win) },
  event:        { parts: ["news"], render: (win) => renderEventSlot(win) },
};

const BADGE = {
  calc: '<span class="badge" data-tip="Calculated by MarketLab from reported data.">calc</span>',
  est:  '<span class="badge est" data-tip="Analyst consensus estimate: a forecast, not a reported result.">est</span>',
};


// ---------- Building blocks ----------------------------------------------

// Section numbers follow the order on screen (tabs put decision-relevant sections first,
// and some sections are optional), so they're assigned after rendering.
function renumberSections(root) {
  if (!root) return;
  let n = 0;
  root.querySelectorAll(".section-head .idx").forEach((el) => {
    if (el.textContent.trim()) el.textContent = String(++n).padStart(2, "0");
  });
}

function section(index, title, source, body) {
  return `
    <section class="section">
      <header class="section-head">
        <span class="idx">${index}</span><h3>${title}</h3>
        ${source ? `<span class="source">${source}</span>` : ""}
      </header>
      ${body}
    </section>`;
}

// One spec-sheet row: label (with glossary ⓘ) · big value · plain-English line.
function specRow({ key, label, value, plain, badge = "", sectionName = "" }) {
  const labelHtml = key ? term(key, label, { plain, value: stripTags(value), section: sectionName }) : escapeHtml(label);
  return `
    <div class="spec-row">
      <div class="spec-top"><span class="label">${labelHtml}${badge}</span><span class="spec-value">${value}</span></div>
      ${plain ? `<div class="spec-plain">${escapeHtml(plain)}</div>` : ""}
    </div>`;
}

function fact(labelHtml, valueHtml) {
  return `<div class="fact"><dt class="label">${labelHtml}</dt><dd>${valueHtml}</dd></div>`;
}

function textOrNA(text) { return text ? escapeHtml(text) : NA; }
function signed(html, value) { return `<span class="${signClass(value)}">${html}</span>`; }
function stripTags(html) { return String(html).replace(/<[^>]*>/g, ""); }

function sourceBadge(source, detail, conflict, currency) {
  if (!source || source === "marketlab") return "";
  const label = source === "sec" ? "SEC" : "Yahoo";
  const tip = source === "sec"
    ? `From the company's official SEC filing (${detail || "10-K"}).`
    : "From Yahoo Finance's copy of the financial statements (no SEC figure available).";
  let html = `<span class="src-badge ${source}" data-tip="${escapeHtml(tip)}">${label}</span>`;
  if (conflict) {
    const other = escapeHtml(`Yahoo Finance shows ${stripTags(fmtMoney(conflict.value, currency))} for this year. ` +
      "MarketLab uses the official SEC filing; differences usually come from reclassification or restatement.");
    html += `<span class="conflict" data-tip="${other}">≠</span>`;
  }
  return html;
}

function slotPlaceholder(win, part) {
  const state = win.status[part];
  if (state === "loading") return '<div class="skeleton"><div class="sk"></div><div class="sk" style="width:80%"></div></div>';
  if (state === "off") return `<div class="slot-msg">${escapeHtml(win.errors[part] || "Not available.")}</div>`;
  return `<div class="slot-msg error">${escapeHtml(win.errors[part] || "Temporarily unavailable.")}</div>`;
}

// A negative P/E-type multiple isn't meaningful: show "n/m".
function peValue(value) {
  if (isNum(value) && value < 0) return '<span class="na" data-tip="Not meaningful: earnings are negative.">n/m</span>';
  return fmtMultiple(value);
}

// "Market open" or "Pre-market · last price Sep 30, 4:00 PM"
function marketStateText(d) {
  const lastTrade = fmtDateTime(d.market_time);
  const labels = { REGULAR: "Market open", PRE: "Pre-market", PREPRE: "Market closed", POST: "After hours", POSTPOST: "Market closed", CLOSED: "Market closed" };
  const label = labels[d.market_state] || "Last trade";
  if (d.market_state === "REGULAR") return label;
  return lastTrade ? `${label} · last price ${escapeHtml(lastTrade)}` : label;
}


// =========================================================
// OVERVIEW: the 30-second picture
// =========================================================
function renderOverview(win) {
  const d = win.data.overview;
  const g = d.glance || {};
  const ctx = { currency: d.currency, pe: g.pe, rate: g.dividend_rate, low: g.week52_low, high: g.week52_high };

  let change = '<span class="change-pill flat">No change data</span>';
  if (isNum(d.change)) {
    const cls = d.change > 0 ? "up" : d.change < 0 ? "down" : "flat";
    const sign = d.change > 0 ? "+" : d.change < 0 ? "−" : "";
    change = `<span class="change-pill ${cls}">${sign}${Math.abs(d.change).toFixed(2)} <small>${fmtPct(d.change_pct / 100, true, 2)} today</small></span>`;
  }
  const priceTip = `Last trade: ${fmtDateTime(d.market_time) || "unknown"}. Downloaded by MarketLab: ${fmtDateTime(d.fetched_at)}. Source: Yahoo Finance (delayed).`;

  const masthead = `
    <div class="masthead">
      <div class="masthead-top">
        <div class="id-block">
          ${logoHtml(win.ticker, d.name, 52)}
          <div>
            <div class="ticker-xl">${escapeHtml(win.ticker)}</div>
            <div class="company-line">${escapeHtml(d.name)}<span class="sep">·</span>${escapeHtml(d.exchange || "")}</div>
          </div>
        </div>
        <div class="masthead-actions">
          <button class="action-btn glass-flat" data-lab-ticker="${escapeHtml(win.ticker)}" data-tip="Open the Lab with ${escapeHtml(win.ticker)} and test an idea against its history">${icon("lab", 17)}Test an idea about ${escapeHtml(win.ticker)}</button>
          <button class="action-btn glass-flat" data-ask>${icon("ask", 17)}Ask</button>
        </div>
      </div>
      <div class="price-line">
        <span class="price-xl">${fmtPrice(d.price)}<small>${escapeHtml(d.currency || "")}</small></span>
        ${change}
        <span class="cap-inline"><span class="label">${term("market_cap", "Market cap")}</span> ${fmtMoney(d.market_cap, d.currency)}</span>
      </div>
      <div class="company-meta">${marketStateText(d)} · <span data-tip="${escapeHtml(priceTip)}">updated <span data-relative="${d.fetched_at}">${fmtRelative(d.fetched_at)}</span></span></div>
      <div class="intro" data-slot="intro">${renderIntroSlot(win)}</div>
    </div>`;

  const chart = `<div class="section tight">${quickChartHtml(win)}</div><div class="ov-event" data-slot="event">${renderEventSlot(win)}</div>`;

  const rev = g.revenue_growth;
  const keyNumbers = section("01", "Key numbers", "Yahoo Finance · latest available", `
    <div class="spec">
      ${specRow({ key: "market_cap", label: "Market cap", value: fmtMoney(d.market_cap, d.currency), sectionName: "Key numbers" })}
      ${specRow({ key: "week52", label: "52-week range", value: isNum(g.week52_low) ? `${fmtPrice(g.week52_low)} – ${fmtPrice(g.week52_high)}` : NA,
                  plain: PLAIN.week52(d.price, ctx), sectionName: "Key numbers" })}
      ${specRow({ key: "pe", label: "P/E · TTM", value: peValue(g.pe), sectionName: "Key numbers",
                  plain: PLAIN.pe(g.pe) || (isNum(g.eps_ttm) && g.eps_ttm < 0 ? "No P/E: the company lost money over the last 12 months." : null) })}
      ${specRow({ key: "eps", label: "EPS · TTM", value: fmtPerShare(g.eps_ttm, d.currency), plain: PLAIN.eps(g.eps_ttm, ctx), sectionName: "Key numbers" })}
      ${specRow({ key: "revenue", label: "Revenue growth · latest qtr", value: signed(fmtPct(rev, true), rev), sectionName: "Key numbers",
                  plain: isNum(rev) ? `Revenue in the latest quarter was ${fmtPct(Math.abs(rev), false, 1)} ${rev >= 0 ? "higher" : "lower"} than a year earlier.` : null })}
      ${specRow({ key: "beta", label: "Beta · 5Y", value: fmtNumber(g.beta), plain: PLAIN.beta(g.beta), sectionName: "Key numbers" })}
      ${specRow({ key: "fcf", label: "Free cash flow · TTM", value: fmtMoney(g.free_cash_flow, d.currency), plain: PLAIN.fcf(g.free_cash_flow), sectionName: "Key numbers" })}
      ${isNum(g.dividend_yield) ? specRow({ key: "dividend_yield", label: "Dividend yield", value: fmtPct(g.dividend_yield, false, 2), badge: BADGE.calc, plain: PLAIN.dividend_yield(g.dividend_yield, ctx), sectionName: "Key numbers" }) : ""}
    </div>`);

  const quickRead = section("02", "Quick read", "facts, not a rating", `
    <div class="quickread">
      ${quickReadRow("Growth", growthRead(g))}
      ${quickReadRow("Profitability", profitRead(g, d.currency))}
      ${quickReadRow("Balance sheet", balanceRead(g, d.currency))}
      ${quickReadRow("Volatility", '<span data-volread><span class="na">Measuring the last year of prices…</span></span>')}
    </div>
    <p class="note">Each line restates reported or measured numbers. MarketLab doesn't score companies. <button class="link-btn" data-goto="risk">More in Risk ${icon("external", 12)}</button></p>`);

  return masthead + chart + keyNumbers + quickRead;
}

// The most important current development (from the News layer), shown under the chart on Overview
function renderEventSlot(win) {
  const st = win.status.news;
  if (st === "loading" || st === undefined) return `<div class="ov-event-row"><span class="label">Latest major development</span><span class="na">checking the news…</span></div>`;
  if (st !== "ok" || !win.data.news) return "";
  const week = Date.now() - 7 * 86400000;
  const events = (win.data.news.events || []).filter((e) => new Date(e.published).getTime() >= week);
  const top = [...events].sort((a, b) => (b.material - a.material) || (b.relevance - a.relevance) || b.published.localeCompare(a.published))[0];
  if (!top) return `<div class="ov-event-row"><span class="label">Latest major development</span><span class="na">No major news in the last week.</span></div>`;
  const lead = top.sources[0];
  return `<div class="ov-event-row"><span class="label">Latest major development</span>
    <a class="ov-event-title" href="${escapeHtml(lead.url)}" target="_blank" rel="noopener">${escapeHtml(top.headline)}</a>
    <span class="ov-event-meta">${escapeHtml(lead.name)} · ${escapeHtml(newsTime(top.published))} · ${escapeHtml(top.category)}${top.source_count > 1 ? ` · ${top.source_count} sources` : ""}</span>
    <button class="link-btn" data-goto="news">All news ${icon("external", 12)}</button></div>`;
}

function afterOverview(win) {
  drawQuickChart(win);
  if (typeof loadPart === "function" && win.status.news === undefined) loadPart(win, "news");
  // Volatility line: computed from the last year of daily closes (same data as the 1Y chart)
  loadChart(win.ticker, "1Y").then((data) => {
    const el = win.el.querySelector("[data-volread]");
    if (!el) return;
    const closes = data.close.filter(isNum);
    if (closes.length < 30) { el.innerHTML = '<span class="na">Not enough price history.</span>'; return; }
    const rets = closes.slice(1).map((c, i) => c / closes[i] - 1);
    const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
    const vol = Math.sqrt(rets.reduce((a, r) => a + (r - mean) ** 2, 0) / (rets.length - 1)) * Math.sqrt(252);
    let peak = closes[0], worst = 0;
    for (const c of closes) { peak = Math.max(peak, c); worst = Math.min(worst, c / peak - 1); }
    const big = rets.filter((r) => Math.abs(r) >= 0.05).length;
    const beta = win.data.overview.glance?.beta;
    el.innerHTML = `Over the past year, ${term("volatility", "annualised volatility")} was ${fmtPct(vol, false, 0)}; ` +
      `the deepest fall from a high was ${fmtPct(worst, false, 0).replace("−", "")}. ${big} day${big === 1 ? "" : "s"} moved 5% or more.` +
      (isNum(beta) ? ` ${PLAIN.beta(beta)}` : "");
  }).catch(() => {
    const el = win.el.querySelector("[data-volread]");
    if (el) el.innerHTML = '<span class="na">Price history unavailable right now.</span>';
  });
}

function quickReadRow(label, html) {
  return `<div class="qr-row"><span class="qr-label">${label}</span><p>${html}</p></div>`;
}

function growthRead(g) {
  const parts = [];
  if (isNum(g.revenue_growth)) parts.push(`Revenue was ${fmtPct(Math.abs(g.revenue_growth), false, 0)} ${g.revenue_growth >= 0 ? "higher" : "lower"} in the latest quarter than a year earlier`);
  if (isNum(g.earnings_growth)) parts.push(`earnings ${g.earnings_growth >= 0 ? "rose" : "fell"} ${fmtPct(Math.abs(g.earnings_growth), false, 0)}`);
  return parts.length ? `${parts.join("; ")}.` : '<span class="na">No growth figures available.</span>';
}

function profitRead(g, currency) {
  if (!isNum(g.operating_margin) && !isNum(g.profit_margin)) return '<span class="na">No margin figures available.</span>';
  const parts = [];
  if (isNum(g.operating_margin)) parts.push(PLAIN.operating_margin(g.operating_margin));
  if (isNum(g.profit_margin)) parts.push(`Net margin over the last 12 months: ${fmtPct(g.profit_margin, false, 1)}.`);
  if (isNum(g.revenue_ttm)) parts.push(`Revenue over that period: ${fmtMoneyText(g.revenue_ttm, currency)}.`);
  return parts.join(" ");
}

function balanceRead(g, currency) {
  if (!isNum(g.total_cash) && !isNum(g.total_debt)) return '<span class="na">No balance-sheet figures available.</span>';
  const parts = [];
  if (isNum(g.total_cash) && isNum(g.total_debt)) parts.push(PLAIN.net_cash(g.total_cash - g.total_debt, { currency }));
  else parts.push(`Cash ${fmtMoneyText(g.total_cash, currency)}, debt ${fmtMoneyText(g.total_debt, currency)}.`);
  if (isNum(g.free_cash_flow)) parts.push(g.free_cash_flow >= 0
    ? `Generated ${fmtMoneyText(g.free_cash_flow, currency)} of free cash flow over the last 12 months.`
    : `Used ${fmtMoneyText(-g.free_cash_flow, currency)} more cash than it generated over the last 12 months.`);
  return parts.join(" ");
}

// The two-sentence "what does it do" line under the header
function renderIntroSlot(win) {
  if (win.status.summary !== "ok") return slotPlaceholder(win, "summary");
  const s = win.data.summary;
  if (!s.text) return '<span class="na">No company description available.</span>';
  const sentences = s.text.match(/[^.!?]+[.!?]+(\s|$)/g) || [s.text];
  const short = sentences.slice(0, 2).join("").trim();
  return `<p class="prose">${escapeHtml(short)}</p>${s.text.length > short.length + 5 || s.method === "ai" ? `<button class="link-btn" data-goto="profile">${s.method === "ai" ? "AI summary · " : ""}More in Profile ${icon("external", 12)}</button>` : ""}`;
}

function renderSummarySlot(win, showSourceAlways) {
  if (win.status.summary !== "ok") return slotPlaceholder(win, "summary");
  const s = win.data.summary;
  if (!s.text) return '<div class="slot-msg">No company description is available from our sources.</div>';
  const method = s.method === "ai"
    ? `<div class="method" data-tip="${escapeHtml(s.note || "")}">AI summary of Yahoo's description + SEC details · checked against the source</div>`
    : `<div class="method" data-tip="${escapeHtml(s.note || "")}">Excerpt from Yahoo Finance's company description</div>`;
  const showSource = s.source_text && (s.method === "ai" || showSourceAlways) && s.source_text !== s.text;
  return `
    <p class="prose">${escapeHtml(s.text)}</p>
    ${method}
    ${showSource ? `<details class="more"><summary>Original source text</summary><div>${escapeHtml(s.source_text)}</div></details>` : ""}`;
}

function filingRow(f) {
  const glossaryKey = { "10-K": "form_10k", "10-Q": "form_10q", "8-K": "form_8k" }[f.form];
  const tip = glossaryKey ? GLOSSARY[glossaryKey].short : f.description;
  const period = f.period && f.form !== "8-K" ? ` · period ending ${fmtDay(f.period)}` : "";
  return `
    <a class="filing" href="${escapeHtml(f.url)}" target="_blank" rel="noopener">
      <span class="form-orb ${f.form.startsWith("10-K") ? "k" : ""}" data-tip="${escapeHtml(tip)}">${escapeHtml(f.form)}</span>
      <span class="desc">${escapeHtml(f.description)}<small>${escapeHtml(period)}</small></span>
      <span class="when">filed ${fmtDay(f.filed)} ↗</span>
    </a>`;
}

// Filings live behind a small button; this fills the popover.
function filingsPopoverHtml(filings, edgarUrl) {
  if (!filings || !filings.length) return '<h4>Filings</h4><p>No 10-K, 10-Q or 8-K filings found for this security.</p>';
  return `<h4>SEC filings</h4><p class="full">Official reports, newest first. The annual report (10-K) has the most detail.</p>
    <div class="filings pop">${filings.slice(0, 6).map(filingRow).join("")}</div>
    ${edgarUrl ? `<p><a class="ext" href="${escapeHtml(edgarUrl)}" target="_blank" rel="noopener">All filings on SEC EDGAR ↗</a></p>` : ""}`;
}


// =========================================================
// PROFILE
// =========================================================
function renderProfile(win) {
  const p = win.data.profile;
  const secNote = p.sources.sec && !p.sources.sec.ok
    ? `<span data-tip="${escapeHtml(p.sources.sec.message || "")}">SEC unavailable: Yahoo figures shown</span>` : "";
  const legend = `
    <div class="legend-line">
      <span><span class="src-badge sec" style="margin:0">SEC</span> official filing</span>
      <span><span class="src-badge" style="margin:0">Yahoo</span> data provider</span>
      <span>${BADGE.calc} calculated</span>
      <span>${BADGE.est} estimate</span>
      ${secNote}
    </div>`;

  return legend
    + section("01", "Business", "", `<div data-slot="summary" data-full="1">${renderSummarySlot(win, true)}</div>`)
    + profileBusiness(p)
    + profileSnapshot(p, win.ticker)
    + `<div class="cols">${profileHealth(p.health, p.statement_currency)}${profileValuation(p.valuation)}</div>`
    + profileGrowth(p.growth, p.statement_currency)
    + profileFacts(p);
}

// What the company sells and to whom, quoted from its own description
function profileBusiness(p) {
  const b = p.business || {};
  const topics = [["products", "Products & services"], ["segments", "Segments"], ["customers", "Customers & channels"], ["partnerships", "Partnerships"]];
  const shown = topics.filter(([key]) => (b[key] || []).length);
  const quotes = shown.map(([key, label]) => `
    <div class="quote-group"><span class="label">${label}</span>
      ${b[key].map((x) => `<p class="quote">${escapeHtml(x)}</p>`).join("")}</div>`).join("");
  const missing = [
    "Revenue split by segment and region (reported in the 10-K; not yet in a structured source)",
    "Named major customers and suppliers",
    "Competitors (no reliable structured source; MarketLab won't guess)",
  ];
  return section("03", "What it sells, to whom", "quoted from the company description", `
    ${quotes || '<p class="slot-msg">The description doesn\'t break down products or customers.</p>'}
    <div class="missing-list"><span class="label">Not available yet</span>${missing.map((m) => `<span class="missing">○ ${escapeHtml(m)}</span>`).join("")}</div>`);
}

function profileFacts(p) {
  const f = p.facts;
  const cur = p.currency;
  let website = NA;
  if (f.website) {
    const host = f.website.replace(/^https?:\/\//, "").replace(/\/$/, "");
    website = `<a href="${escapeHtml(f.website)}" target="_blank" rel="noopener">${escapeHtml(host)} ↗</a>`;
  }
  const founded = isNum(f.founded)
    ? `<span data-tip="From the 'founded in …' sentence in Yahoo's company description.">${f.founded}</span>`
    : '<span class="na" data-tip="Not stated in our sources.">N/A</span>';
  const sec = (label) => `${label}<span class="src-badge sec" data-tip="From the company's SEC registration.">SEC</span>`;

  const filingsButton = p.filings && p.filings.length
    ? `<button class="small-btn" data-filings data-tip="Official SEC filings (10-K, 10-Q, 8-K)">${icon("filings", 14)}Filings ↗</button>` : "";
  return section("02", "Company facts", filingsButton, `
    <dl class="facts">
      ${fact(f.legal_name ? sec("Legal name") : "Name", textOrNA(f.legal_name || p.name))}
      ${fact("CEO", textOrNA(f.ceo))}
      ${fact("Headquarters", f.headquarters ? `<span data-tip="Source: ${f.headquarters_source === "sec" ? "SEC business address" : "Yahoo Finance"}">${escapeHtml(f.headquarters)}</span>` : NA)}
      ${fact("Employees", isNum(f.employees) ? f.employees.toLocaleString("en-US") : NA)}
      ${fact("Founded", founded)}
      ${fact("Website", website)}
      ${fact(term("market_cap", "Market cap"), fmtMoney(f.market_cap, cur))}
      ${fact(term("ev", "Enterprise value"), fmtMoney(f.enterprise_value, cur))}
      ${fact("Exchange", textOrNA(f.exchange))}
      ${fact("Sector", f.sector ? `${escapeHtml(f.sector)} · ${escapeHtml(f.industry || "")}` : NA)}
      ${f.sec_industry ? fact(sec("Industry code"), escapeHtml(f.sec_industry)) : ""}
      ${f.fiscal_year_end ? fact(sec(term("fiscal_year", "Fiscal year ends")), escapeHtml(f.fiscal_year_end)) : ""}
    </dl>`);
}

// ---- Financial snapshot (kept from the previous version) ----
const SNAPSHOT_ROWS = [
  ["revenue", "Revenue", "revenue"],
  ["operating_income", "Operating income", "operating_income"],
  ["net_income", "Net income", "net_income"],
  ["free_cash_flow", "Free cash flow", "fcf"],
  ["gross_margin", "Gross margin", "gross_margin"],
  ["operating_margin", "Operating margin", "operating_margin"],
  ["eps", "Diluted EPS", "eps"],
];

function profileSnapshot(p, ticker) {
  const s = p.snapshot;
  const fin = p.statement_currency;
  if (!SNAPSHOT_ROWS.some(([key]) => s[key].points.length)) {
    return section("04", "Financial snapshot", "", '<div class="unavailable">○ No financial statements for this security (common for ETFs and funds).</div>');
  }
  const latestYear = s.revenue.latest_date ? `latest FY ends ${fmtPeriod(s.revenue.latest_date)}` : "";
  return section("04", "Financial snapshot", `annual · ${escapeHtml(fin || "")} · ${latestYear}`, `
    <div class="fin-table">
      <div class="fin-head label"><span>Click a row for history</span><span>Latest</span><span>${term("yoy")}</span><span class="spark-h">Trend</span></div>
      ${SNAPSHOT_ROWS.map(([key, label, glossaryKey]) => finRow(label, s[key], fin, glossaryKey, ticker)).join("")}
    </div>`);
}

function formatMetricValue(value, kind, currency) {
  if (kind === "percent") return fmtPct(value);
  if (kind === "per_share") return fmtPerShare(value, currency);
  return fmtMoney(value, currency);
}

function formatMetricChange(change, kind, hasPrior = false) {
  if (!isNum(change) && hasPrior) {
    return '<span class="na" data-tip="Not meaningful: the prior year was zero or negative, or the years aren\'t consecutive.">n/m</span>';
  }
  return kind === "percent" ? fmtPts(change) : fmtPct(change, true);
}

function finRow(label, m, currency, glossaryKey, ticker) {
  const latest = formatMetricValue(m.latest, m.kind, currency);
  const change = formatMetricChange(m.yoy, m.kind, m.points.length >= 2);
  const isCalc = m.kind === "percent";
  const plainKey = { gross_margin: "gross_margin", operating_margin: "operating_margin", fcf: "fcf" }[glossaryKey];
  const plain = plainKey && PLAIN[plainKey] ? PLAIN[plainKey](m.latest) : null;

  const history = m.points.length ? `
    <div class="history"><table>
      <tr><th>Fiscal year end</th>${m.points.map((p) => `<th>${fmtPeriod(p.date)}</th>`).join("")}</tr>
      <tr><td>${label}</td>${m.points.map((p) => `<td>${formatMetricValue(p.value, m.kind, currency)}</td>`).join("")}</tr>
      <tr><td>${m.kind === "percent" ? "Change" : "YoY"}</td>${m.points.map((p, i) => `<td>${signed(formatMetricChange(p.yoy, m.kind, i > 0), p.yoy)}</td>`).join("")}</tr>
      <tr><td>Source</td>${m.points.map((p) => `<td>${p.source === "sec" ? `<a href="${escapeHtml(p.url || "#")}" target="_blank" rel="noopener" data-tip="${escapeHtml(p.detail || "")}">SEC ↗</a>` : p.source === "yahoo" ? "Yahoo" : "calc"}</td>`).join("")}</tr>
    </table></div>` : '<span class="na">No history available.</span>';

  const why = GLOSSARY[glossaryKey]?.why;
  const explainValue = `${stripTags(latest)}${m.latest_date ? ` (FY ending ${fmtPeriod(m.latest_date)})` : ""}, YoY ${stripTags(change)}`;

  return `
    <details class="fin-row">
      <summary>
        <span class="label-cell">${term(glossaryKey, label, { plain, value: explainValue, section: "Financial snapshot" })}${isCalc ? BADGE.calc : ""}</span>
        <span class="num latest">${latest}${sourceBadge(m.source, m.detail, m.conflict, currency)}${m.latest_date ? `<small>FY ${fmtPeriod(m.latest_date)}</small>` : ""}</span>
        <span class="num yoy">${signed(change, m.yoy)}</span>
        <span class="spark-cell">${sparkline(m.points)}</span>
      </summary>
      <div class="fin-detail">
        ${history}
        ${plain ? `<p class="why"><b>${escapeHtml(ticker)}:</b> ${escapeHtml(plain)}</p>` : ""}
        ${why ? `<p class="why"><b>Why this matters.</b> ${escapeHtml(why)}</p>` : ""}
        <div class="detail-actions">
          <button class="explain-btn" data-explain-label="${escapeHtml(label)}" data-explain-value="${escapeHtml(explainValue)}" data-explain-section="Financial snapshot">✦ Explain ${escapeHtml(ticker)}'s ${escapeHtml(label.toLowerCase())}</button>
        </div>
      </div>
    </details>`;
}

// ---- Growth: a few oversized figures ----
function profileGrowth(g, fin) {
  let trajectory = "";
  if (isNum(g.revenue_yoy) && isNum(g.revenue_yoy_prior)) {
    const word = g.revenue_yoy > g.revenue_yoy_prior ? "accelerated" : g.revenue_yoy < g.revenue_yoy_prior ? "slowed" : "held steady";
    trajectory = `<p class="aside" style="margin-top:18px">Revenue growth ${word}: ${fmtPct(g.revenue_yoy_prior, true)} the year before, ${fmtPct(g.revenue_yoy, true)} in the latest fiscal year.</p>`;
  }
  const big = (key, label, value, badge = BADGE.calc) => `
    <div class="big"><div class="value">${signed(fmtPct(value, true), value)}</div><div class="label">${term(key, label)}${badge}</div></div>`;

  return section("05", "Growth", "calculated from annual results", `
    <div class="bigrow">
      ${big("revenue", "Revenue · latest FY", g.revenue_yoy)}
      ${big("cagr", "Revenue · 3-yr CAGR", g.revenue_cagr_3y)}
      ${big("eps", "EPS · latest FY", g.eps_yoy)}
      ${big("fcf", "Free cash flow · latest FY", g.fcf_yoy)}
    </div>
    <div class="spec" style="margin-top:14px">
      ${specRow({ key: "yoy", label: "Revenue · latest quarter YoY", value: signed(fmtPct(g.quarterly_revenue_yoy, true), g.quarterly_revenue_yoy) })}
      ${specRow({ key: "yoy", label: "Earnings · latest quarter YoY", value: signed(fmtPct(g.quarterly_earnings_yoy, true), g.quarterly_earnings_yoy) })}
    </div>
    ${trajectory}
    ${estimateTable(g.estimates, fin)}`);
}

function estimateTable(estimates, fin) {
  const rev = (estimates && estimates.revenue) || {};
  const eps = (estimates && estimates.eps) || {};
  const header = `<div class="label" style="margin-top:22px">${term("consensus", "Analyst estimates")} ${BADGE.est}</div>`;
  if (!rev.current_fy && !rev.next_fy && !eps.current_fy && !eps.next_fy) {
    return `${header}<p class="note">No consensus estimates available.</p>`;
  }
  const cell = (e, format) => e ? `${format(e.avg)} ${isNum(e.growth) ? `<small>${fmtPct(e.growth, true)}</small>` : ""}` : NA;
  const analysts = (e) => (e && isNum(e.analysts) ? `${e.analysts} analysts` : "");
  return `
    ${header}
    <table class="mini-table">
      <tr><th></th><th>Current FY</th><th>Next FY</th></tr>
      <tr><td>Revenue</td><td>${cell(rev.current_fy, (v) => fmtMoney(v, fin))}</td><td>${cell(rev.next_fy, (v) => fmtMoney(v, fin))}</td></tr>
      <tr><td>EPS</td><td>${cell(eps.current_fy, (v) => fmtPerShare(v, fin))}</td><td>${cell(eps.next_fy, (v) => fmtPerShare(v, fin))}</td></tr>
    </table>
    <p class="note">Average forecast; grey = implied growth vs. the prior year. ${escapeHtml(analysts(rev.current_fy) || analysts(eps.current_fy))}</p>`;
}

function profileValuation(v) {
  const ctx = { pe: v.pe };
  const fcfNote = v.fcf_yield_basis ? `<small>FY ${fmtPeriod(v.fcf_yield_basis)}</small>` : "";
  return section("06", "Valuation", "current multiples", `
    <div class="spec" style="grid-template-columns:1fr">
      ${specRow({ key: "pe", label: "P/E · TTM", value: peValue(v.pe), plain: PLAIN.pe(v.pe) || "No P/E: no profit over the last 12 months.", sectionName: "Valuation" })}
      ${specRow({ key: "forward_pe", label: "Forward P/E", value: peValue(v.forward_pe), badge: BADGE.est, plain: PLAIN.forward_pe(v.forward_pe, ctx), sectionName: "Valuation" })}
      ${specRow({ key: "ps", label: "Price / sales", value: fmtMultiple(v.price_to_sales), plain: PLAIN.ps(v.price_to_sales), sectionName: "Valuation" })}
      ${specRow({ key: "pb", label: "Price / book", value: fmtMultiple(v.price_to_book), plain: PLAIN.pb(v.price_to_book), sectionName: "Valuation" })}
      ${specRow({ key: "ev_ebitda", label: "EV / EBITDA", value: peValue(v.ev_to_ebitda), plain: PLAIN.ev_ebitda(v.ev_to_ebitda), sectionName: "Valuation" })}
      ${specRow({ key: "peg", label: "PEG", value: fmtNumber(v.peg), badge: BADGE.est, plain: PLAIN.peg(v.peg), sectionName: "Valuation" })}
      ${specRow({ key: "fcf_yield", label: "FCF yield", value: fmtPct(v.fcf_yield, false, 2) + fcfNote, badge: BADGE.calc, plain: PLAIN.fcf_yield(v.fcf_yield), sectionName: "Valuation" })}
    </div>
    <p class="note">Multiples describe what the market currently pays. Whether that's high or low depends on the industry and the company's growth.</p>`);
}

function profileHealth(h, fin) {
  const ctx = { currency: fin };
  let netLabel = "Net cash / debt";
  let netValue = NA;
  if (isNum(h.net_cash)) {
    netLabel = h.net_cash >= 0 ? "Net cash" : "Net debt";
    netValue = fmtMoney(Math.abs(h.net_cash), fin);
  }
  const fcfDate = h.free_cash_flow_date ? `<small>FY ${fmtPeriod(h.free_cash_flow_date)}</small>` : "";
  const coverageDate = h.interest_coverage_date ? `<small>FY ${fmtPeriod(h.interest_coverage_date)}</small>` : "";
  return section("07", "Financial health", h.as_of ? `as of ${fmtPeriod(h.as_of)} · Yahoo` : "", `
    <div class="spec" style="grid-template-columns:1fr">
      ${specRow({ key: "cash", label: "Cash & ST investments", value: fmtMoney(h.cash, fin), sectionName: "Financial health" })}
      ${specRow({ key: "total_debt", label: "Total debt", value: fmtMoney(h.total_debt, fin), sectionName: "Financial health" })}
      ${specRow({ key: "net_cash", label: netLabel, value: netValue, badge: BADGE.calc, plain: PLAIN.net_cash(h.net_cash, ctx), sectionName: "Financial health" })}
      ${specRow({ key: "current_ratio", label: "Current ratio", value: fmtMultiple(h.current_ratio, 2), badge: BADGE.calc, plain: PLAIN.current_ratio(h.current_ratio), sectionName: "Financial health" })}
      ${specRow({ key: "debt_equity", label: "Debt / equity", value: fmtMultiple(h.debt_to_equity, 2), badge: BADGE.calc, plain: PLAIN.debt_equity(h.debt_to_equity), sectionName: "Financial health" })}
      ${specRow({ key: "fcf", label: "Free cash flow", value: fmtMoney(h.free_cash_flow, fin) + fcfDate, plain: PLAIN.fcf(h.free_cash_flow), sectionName: "Financial health" })}
      ${specRow({ key: "interest_coverage", label: "Interest coverage", value: fmtMultiple(h.interest_coverage) + coverageDate, badge: BADGE.calc,
                  plain: PLAIN.interest_coverage(h.interest_coverage) || "N/A when interest expense isn't reported separately or operating profit is negative.", sectionName: "Financial health" })}
    </div>`);
}
