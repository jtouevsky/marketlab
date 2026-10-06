// =========================================================
// charts.js — price charts (Overview quick chart + the Chart tab).
//
// Drawing: TradingView Lightweight Charts v4 (open source, Apache-2.0,
// bundled in static/vendor/). Data: /api/stock/<T>/chart?range=…
// which returns bars + moving averages computed by tab_chart.py.
//
// Times: daily bars are "YYYY-MM-DD"; intraday bars are seconds in the
// EXCHANGE's local time, so the axis shows market hours (9:30–16:00 ET).
// =========================================================

const CHART_RANGES = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"];
const QUICK_RANGES = [["1D", "1D"], ["1W", "5D"], ["1M", "1M"], ["3M", "3M"], ["YTD", "YTD"], ["1Y", "1Y"], ["5Y", "5Y"]];
const COMPARE_COLORS = [CC().blue, CC().violet, CC().amber];
const chartCache = new Map();          // "NVDA|1Y" -> { at, data }

function loadChart(ticker, range) {
  const key = `${ticker}|${range}`;
  const hit = chartCache.get(key);
  const maxAge = range === "1D" || range === "5D" ? 120_000 : 600_000;
  if (hit && Date.now() - hit.at < maxAge) return hit.promise;
  const promise = fetchJson(`${apiUrl(ticker, "/chart")}?range=${range}`);
  chartCache.set(key, { at: Date.now(), promise });
  promise.catch(() => chartCache.delete(key));
  return promise;
}

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function baseChartOptions(intraday) {
  return {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: CC().text, fontFamily: "Geist Mono, ui-monospace, monospace", fontSize: 10 },
    grid: { vertLines: { color: CC().grid1 }, horzLines: { color: CC().grid2 } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderVisible: false, timeVisible: intraday, secondsVisible: false, rightOffset: 2, fixLeftEdge: true, fixRightEdge: true },
    crosshair: {
      mode: 0,   // free crosshair (not magnet)
      vertLine: { color: CC().cross1, width: 1, style: 3, labelBackgroundColor: CC().label },
      horzLine: { color: CC().cross2, width: 1, style: 3, labelBackgroundColor: CC().label },
    },
    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    localization: { locale: "en-US", priceFormatter: (p) => p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
  };
}

function destroyCharts(win) {
  for (const chart of win.charts || []) { try { chart.remove(); } catch (e) { /* already gone */ } }
  win.charts = [];
}

