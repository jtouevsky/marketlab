// =========================================================
// tab_risk.js — the Risk tab: how the stock has behaved, and what
// could hurt it. Measurements and reported facts only; NO risk score.
// Data: /api/stock/<T>/risk?period=full|5y&custom=N (tab_risk.py)
// =========================================================

function renderRisk(win) {
  const r = win.data.risk;
  const p = win.riskParams || { period: "full", custom: null };
  const selected = r.horizons.find((h) => h.label === win.riskHorizon) || r.horizons.find((h) => h.custom) || r.horizons[2];
  win.riskHorizon = selected.label;

  const controls = `
    <div class="risk-controls">
      <div><span class="label">Holding period</span>
        <div class="seg">${r.horizons.filter((h) => !h.custom).map((h) => `<button class="${h.label === selected.label ? "on" : ""}" data-risk-h="${h.label}">${h.label}</button>`).join("")}
          <form class="seg-input ${selected.custom ? "on" : ""}" data-risk-custom><input value="${selected.custom ? selected.days : ""}" placeholder="custom" inputmode="numeric" aria-label="Custom holding period in trading days"><span>days</span></form></div></div>
      <div><span class="label">History</span>
        <div class="seg"><button class="${p.period !== "5y" ? "on" : ""}" data-risk-period="full">All (${fmtDay(r.tested_from).slice(-4)}–)</button><button class="${p.period === "5y" ? "on" : ""}" data-risk-period="5y">Last 5 years</button></div></div>
    </div>`;

  return `
    <div class="risk-intro"><p class="aside">How ${escapeHtml(r.ticker)} has behaved, measured from ${r.trading_days.toLocaleString("en-US")} trading days of prices, and the reported facts that bear on risk.
      There's no overall risk score: each section measures something different.</p></div>
    ${riskGlanceHtml(win, r, selected)}
    ${controls}
    ${horizonSection(r, selected)}
    <div data-risk-intel>${riskIntelHtml(win)}</div>
    ${fundamentalSection(r)}
    ${drawdownSection(r)}
    <details class="risk-advanced" ${win.riskAdvancedOpen ? "open" : ""} data-risk-adv><summary><b>Advanced statistics</b><span class="rp-fine">volatility · market sensitivity · bad days · upside vs downside</span></summary>
      ${volatilitySection(r)}
      ${marketSection(r)}
      ${tailSection(r)}
      ${asymmetrySection(r)}
    </details>
    ${analystSection(r)}
    <div data-risk-sources>${sourcesSection(r, win)}</div>
    <p class="data-foot">Prices: ${escapeHtml(r.data.provider)}. Data checks: ${escapeHtml(qualityText(r.data.quality))}. All statistics describe the past and are not forecasts.</p>`;
}

