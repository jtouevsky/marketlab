// =========================================================
// tab_earnings.js — the Earnings tab: next report, latest report,
// history (EPS vs estimate, revenue, the stock's move afterwards).
// Data: /api/stock/<T>/earnings (tab_earnings.py)
// =========================================================

const TIMING_TEXT = { before_open: "before the open", after_close: "after the close", during_market: "during market hours", unknown: "time not confirmed" };

function renderEarnings(win) {
  const e = win.data.earnings;
  const cur = e.currency;
  return nextReportHtml(e, cur) + latestReportHtml(e, cur) + historyHtml(e, cur) + revenueBarsHtml(e, cur) + `
    ${section("05", "Guidance", "", `<div class="unavailable">${icon("info", 15)} ${escapeHtml(e.notes.guidance)}</div>
      <p class="note">Company guidance (what management expects) is different from analyst estimates (what outside analysts expect). MarketLab only shows the estimates.</p>`)}
    <p class="data-foot">Sources: ${escapeHtml(e.sources.estimates)}; revenue from ${escapeHtml(e.sources.revenue)}; prices from ${escapeHtml(e.sources.prices)}.
      ${escapeHtml(e.notes.eps)}</p>`;
}

function nextReportHtml(e, cur) {
  const n = e.next;
  if (!n) return section("01", "Next report", "", '<p class="slot-msg">No upcoming report date published yet.</p>');
  const days = Math.round((new Date(`${n.date}T00:00:00`) - new Date(new Date().toDateString())) / 86400000);
  const range = (lo, hi, fmt) => (isNum(lo) && isNum(hi) ? `<small>range ${fmt(lo)} – ${fmt(hi)}</small>` : "");
  return section("01", "Next report", "Yahoo Finance · analyst consensus", `
    <div class="next-report">
      <div class="when-block">
        <div class="big-date">${fmtDay(n.date)}</div>
        <div class="na">${days >= 0 ? `in ${days} day${days === 1 ? "" : "s"}` : ""} · ${TIMING_TEXT[n.timing] || ""}</div>
      </div>
      <div class="spec" style="flex:1">
        ${specRow({ key: "eps", label: "Expected EPS", badge: BADGE.est, value: fmtPerShare(n.eps_estimate, cur) + range(n.eps_low, n.eps_high, (v) => fmtPerShare(v, cur)), sectionName: "Earnings" })}
        ${specRow({ key: "revenue", label: "Expected revenue", badge: BADGE.est, value: fmtMoney(n.revenue_estimate, cur) + range(n.revenue_low, n.revenue_high, (v) => fmtMoney(v, cur)), sectionName: "Earnings" })}
      </div>
    </div>
    <p class="note">Dates published before the company confirms them can move. The range shows the lowest and highest analyst estimate.</p>`);
}

function vsEstimate(actual, estimate) {
  if (!isNum(actual) || !isNum(estimate)) return "";
  if (actual > estimate) return '<span class="up">above the estimate</span>';
  if (actual < estimate) return '<span class="down">below the estimate</span>';
  return "matched the estimate";
}

function earningsLabButton(e) {
  const l = e.latest;
  const beat = l && isNum(l.surprise_pct) ? (l.surprise_pct > 0 ? "an EPS beat" : l.surprise_pct < 0 ? "an EPS miss" : "any result") : "any result";
  const move = l?.reaction?.one_day;
  const reaction = !isNum(move) ? "moves any amount" : move >= 0 ? "rises" : "falls";
  const threshold = isNum(move) ? Math.floor(Math.abs(move) * 200) / 2 : 0;
  const preset = { conditions: [{ id: "c1", type: "earnings", params: { result: beat, reaction, threshold } }], outcome: { type: "forward_return", horizon: 10 } };
  const note = l ? `Latest report: ${beat === "any result" ? "EPS vs estimate n/a" : beat.replace("an ", "")}, stock ${isNum(move) ? stripTags(fmtPct(move, true, 1)) : "n/a"} the next session. This tests every past report like it.` : "";
  return `<button class="small-btn lab-jump" data-lab-preset='${escapeHtml(JSON.stringify(preset))}' data-lab-from="Earnings" data-lab-note="${escapeHtml(note)}"
    data-tip="Open the Lab: what happened after past reports like the latest one">${icon("lab", 13)}Test post-earnings behavior</button>`;
}