function timeLabel(time, intraday) {
  if (typeof time === "number") {
    return new Date(time * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC" });
  }
  return fmtDay(typeof time === "string" ? time : `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`);
}
const timeKey = (t) => (typeof t === "object" ? `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}` : t);


// =========================================================
// Overview: quick chart
// =========================================================
function quickChartHtml(win) {
  const range = win.quickRange || "1Y";
  return `
    <div class="quick-chart">
      <div class="chart-toolbar">
        <div class="seg" role="tablist" aria-label="Chart range">
          ${QUICK_RANGES.map(([label, value]) => `<button class="${value === range ? "on" : ""}" data-quick="${value}">${label}</button>`).join("")}
        </div>
        <span class="chart-change" data-quick-change></span>
        <button class="link-btn" data-goto="chart">Full chart ${icon("external", 13)}</button>
      </div>
      <div class="chart-box quick" data-quick-box><div class="chart-legend" data-quick-legend></div></div>
    </div>`;
}

async function drawQuickChart(win) {
  const box = win.el.querySelector("[data-quick-box]");
  if (!box) return;
  const range = win.quickRange || "1Y";
  box.classList.add("loading");
  let data;
  try {
    data = await loadChart(win.ticker, range);
  } catch (error) {
    box.classList.remove("loading");
    box.insertAdjacentHTML("beforeend", `<div class="chart-msg">${escapeHtml(error.message)}</div>`);
    return;
  }
  if (!box.isConnected || (win.quickRange || "1Y") !== range) return;
  box.classList.remove("loading");
  destroyCharts(win);
  const intraday = typeof data.time[0] === "number";
  const chart = LightweightCharts.createChart(box, { ...baseChartOptions(intraday), handleScale: false, handleScroll: false });
  win.charts.push(chart);
  const up = isNum(data.summary.change) && data.summary.change >= 0;
  const color = up ? CC().up : CC().down;
  const series = chart.addAreaSeries({
    lineColor: color, lineWidth: 2, topColor: up ? CC().upFill : CC().downFill, bottomColor: "rgba(0,0,0,0)",
    priceLineVisible: false, lastValueVisible: true, crosshairMarkerRadius: 4,
  });
  const points = data.time.map((t, i) => ({ time: t, value: data.close[i] })).filter((p) => isNum(p.value));
  series.setData(points);
  chart.timeScale().fitContent();

  const change = win.el.querySelector("[data-quick-change]");
  if (change) change.innerHTML = `<span class="${signClass(data.summary.change)}">${fmtPct(data.summary.change, true, 2)}</span> <small>${range === "1D" ? "today" : `over ${QUICK_RANGES.find((r) => r[1] === range)?.[0] || range}`}</small>`;
  const legend = win.el.querySelector("[data-quick-legend]");
  const first = points[0]?.value;
  chart.subscribeCrosshairMove((param) => {
    if (!param.time || !param.seriesData.get(series)) { legend.innerHTML = ""; return; }
    const v = param.seriesData.get(series).value;
    legend.innerHTML = `<b>${fmtPrice(v)}</b> <span class="${signClass(v - first)}">${fmtPct(v / first - 1, true, 2)}</span> <span>${timeLabel(param.time)}</span>`;
  });
}


// =========================================================
// Chart tab
// =========================================================
function chartState(win) {
  if (!win.chartState) {
    win.chartState = { range: "1Y", type: "candles", sma20: false, sma50: true, sma200: true, volume: true, vwap: true, markers: true, compare: [] };
  }
  return win.chartState;
}

function renderChartTab(win) {
  const st = chartState(win);
  const toggle = (key, label, tip) => `<button class="tog ${st[key] ? "on" : ""}" data-chart-toggle="${key}" data-tip="${escapeHtml(tip)}">${label}</button>`;
  const intraday = st.range === "1D" || st.range === "5D";
  const comparing = st.compare.length > 0;
  return `
    <div class="chart-tab">
      <div class="chart-toolbar wrap">
        <div class="seg" aria-label="Range">${CHART_RANGES.map((r) => `<button class="${r === st.range ? "on" : ""}" data-chart-range="${r}">${r}</button>`).join("")}</div>
        <button class="small-btn lab-jump" data-lab-chart data-tip="Open the Lab with a test of what followed moves like the last 5 days">${icon("lab", 13)}Test this move</button>
        <div class="seg" aria-label="Chart type">
          <button class="${st.type === "candles" && !comparing ? "on" : ""}" data-chart-type="candles" ${comparing ? 'disabled data-tip="Comparison uses lines (percent change)"' : ""}>Candles</button>
          <button class="${st.type === "line" || comparing ? "on" : ""}" data-chart-type="line">Line</button>
        </div>
      </div>
      <div class="chart-toolbar wrap sub">
        <span class="label">Overlays</span>
        ${intraday ? "" : toggle("sma50", "SMA 50", "50-day simple moving average")}
        ${intraday ? "" : toggle("sma200", "SMA 200", "200-day simple moving average")}
        ${toggle("sma20", "SMA 20", intraday ? "20-bar simple moving average" : "20-day simple moving average")}
        ${toggle("volume", "Volume", "Shares traded per bar")}
        ${intraday ? toggle("vwap", "VWAP", "Volume-weighted average price, resets each session") : ""}
        ${intraday ? "" : toggle("markers", "Earnings", "Mark each earnings report; click a marker to inspect it")}
        <span class="label" style="margin-left:10px">Compare</span>
        ${["SPY", "QQQ"].map((s) => `<button class="tog ${st.compare.includes(s) ? "on" : ""}" data-chart-compare="${s}">${s}</button>`).join("")}
        ${st.compare.filter((s) => !["SPY", "QQQ"].includes(s)).map((s) => `<button class="tog on" data-chart-compare="${escapeHtml(s)}">${escapeHtml(s)} ${icon("close", 11)}</button>`).join("")}
        <form class="compare-add" data-compare-form><input placeholder="+ ticker" maxlength="10" spellcheck="false" aria-label="Compare with ticker"></form>
      </div>
      <div class="chart-box main" data-chart-box><div class="chart-legend" data-chart-legend></div></div>
      <div data-chart-foot></div>
      <div data-earnings-inspect></div>
    </div>`;
}

async function drawMainChart(win) {
  const box = win.el.querySelector("[data-chart-box]");
  if (!box) return;
  const st = chartState(win);
  const range = st.range;
  const token = (win.chartToken || 0) + 1;
  win.chartToken = token;
  box.classList.add("loading");

  let data, compares = [], markers = [];
  try {
    const wantsMarkers = st.markers && !["1D", "5D"].includes(range);
    [data, compares, markers] = await Promise.all([
      loadChart(win.ticker, range),
      Promise.all(st.compare.map((s) => loadChart(s, range).then((d) => ({ symbol: s, data: d })).catch((e) => ({ symbol: s, error: e.message })))),
      wantsMarkers ? loadEarningsForMarkers(win) : Promise.resolve([]),
    ]);
  } catch (error) {
    if (win.chartToken !== token) return;
    box.classList.remove("loading");
    box.querySelectorAll(".chart-msg").forEach((m) => m.remove());
    box.insertAdjacentHTML("beforeend", `<div class="chart-msg">${escapeHtml(error.message)}</div>`);
    return;
  }
  if (win.chartToken !== token || !box.isConnected) return;
  box.classList.remove("loading");
  box.querySelectorAll(".chart-msg").forEach((m) => m.remove());
  destroyCharts(win);

  const intraday = typeof data.time[0] === "number";
  const chart = LightweightCharts.createChart(box, baseChartOptions(intraday));
  win.charts.push(chart);
  const comparing = compares.some((c) => c.data);
  const legend = win.el.querySelector("[data-chart-legend]");
  const bars = data.time.map((t, i) => ({ time: t, open: data.open[i], high: data.high[i], low: data.low[i], close: data.close[i], volume: data.volume[i] }))
    .filter((b) => [b.open, b.high, b.low, b.close].every(isNum));
  const byTime = new Map(bars.map((b, i) => [timeKey(b.time), { ...b, prev: bars[i - 1]?.close }]));

  let main;
  const lines = {};
  if (comparing) {
    // Normalised: % change from the first bar of the range, so different prices share one axis.
    const base = bars[0].close;
    main = chart.addLineSeries({ color: CC().ink, lineWidth: 2, priceLineVisible: false, priceFormat: { type: "custom", formatter: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` } });
    main.setData(bars.map((b) => ({ time: b.time, value: (b.close / base - 1) * 100 })));
    compares.filter((c) => c.data).forEach((c, k) => {
      const times = new Set(bars.map((b) => timeKey(b.time)));
      const pts = c.data.time.map((t, i) => ({ time: t, value: c.data.close[i] })).filter((p) => isNum(p.value) && times.has(timeKey(p.time)));
      if (!pts.length) return;
      const first = pts[0].value;
      lines[c.symbol] = chart.addLineSeries({ color: COMPARE_COLORS[k % 3], lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
        priceFormat: { type: "custom", formatter: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` } });
      lines[c.symbol].setData(pts.map((p) => ({ time: p.time, value: (p.value / first - 1) * 100 })));
    });
  } else if (st.type === "candles") {
    main = chart.addCandlestickSeries({ upColor: CC().up, downColor: CC().down, borderVisible: false, wickUpColor: CC().up, wickDownColor: CC().down, priceLineVisible: false });
    main.setData(bars);
  } else {
    main = chart.addLineSeries({ color: CC().ink, lineWidth: 2, priceLineVisible: false });
    main.setData(bars.map((b) => ({ time: b.time, value: b.close })));
  }

  if (!comparing) {
    const overlayColors = { sma20: CC().blue, sma50: CC().amber, sma200: CC().violet, vwap: CC().teal };
    for (const key of ["sma20", "sma50", "sma200", "vwap"]) {
      const values = data.overlays[key];
      if (!st[key] || !values || (key === "vwap" && !intraday) || ((key === "sma50" || key === "sma200") && intraday)) continue;
      const pts = data.time.map((t, i) => ({ time: t, value: values[i] })).filter((p) => isNum(p.value));
      if (!pts.length) continue;
      lines[key] = chart.addLineSeries({ color: overlayColors[key], lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      lines[key].setData(pts);
    }
  }
  let volume = null;
  if (st.volume) {
    volume = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(bars.map((b) => ({ time: b.time, value: b.volume || 0, color: b.close >= b.open ? CC().upVol : CC().downVol })));
  }

  // Earnings markers: placed on the first trading session that reflected each report
  const markerByTime = new Map();
  if (markers.length) {
    const times = bars.map((b) => timeKey(b.time));
    for (const m of markers) {
      const day = m.reaction?.day0 || m.date;
      const i = times.findIndex((t) => t >= day);
      if (i < 0 || (i === 0 && day < times[0])) continue;
      markerByTime.set(times[i], m);
    }
  }
  // A focused event from another tab ("View on chart"): one marker on the first session on/after its date
  const allMarkers = [...markerByTime.entries()].map(([t, m]) => ({
    time: t, position: "belowBar", shape: "circle", size: 1.2,
    color: !isNum(m.surprise_pct) ? CC().gray : m.surprise_pct >= 0 ? CC().up : CC().down, text: "E",
  }));
  if (win.chartFocus && !intraday) {
    const times = bars.map((b) => timeKey(b.time));
    const i = times.findIndex((t) => t >= win.chartFocus.date);
    if (i >= 0) allMarkers.push({ time: times[i], position: "aboveBar", shape: "arrowDown", color: CC().amber, text: win.chartFocus.label.slice(0, 40) });
  }
  if (allMarkers.length) main.setMarkers(allMarkers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)));
  chart.timeScale().fitContent();

  // Legend: OHLCV + overlays at the crosshair (or the latest bar)
  const describe = (key) => {
    const b = byTime.get(key);
    if (!b) return "";
    const change = isNum(b.prev) ? b.close / b.prev - 1 : null;
    let html = `<span class="when">${timeLabel(b.time, intraday)}</span>`;
    html += `<span>O <b>${fmtPrice(b.open)}</b></span><span>H <b>${fmtPrice(b.high)}</b></span><span>L <b>${fmtPrice(b.low)}</b></span><span>C <b>${fmtPrice(b.close)}</b></span>`;
    if (isNum(change)) html += `<span class="${signClass(change)}">${fmtPct(change, true, 2)}</span>`;
    html += `<span>Vol <b>${fmtCompact(b.volume)}</b></span>`;
    const m = markerByTime.get(key);
    if (m) html += `<span class="earn-tag">Earnings ${isNum(m.eps_actual) ? `EPS ${m.eps_actual.toFixed(2)}` : ""}${isNum(m.eps_estimate) ? ` vs ${m.eps_estimate.toFixed(2)} est.` : ""} · click</span>`;
    return html;
  };
  const latestKey = timeKey(bars[bars.length - 1].time);
  const overlayLegend = Object.keys(lines).map((k) => `<span class="ov ov-${k.toLowerCase()}">${k.toUpperCase().replace("SMA", "SMA ")}</span>`).join("");
  legend.innerHTML = describe(latestKey) + overlayLegend;
  chart.subscribeCrosshairMove((param) => {
    legend.innerHTML = (param.time ? describe(timeKey(param.time)) : describe(latestKey)) + overlayLegend;
  });
  chart.subscribeClick((param) => {
    if (!param.time) return;
    const m = markerByTime.get(timeKey(param.time));
    if (m) inspectEarnings(win, m);
  });

  const foot = win.el.querySelector("[data-chart-foot]");
  const failed = compares.filter((c) => c.error);
  foot.innerHTML = `
    <div class="chart-summary">
      <span><b class="${signClass(data.summary.change)}">${fmtPct(data.summary.change, true, 2)}</b> over ${escapeHtml(range)}</span>
      <span>High ${fmtPrice(data.summary.high)}</span><span>Low ${fmtPrice(data.summary.low)}</span>
      ${comparing ? `<span>${compares.filter((c) => c.data).map((c, k) => `<i class="sw" style="background:${COMPARE_COLORS[k % 3]}"></i>${escapeHtml(c.symbol)}`).join(" ")} · % change from the first bar</span>` : ""}
    </div>
    ${failed.length ? `<p class="note">Couldn't load ${failed.map((c) => escapeHtml(c.symbol)).join(", ")}.</p>` : ""}
    <p class="data-foot">Source: Yahoo Finance. ${intraday ? `${data.interval} bars, not adjusted; times are exchange time.` : data.interval === "1wk" ? "Weekly bars aggregated from adjusted daily data; moving averages are in weeks." : "Daily bars adjusted for splits and dividends; moving averages computed on the full history, so they're correct from the first day shown."}
      ${markerByTime.size ? " E = earnings report (green: EPS above estimate, red: below), placed on the first session after the announcement." : ""}</p>`;
}

async function loadEarningsForMarkers(win) {
  if (win.status.earnings === "ok") return win.data.earnings.history;
  try {
    await loadPart(win, "earnings");
    return win.data.earnings?.history || [];
  } catch (e) { return []; }
}

function inspectEarnings(win, m) {
  const holder = win.el.querySelector("[data-earnings-inspect]");
  if (!holder) return;
  const cur = win.data.earnings?.currency;
  const r = m.reaction;
  holder.innerHTML = `
    <div class="inspect glass-flat">
      <div class="inspect-head"><span class="label">Earnings report</span><b>${fmtDay(m.date)}</b>
        <span class="na">${{ before_open: "before the open", after_close: "after the close", during_market: "during market hours", unknown: "time not reported" }[m.timing] || ""}</span>
        <button class="icon-btn" data-close-inspect aria-label="Close">${icon("close", 14)}</button></div>
      <div class="inspect-grid">
        <div><span class="label">EPS reported</span><div class="v">${fmtPerShare(m.eps_actual, cur)}</div></div>
        <div><span class="label">EPS estimate</span><div class="v">${fmtPerShare(m.eps_estimate, cur)}</div></div>
        <div><span class="label">${term("eps_surprise", "Surprise")}</span><div class="v ${signClass(m.surprise_pct)}">${isNum(m.surprise_pct) ? fmtPct(m.surprise_pct / 100, true, 1) : NA}</div></div>
        <div><span class="label">Revenue</span><div class="v">${m.revenue ? fmtMoney(m.revenue.value, cur) : NA}</div></div>
        <div><span class="label">Next session</span><div class="v ${signClass(r?.one_day)}">${r ? fmtPct(r.one_day, true, 1) : NA}</div></div>
        <div><span class="label">5 sessions</span><div class="v ${signClass(r?.five_day)}">${r && isNum(r.five_day) ? fmtPct(r.five_day, true, 1) : NA}</div></div>
      </div>
      <p class="note">Price moves around a report show what happened, not why. <button class="link-btn" data-goto="earnings">All reports ${icon("external", 12)}</button></p>
    </div>`;
  holder.querySelector("[data-close-inspect]").addEventListener("click", () => { holder.innerHTML = ""; });
}

// Clicks inside the chart tab / quick chart (delegated from the window body)
function chartClick(win, event) {
  const st = chartState(win);
  const quick = event.target.closest("[data-quick]");
  if (quick) { win.quickRange = quick.dataset.quick; quick.parentNode.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === quick)); drawQuickChart(win); return true; }
  const range = event.target.closest("[data-chart-range]");
  const type = event.target.closest("[data-chart-type]");
  const tog = event.target.closest("[data-chart-toggle]");
  const cmp = event.target.closest("[data-chart-compare]");
  if (range) st.range = range.dataset.chartRange;
  else if (type && !type.disabled) st.type = type.dataset.chartType;
  else if (tog) st[tog.dataset.chartToggle] = !st[tog.dataset.chartToggle];
  else if (cmp) {
    const s = cmp.dataset.chartCompare;
    st.compare = st.compare.includes(s) ? st.compare.filter((x) => x !== s) : [...st.compare, s].slice(-3);
  } else return false;
  rerenderChartTab(win);
  return true;
}

function rerenderChartTab(win) {
  const tab = win.el.querySelector(".chart-tab");
  if (!tab) return;
  tab.outerHTML = renderChartTab(win);
  bindCompareForm(win);
  drawMainChart(win);
}

function bindCompareForm(win) {
  const form = win.el.querySelector("[data-compare-form]");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = form.querySelector("input").value.trim().toUpperCase();
    if (!value || !/^[A-Z0-9.\-^=]{1,10}$/.test(value)) return;
    const st = chartState(win);
    if (!st.compare.includes(value)) st.compare = [...st.compare, value].slice(-3);
    rerenderChartTab(win);
  });
}

function afterChartTab(win) {
  bindCompareForm(win);
  drawMainChart(win);
}