// The decision-relevant picture first: holding-period range, worst drawdown, market sensitivity,
// funding, and (once the filings are read) the top dependencies. Everything is detailed below.
function riskGlanceHtml(win, r, h) {
  const s = h.stats;
  const spy = (r.market || []).find((m) => m.symbol === "SPY");
  const beta = spy?.y1?.beta;
  const f = r.fundamental || {};
  const ri = win.riskIntel;
  const deps = ri?.dependencies?.entities ? ri.dependencies.entities.filter((e) => e.relationship !== "Competitor").slice(0, 3).map((e) => e.name) : null;
  const tile = (label, value, sub, cls = "") => `<div class="glance-tile"><span class="label">${label}</span><b class="${cls}">${value}</b><small>${sub}</small></div>`;
  return `<section class="risk-glance glass-flat" aria-label="Risk at a glance"><div class="glance-head"><h3>Risk at a glance</h3><span class="rp-fine">from ${r.trading_days.toLocaleString("en-US")} trading days and the latest filings · details below</span></div>
    <div class="glance-grid">
      ${s ? tile(`Holding ${escapeHtml(h.label)}`, `${fmtPct(s.p5, true, 0)} to ${fmtPct(s.p95, true, 0)}`, `90% of past ${escapeHtml(h.label)} periods · ${fmtPct(s.p_negative, false, 0)} ended lower`) : ""}
      ${r.drawdowns?.max ? tile("Worst drawdown", fmtPct(r.drawdowns.max.depth, true, 0), `${fmtDay(r.drawdowns.max.peak)} → ${fmtDay(r.drawdowns.max.trough)} · now ${fmtPct(r.drawdowns.current, true, 0)} from the high`, "down") : ""}
      ${isNum(beta) ? tile("Market sensitivity", `β ${beta.toFixed(2)}`, beta > 1.2 ? "moves more than the S&P 500" : beta < 0.8 ? "moves less than the S&P 500" : "moves about with the S&P 500") : ""}
      ${isNum(r.volatility?.one_year) ? tile("Volatility (1 year)", fmtPct(r.volatility.one_year, false, 0), "annualised") : ""}
      ${f && (f.burning_cash != null) ? tile("Funding", f.burning_cash ? "Burning cash" : "Self-funding", f.burning_cash && isNum(f.runway_years) ? `≈${f.runway_years.toFixed(1)} years of cash at the current rate` : "positive free cash flow", f.burning_cash ? "down" : "") : ""}
      ${tile("Top dependencies", deps ? (deps.length ? escapeHtml(deps.join(" · ")) : "none named") : "reading filings…", deps ? "named in the latest annual report" : "about 20 seconds the first time", "glance-text")}
    </div></section>`;
}

function qualityText(q) {
  if (!q) return "n/a";
  return `${q.likely_error || 0} likely errors corrected, ${q.warning || 0} warnings, ${q.info || 0} notes`;
}

// ---------- 01 Holding period ----------
function riskLabButton(r, h) {
  const s = h.stats;
  if (!s || s.p5 >= 0) return "";
  const days = Math.min(h.days, 63);
  const threshold = Math.max(1, Math.floor(Math.abs(s.p5) * 200) / 2);
  const preset = { conditions: [{ id: "c1", type: "price_move", params: { direction: "falls", threshold: Math.min(threshold, 95), window: days } }],
                   outcome: { type: "forward_return", horizon: Math.min(h.days, 252) } };
  const note = `A fall of ${threshold}% over ${days} trading days is about as bad as the worst 5% of ${h.label} periods. This tests what came after.`;
  return `<button class="small-btn lab-jump" data-lab-preset='${escapeHtml(JSON.stringify(preset))}' data-lab-from="Risk" data-lab-note="${escapeHtml(note)}"
    data-tip="Open the Lab: what happened after falls as bad as the worst 5% of these periods?">${icon("lab", 13)}Explore historically</button>`;
}