function latestReportHtml(e, cur) {
  const l = e.latest;
  if (!l) return "";
  const r = l.reaction;
  return section("02", "Latest report", `${fmtDay(l.date)} · ${TIMING_TEXT[l.timing] || ""} ${earningsLabButton(e)}`, `
    <div class="bigrow">
      <div class="big"><div class="value">${fmtPerShare(l.eps_actual, cur)}</div>
        <div class="label">EPS reported · est. ${fmtPerShare(l.eps_estimate, cur)}</div>
        <div class="big-sub">${vsEstimate(l.eps_actual, l.eps_estimate)}${isNum(l.surprise_pct) ? ` by ${fmtPct(Math.abs(l.surprise_pct) / 100, false, 1)}` : ""}</div></div>
      <div class="big"><div class="value">${l.revenue ? fmtMoney(l.revenue.value, cur) : NA}</div>
        <div class="label">Revenue${l.revenue ? ` · quarter ending ${fmtPeriod(l.revenue.quarter_end)}` : ""}</div>
        <div class="big-sub">${l.revenue && isNum(l.revenue.yoy) ? `${signed(fmtPct(l.revenue.yoy, true, 1), l.revenue.yoy)} vs a year earlier` : ""}</div></div>
      <div class="big"><div class="value ${signClass(r?.one_day)}">${r ? fmtPct(r.one_day, true, 1) : NA}</div>
        <div class="label">Next session${r ? ` · ${fmtDay(r.day0)}` : ""}</div>
        <div class="big-sub">${r && isNum(r.five_day) ? `${signed(fmtPct(r.five_day, true, 1), r.five_day)} over 5 sessions` : ""}</div></div>
    </div>
    ${isNum(l.eps_yoy) ? `<p class="aside" style="margin-top:16px">Reported EPS was ${fmtPct(Math.abs(l.eps_yoy), false, 0)} ${l.eps_yoy >= 0 ? "higher" : "lower"} than in the same quarter a year earlier.</p>` : ""}
    <p class="note">${escapeHtml(e.notes.reaction)}${r?.assumed_timing ? " The report time wasn't published, so MarketLab assumed it came after the close." : ""}
      ${escapeHtml(e.notes.revenue_estimates)}</p>`);
}

function historyHtml(e, cur) {
  if (!e.history.length) return section("03", "History", "", '<p class="slot-msg">No past reports available.</p>');
  const rows = e.history.map((h) => {
    const r = h.reaction;
    const rev = h.revenue;
    return `<tr>
      <td>${fmtDay(h.date)}<small>${escapeHtml({ before_open: "pre-market", after_close: "after close", during_market: "intraday", unknown: "time n/a" }[h.timing] || "")}</small></td>
      <td>${fmtPerShare(h.eps_estimate, cur)}</td>
      <td>${fmtPerShare(h.eps_actual, cur)}</td>
      <td class="${signClass(h.surprise_pct)}">${isNum(h.surprise_pct) ? fmtPct(h.surprise_pct / 100, true, 1) : NA}</td>
      <td>${rev ? fmtMoney(rev.value, cur) + (rev.derived ? '<sup data-tip="Q4 derived: full-year 10-K minus the three quarterly reports">d</sup>' : "") : NA}</td>
      <td>${rev ? signed(fmtPct(rev.yoy, true, 0), rev.yoy) : NA}</td>
      <td class="${signClass(r?.one_day)}">${r ? fmtPct(r.one_day, true, 1) : NA}</td>
      <td class="${signClass(r?.five_day)}">${r && isNum(r.five_day) ? fmtPct(r.five_day, true, 1) : NA}</td>
    </tr>`;
  }).join("");
  const reactions = e.history.filter((h) => h.reaction).slice().reverse();
  return section("03", "History", `last ${e.history.length} reports`, `
    <div class="table-scroll"><table class="data-table">
      <thead><tr><th>Reported</th><th>EPS est.</th><th>EPS</th><th>${term("eps_surprise", "Surprise")}</th><th>Revenue</th><th>Rev. YoY</th><th>Next day</th><th>5 days</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${reactions.length >= 3 ? `
      <div class="label" style="margin-top:22px">Move in the session after each report</div>
      ${barStrip(reactions.map((h) => ({ value: h.reaction.one_day, label: fmtPeriod(h.date), tip: `${fmtDay(h.date)}: ${fmtPct(h.reaction.one_day, true, 1)} next session` })))}` : ""}`);
}

function revenueBarsHtml(e, cur) {
  const q = e.revenue_quarters || [];
  if (q.length < 4) return "";
  return section("04", "Quarterly revenue", "SEC filings first, Yahoo fills gaps", `
    ${barStrip(q.map((p) => ({ value: p.value, label: fmtPeriod(p.date), tip: `Quarter ending ${fmtDay(p.date)}: ${stripTags(fmtMoney(p.value, cur))}${p.derived ? " (Q4 derived from the annual report)" : ""} · ${p.source === "sec" ? "SEC" : "Yahoo"}` })), { money: cur })}
    <p class="note">Fiscal quarters, oldest to newest. Hover a bar for the exact figure and source.</p>`);
}

// Simple vertical bars around a zero line (shared with the Risk tab)
function barStrip(items, { money = null, height = 120 } = {}) {
  const values = items.map((i) => i.value).filter(isNum);
  if (!values.length) return "";
  const max = Math.max(...values.map(Math.abs), 1e-9);
  const hasNeg = values.some((v) => v < 0);
  const zeroY = hasNeg ? height / 2 : height;
  const scale = (hasNeg ? height / 2 : height) - 4;
  const bars = items.map((it) => {
    const v = isNum(it.value) ? it.value : 0;
    const h = Math.max(1.5, (Math.abs(v) / max) * scale);
    const cls = money ? "neutral" : v >= 0 ? "up" : "down";
    return `<div class="bs-col" data-tip="${escapeHtml(it.tip || "")}"><div class="bs-plot" style="height:${height}px">
      <i class="${cls}" style="height:${h}px;${v >= 0 ? `bottom:${height - zeroY}px` : `top:${zeroY}px`}"></i></div>
      <span>${escapeHtml(it.label)}</span></div>`;
  }).join("");
  return `<div class="bar-strip" style="--n:${items.length}">${hasNeg ? `<div class="bs-zero" style="top:${zeroY}px"></div>` : ""}${bars}</div>`;
}