function horizonSection(r, h) {
  const s = h.stats;
  if (!s) return section("01", `Holding for ${h.label}`, "", '<p class="slot-msg">Not enough history for this holding period.</p>');
  const few = s.independent_periods < 20;
  const table = r.horizons.filter((x) => x.stats).map((x) => `
    <tr class="${x.label === h.label ? "sel" : ""}"><td>${x.label}</td><td class="${signClass(x.stats.median)}">${fmtPct(x.stats.median, true, 1)}</td>
      <td class="down">${fmtPct(x.stats.p5, true, 1)}</td><td class="up">${fmtPct(x.stats.p95, true, 1)}</td><td>${fmtPct(x.stats.p_negative, false, 0)}</td>
      <td class="down">${fmtPct(x.stats.worst.value, true, 0)}</td></tr>`).join("");
  return section("01", `Holding for ${h.label}`, `${term("rolling_returns", "every start day")} · ${s.n.toLocaleString("en-US")} windows ${riskLabButton(r, h)}`, `
    <div class="bigrow">
      <div class="big"><div class="value ${signClass(s.median)}">${fmtPct(s.median, true, 1)}</div><div class="label">${term("lab_median", "Median")} return</div></div>
      <div class="big"><div class="value">${fmtPct(s.p_negative, false, 0)}</div><div class="label">of periods ended lower</div></div>
      <div class="big"><div class="value down">${fmtPct(s.worst.value, true, 0)}</div><div class="label">Worst · ${fmtDay(s.worst.start)}</div></div>
      <div class="big"><div class="value up">${fmtPct(s.best.value, true, 0)}</div><div class="label">Best · ${fmtDay(s.best.start)}</div></div>
    </div>
    ${histogramSvg(s.histogram, { p5: s.p5, p95: s.p95, median: s.median })}
    <div class="pct-line">
      <span>5th pct <b class="${signClass(s.p5)}">${fmtPct(s.p5, true, 1)}</b></span><span>25th <b class="${signClass(s.p25)}">${fmtPct(s.p25, true, 1)}</b></span>
      <span>75th <b class="${signClass(s.p75)}">${fmtPct(s.p75, true, 1)}</b></span><span>95th <b class="${signClass(s.p95)}">${fmtPct(s.p95, true, 1)}</b></span>
      <span>Mean <b>${fmtPct(s.mean, true, 1)}</b></span><span>Std dev <b>${fmtPct(s.std, false, 1)}</b></span>
    </div>
    ${few ? `<div class="warning">Only about ${s.independent_periods} non-overlapping ${h.label} periods exist in this history. The windows overlap heavily, so these percentiles rest on few independent experiences.</div>` : ""}
    <p class="note">5% of ${h.label} holding periods did worse than ${fmtPct(s.p5, true, 1)}; 5% did better than ${fmtPct(s.p95, true, 1)}. ${escapeHtml(r.notes.horizons)}</p>
    <div class="table-scroll"><table class="data-table compact">
      <thead><tr><th>Hold</th><th>Median</th><th>5th pct</th><th>95th pct</th><th>Ended lower</th><th>Worst</th></tr></thead><tbody>${table}</tbody></table></div>`);
}

function histogramSvg(hist, marks) {
  if (!hist) return "";
  // Bars are SVG (stretched to the width); labels are HTML so the text never gets distorted.
  const W = 640, H = 130;
  const n = hist.counts.length;
  const max = Math.max(...hist.counts);
  const span = hist.width * n;
  const pct = (v) => ((v - hist.start) / span) * 100;
  const inside = (v) => isNum(v) && v >= hist.start && v <= hist.start + span;
  const bars = hist.counts.map((c, i) => {
    const mid = hist.start + (i + 0.5) * hist.width;
    const h = (c / max) * (H - 4);
    return `<rect x="${(i * W) / n + 0.5}" y="${H - h}" width="${Math.max(0.5, W / n - 1)}" height="${h}" rx="1.5" class="${mid >= 0 ? "up" : "down"}"/>`;
  }).join("");
  const marker = (v, cls, label) => inside(v) ? `<span class="hm ${cls}" style="left:${pct(v)}%"><em>${label}</em></span>` : "";
  return `<div class="hist">
    <div class="hist-plot"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Distribution of returns">${bars}</svg>
      ${marker(0, "zero", "0%")}${marker(marks.p5, "pct", "5th")}${marker(marks.median, "med", "median")}${marker(marks.p95, "pct", "95th")}</div>
    <div class="hist-axis"><span>${fmtPct(hist.start, true, 0)}</span><span>${fmtPct(hist.start + span, true, 0)}</span></div>
    ${hist.clipped_below || hist.clipped_above ? `<span class="hist-note">The outermost bars also hold ${hist.clipped_below + hist.clipped_above} extreme windows beyond the axis.</span>` : ""}</div>`;
}

function seriesSvg(series, { area = false, fmt = (v) => fmtPct(v, false, 0), cls = "", second = null, height = 120 } = {}) {
  if (!series || series.value.length < 2) return "";
  const all = [...series.value, ...(second ? second.value : [])].filter(isNum);
  const lo = area ? Math.min(...all) : Math.min(...all), hi = area ? 0 : Math.max(...all);
  const W = 640, H = height, pad = 6;
  const span = hi - lo || 1;
  const path = (s) => s.value.map((v, i) => `${i ? "L" : "M"}${((i / (s.value.length - 1)) * W).toFixed(1)},${(pad + ((hi - v) / span) * (H - 2 * pad)).toFixed(1)}`).join("");
  const main = path(series);
  return `<div class="series"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="${cls}">
      ${area ? `<path d="${main}L${W},${pad}L0,${pad}Z" class="area"/>` : ""}
      <path d="${main}" class="line"/>${second ? `<path d="${path(second)}" class="line second"/>` : ""}
    </svg><div class="series-axis"><span>${fmtDay(series.date[0])}</span><span>${area ? `lowest ${fmt(lo)}` : `range ${fmt(lo)} – ${fmt(hi)}`}</span><span>${fmtDay(series.date[series.date.length - 1])}</span></div></div>`;
}

// ---------- 02 Drawdowns ----------
function drawdownSection(r) {
  const d = r.drawdowns;
  const m = d.max;
  const rows = d.episodes.map((e) => `<tr><td>${fmtDay(e.peak)}</td><td class="down">${fmtPct(e.depth, true, 0)}</td><td>${fmtDay(e.trough)}</td>
    <td>${e.days_to_trough} d</td><td>${e.recovered ? `${fmtDay(e.recovered)} <small>(${e.days_to_recover} d)</small>` : '<span class="na">not yet</span>'}</td></tr>`).join("");
  return section("02", term("drawdown", "Drawdowns"), "falls from a previous high", `
    <div class="bigrow">
      <div class="big"><div class="value ${d.current < -0.005 ? "down" : ""}">${d.current < -0.0005 ? fmtPct(d.current, true, 1) : "0%"}</div><div class="label">Now · below the ${fmtDay(d.current_peak)} high</div></div>
      <div class="big"><div class="value down">${m ? fmtPct(m.depth, true, 0) : NA}</div><div class="label">Deepest${m ? ` · ${fmtDay(m.peak)} → ${fmtDay(m.trough)}` : ""}</div></div>
      <div class="big"><div class="value">${d.significant_count}</div><div class="label">falls of ${fmtPct(d.significant_threshold, false, 0)} or more</div></div>
      <div class="big"><div class="value">${isNum(d.median_recovery_days) ? `${Math.round(d.median_recovery_days)} d` : NA}</div><div class="label">median recovery (trading days)</div></div>
    </div>
    ${seriesSvg(d.underwater, { area: true, cls: "underwater" })}
    ${m ? `<p class="aside" style="margin-top:12px">The deepest fall took ${m.days_to_trough} trading days to reach bottom and ${m.recovered ? `${m.days_to_recover} more to recover (${fmtDay(m.recovered)})` : "has not fully recovered"}.
      ${isNum(d.average_significant) ? `Falls of ${fmtPct(d.significant_threshold, false, 0)}+ averaged ${fmtPct(d.average_significant, true, 0)}.` : ""}</p>` : ""}
    ${rows ? `<div class="table-scroll"><table class="data-table compact"><thead><tr><th>Peak</th><th>Depth</th><th>Bottom</th><th>Fall took</th><th>Recovered</th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}`);
}

// ---------- 03 Volatility ----------
function volatilitySection(r) {
  const v = r.volatility;
  const pct = isNum(v.rolling_30_percentile) ? v.rolling_30_percentile : null;
  return section("03", term("volatility", "Volatility"), "annualised", `
    <div class="bigrow">
      <div class="big"><div class="value">${fmtPct(v.rolling_30, false, 0)}</div><div class="label">Last 30 days</div></div>
      <div class="big"><div class="value">${fmtPct(v.rolling_90, false, 0)}</div><div class="label">Last 90 days</div></div>
      <div class="big"><div class="value">${fmtPct(v.one_year, false, 0)}</div><div class="label">Last year</div></div>
      <div class="big"><div class="value">${fmtPct(v.full, false, 0)}</div><div class="label">Whole history</div></div>
    </div>
    ${seriesSvg(v.series_30, { second: v.series_90, cls: "vol" })}
    <div class="dist-legend"><span><i class="k line"></i>30-day</span><span><i class="k dash"></i>90-day</span><span>last 5 years</span></div>
    ${pct !== null ? `<p class="aside" style="margin-top:12px">Recent 30-day volatility is higher than ${fmtPct(pct, false, 0)} of all 30-day windows in the stock's history.</p>` : ""}
    <p class="note">${escapeHtml(GLOSSARY.volatility.why)}</p>`);
}

// ---------- 04 Market relationships ----------
function marketSection(r) {
  const row = (m) => m.error ? `<tr><td>${escapeHtml(m.label)}</td><td colspan="4" class="na">unavailable</td></tr>` : `
    <tr><td>${escapeHtml(m.label)}</td><td>${fmtNumber(m.y1?.beta)}</td><td>${fmtNumber(m.y1?.correlation)}</td><td>${fmtNumber(m.y3?.beta)}</td><td>${fmtNumber(m.y3?.correlation)}</td></tr>`;
  const sens = r.sensitivities.map((s) => `<div class="fact"><dt class="label">${escapeHtml(s.label)}</dt><dd>${fmtNumber(s.correlation)}</dd></div>`).join("");
  return section("04", "Moves with the market", "daily returns", `
    <div class="table-scroll"><table class="data-table">
      <thead><tr><th></th><th>${term("beta", "Beta")} · 1Y</th><th>${term("correlation", "Corr.")} · 1Y</th><th>Beta · 3Y</th><th>Corr. · 3Y</th></tr></thead>
      <tbody>${r.market.map(row).join("")}</tbody></table></div>
    <p class="note">${escapeHtml(r.notes.beta)} ${escapeHtml(r.notes.spy_qqq)}</p>
    <div class="label" style="margin-top:22px">Correlation with other assets · 3 years</div>
    <dl class="facts">${sens}</dl>
    <p class="note">Near 0 means no consistent day-to-day relationship. These are associations in past data, not causes.</p>`);
}

// ---------- 05 Tail risk ----------
function tailSection(r) {
  const t = r.tail;
  if (!t) return "";
  return section("05", "Bad days", t.window, `
    <div class="spec">
      ${specRow({ key: "var", label: "VaR · 95%, 1 day", value: `<span class="down">${fmtPct(t.var95, true, 1)}</span>`, plain: `On 1 in 20 days the stock fell ${fmtPct(-t.var95, false, 1)} or more.` })}
      ${specRow({ key: "es", label: "Expected shortfall · 95%", value: `<span class="down">${fmtPct(t.es95, true, 1)}</span>`, plain: "The average of those worst 5% of days." })}
      ${specRow({ key: "var", label: "VaR · 99%, 1 day", value: `<span class="down">${fmtPct(t.var99, true, 1)}</span>`, plain: `On 1 in 100 days it fell ${fmtPct(-t.var99, false, 1)} or more.` })}
      ${specRow({ key: "es", label: "Expected shortfall · 99%", value: `<span class="down">${fmtPct(t.es99, true, 1)}</span>` })}
      ${specRow({ key: "downside_dev", label: "Downside deviation", value: fmtPct(t.downside_deviation, false, 0), plain: "Annualised, counting only down days." })}
      ${specRow({ key: "sortino", label: "Sortino ratio", value: fmtNumber(t.sortino), plain: `Average return of ${fmtPct(t.annual_mean, true, 0)} a year over the period, per unit of downside deviation.` })}
    </div>
    <p class="note">${escapeHtml(r.notes.tail)}</p>`);
}

// ---------- 06 Long / short asymmetry ----------
function asymmetrySection(r) {
  const a = r.asymmetry;
  const moves = a.moves.map((m) => `<tr><td>${m.days === 1 ? "1 day" : `${m.days} days`}</td>
    <td class="up">${fmtPct(m.largest_gain.value, true, 0)} <small>${fmtDay(m.largest_gain.start)}</small></td>
    <td class="down">${fmtPct(m.largest_loss.value, true, 0)} <small>${fmtDay(m.largest_loss.start)}</small></td></tr>`).join("");
  const g = a.gaps;
  const si = a.short_interest;
  const siChange = isNum(si.shares_short) && isNum(si.prior_month) && si.prior_month ? si.shares_short / si.prior_month - 1 : null;
  return section("06", "Long vs short risk", "upside and downside extremes", `
    <div class="table-scroll"><table class="data-table compact"><thead><tr><th>Over</th><th>Largest gain</th><th>Largest loss</th></tr></thead><tbody>${moves}</tbody></table></div>
    <p class="note">A holder is exposed to the losses; someone betting against the stock (short) is exposed to the gains, which have no upper limit.</p>
    <div class="cols" style="margin-top:8px">
      <div>
        <div class="label" style="margin:18px 0 4px">${term("gap", "Overnight gaps")} · since ${fmtDay(g?.since)}</div>
        ${g ? `<dl class="facts" style="grid-template-columns:1fr">
          <div class="fact"><dt>Largest gap up</dt><dd class="up">${fmtPct(g.largest_up.value, true, 1)} <small>${fmtDay(g.largest_up.date)}</small></dd></div>
          <div class="fact"><dt>Largest gap down</dt><dd class="down">${fmtPct(g.largest_down.value, true, 1)} <small>${fmtDay(g.largest_down.date)}</small></dd></div>
          <div class="fact"><dt>Opened 5%+ away from prior close</dt><dd>${fmtPct(g.share_over_5pct, false, 1)} of sessions</dd></div>
          <div class="fact"><dt>Opened 10%+ lower</dt><dd>${g.count_down_over_10pct} times</dd></div></dl>` : '<p class="slot-msg">Not available.</p>'}
      </div>
      <div>
        <div class="label" style="margin:18px 0 4px">${term("short_interest", "Short interest")}${si.as_of ? ` · as of ${fmtDay(si.as_of)}` : ""}</div>
        <dl class="facts" style="grid-template-columns:1fr">
          <div class="fact"><dt>Of shares available to trade</dt><dd>${fmtPct(si.percent_of_float, false, 1)}</dd></div>
          <div class="fact"><dt>Shares sold short</dt><dd>${fmtCompact(si.shares_short)}${isNum(siChange) ? ` <small class="${signClass(siChange)}">${fmtPct(siChange, true, 0)} vs prior month</small>` : ""}</dd></div>
          <div class="fact"><dt>${term("days_to_cover", "Days to cover")}</dt><dd>${fmtNumber(si.days_to_cover, 1)}</dd></div></dl>
        <p class="note">${escapeHtml(si.source)}.</p>
      </div>
    </div>`);
}

// ---------- 07 Fundamental risk ----------
function fundamentalSection(r) {
  const f = r.fundamental;
  if (!f) return section("07", "Fundamental risk", "", '<p class="slot-msg">Financial statements unavailable for this security.</p>');
  const cur = f.currency;
  const d = f.dilution || {};
  const dil = (x) => (x ? `${signed(fmtPct(x.value, true, 1), -x.value)} <small>${fmtPeriod(x.from)} → ${fmtPeriod(x.to)}${x.split_adjusted ? ", split-adjusted" : ""}</small>` : NA);
  return section("07", "Fundamental risk", f.as_of ? `financial position · balance sheet as of ${fmtPeriod(f.as_of)}` : "", `
    <div class="spec">
      ${specRow({ key: "cash", label: "Cash & ST investments", value: fmtMoney(f.cash, cur) })}
      ${specRow({ key: "total_debt", label: "Total debt", value: fmtMoney(f.total_debt, cur) })}
      ${specRow({ key: "net_cash", label: isNum(f.net_cash) && f.net_cash < 0 ? "Net debt" : "Net cash", value: fmtMoney(isNum(f.net_cash) ? Math.abs(f.net_cash) : null, cur), plain: PLAIN.net_cash(f.net_cash, { currency: cur }) })}
      ${specRow({ key: "fcf", label: "Free cash flow", value: `${fmtMoney(f.free_cash_flow, cur)}${f.free_cash_flow_date ? `<small>FY ${fmtPeriod(f.free_cash_flow_date)}</small>` : ""}`, plain: PLAIN.fcf(f.free_cash_flow) })}
      ${f.burning_cash ? specRow({ key: "runway", label: "Cash runway", value: isNum(f.runway_years) ? `${f.runway_years.toFixed(1)} yrs` : NA,
        plain: "At last fiscal year's burn rate, with today's cash. Burn rates change, and companies can raise money." }) : ""}
      ${specRow({ key: "dilution", label: "Share count · 1 year", value: dil(d.one_year), plain: d.source ? "From the share counts on the cover of SEC filings." : "SEC share counts unavailable." })}
      ${specRow({ key: "dilution", label: "Share count · 3 years", value: dil(d.three_year) })}
      ${specRow({ key: "operating_margin", label: "Operating margin", value: fmtPct(f.operating_margin), plain: PLAIN.operating_margin(f.operating_margin) })}
      ${specRow({ key: "current_ratio", label: "Current ratio", value: fmtMultiple(f.current_ratio, 2), plain: PLAIN.current_ratio(f.current_ratio) })}
      ${specRow({ key: "interest_coverage", label: "Interest coverage", value: fmtMultiple(f.interest_coverage), plain: PLAIN.interest_coverage(f.interest_coverage) || "Not meaningful or not reported." })}
    </div>
    ${(f.operating_margin_history || []).length >= 2 ? `<p class="note">Operating margin by fiscal year: ${f.operating_margin_history.map((p) => `${fmtPeriod(p.date)} ${stripTags(fmtPct(p.value))}`).join(" · ")}</p>` : ""}`);
}

// ---------- 08 Analysts (attributed, not ours) ----------
function analystSection(r) {
  const a = r.analysts;
  const t = a.targets;
  let targets = '<p class="slot-msg">No analyst price targets available.</p>';
  if (t && isNum(t.low) && isNum(t.high) && t.high > t.low) {
    const pos = (v) => Math.min(100, Math.max(0, ((v - t.low) / (t.high - t.low)) * 100));
    targets = `
      <div class="range">
        <div class="range-track wide">
          ${isNum(t.mean) ? `<span class="tick" style="left:${pos(t.mean)}%" data-tip="Average target ${fmtPrice(t.mean)}"></span>` : ""}
          ${isNum(a.price) ? `<span class="range-marker" style="left:${pos(Math.min(Math.max(a.price, t.low), t.high))}%" data-tip="Current price ${fmtPrice(a.price)}"></span>` : ""}
        </div>
        <div class="range-ends"><span>Lowest ${fmtPrice(t.low)}</span><span>Average ${fmtPrice(t.mean)}</span><span>Highest ${fmtPrice(t.high)}</span></div>
      </div>`;
  }
  const rt = a.ratings;
  const total = rt ? Object.values(rt).reduce((x, y) => x + y, 0) : 0;
  const labels = { strongBuy: "Strong buy", buy: "Buy", hold: "Hold", sell: "Sell", strongSell: "Strong sell" };
  const order = Object.keys(labels).filter((k) => rt && k in rt);   // fixed order (JSON key order isn't guaranteed)
  const ratings = rt && total ? `
    <div class="label" style="margin-top:22px">Analyst ratings this month · ${total} analysts</div>
    <div class="rating-bar">${order.filter((k) => rt[k]).map((k) => `<span class="r-${k}" style="flex:${rt[k]}" data-tip="${labels[k]}: ${rt[k]}">${rt[k]}</span>`).join("")}</div>
    <div class="rating-legend">${order.map((k) => `<span><i class="r-${k}"></i>${labels[k]} ${rt[k]}</span>`).join("")}</div>` : "";
  const e = a.eps_dispersion;
  return section("12", "Analyst & professional views", "attributed · not MarketLab's view", `
    <div class="label">12-month price targets${isNum(a.analyst_count) ? ` · ${a.analyst_count} analysts` : ""}</div>
    ${targets}
    ${ratings}
    ${e ? `<p class="aside" style="margin-top:18px">Estimates for ${escapeHtml(e.period)} range from ${fmtPerShare(e.low)} to ${fmtPerShare(e.high)} (average ${fmtPerShare(e.avg)}, ${e.analysts} analysts).
      A wide range means analysts disagree about the outlook.</p>` : ""}
    <p class="note">Source: ${escapeHtml(a.source)}. These are other people's opinions, shown so you can see the range of views. MarketLab doesn't make buy or sell recommendations.</p>`);
}

// ---------- 09 Company-specific ----------
function companySection(r) {
  const s = r.sector;
  const filing = s.risk_factors_filing;
  return section("09", "Company-specific risks", s.sector ? `${escapeHtml(s.sector)} · ${escapeHtml(s.industry || "")}` : "", `
    <p class="aside">${escapeHtml(s.note)}</p>
    ${filing ? `<p style="margin-top:12px"><a class="small-btn" href="${escapeHtml(filing.url)}" target="_blank" rel="noopener">${icon("filings", 14)}Risk factors · ${escapeHtml(filing.form)} filed ${fmtDay(filing.filed)} ↗</a></p>` : ""}
    <div class="missing-list"><span class="label">Not measured yet</span>${r.unavailable.map((u) => `<span class="missing" data-tip="${escapeHtml(u.why)}">○ ${escapeHtml(u.item)}</span>`).join("")}</div>`);
}

function afterRisk(win) {
  if (!win.riskIntel && !win.riskIntelLoading) loadRiskIntel(win);     // tab_risk_intel.js (lazy, slower sources)
  const adv = win.el.querySelector("[data-risk-adv]");
  if (adv) adv.addEventListener("toggle", () => { win.riskAdvancedOpen = adv.open; });
  const form = win.el.querySelector("[data-risk-custom]");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const days = parseInt(form.querySelector("input").value, 10);
    if (!Number.isFinite(days) || days < 1 || days > 2520) { showToast("Enter a holding period between 1 and 2520 trading days."); return; }
    win.riskParams = { ...(win.riskParams || { period: "full" }), custom: days };
    win.riskHorizon = `${days}D`;
    reloadPart(win, "risk");
  });
}

function riskClick(win, event) {
  if (riskIntelClick(win, event)) return true;
  const h = event.target.closest("[data-risk-h]");
  const period = event.target.closest("[data-risk-period]");
  if (h) {
    win.riskHorizon = h.dataset.riskH;
    const body = win.el.querySelector(".window-body");
    const scroll = body.scrollTop;
    body.innerHTML = renderRisk(win);
    renumberSections(body);
    afterRisk(win);
    body.scrollTop = scroll;
    return true;
  }
  if (period) {
    win.riskParams = { ...(win.riskParams || {}), period: period.dataset.riskPeriod };
    reloadPart(win, "risk");
    return true;
  }
  return false;
}
