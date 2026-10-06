// =========================================================
// format.js — small helpers that turn raw numbers into readable text.
// Every formatter returns "N/A" (greyed out) when the value is missing,
// so no screen ever shows "undefined", "NaN" or a made-up number.
// =========================================================

const NA = '<span class="na">N/A</span>';

function isNum(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// Safety: outside text (company names, descriptions) must never be inserted
// into the page as raw HTML. This turns < > & " ' into harmless text.
function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// "$" for US dollars; other currencies get their code at the end ("1.2B TWD").
function moneyParts(currency) {
  const usd = !currency || currency === "USD";
  return { prefix: usd ? "$" : "", suffix: usd ? "" : ` ${currency}` };
}

// Big amounts: 416200000000 -> "$416.2B"
function fmtMoney(value, currency) {
  if (!isNum(value)) return NA;
  const { prefix, suffix } = moneyParts(currency);
  const sign = value < 0 ? "−" : "";
  const abs = Math.abs(value);
  for (const [limit, unit] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
    if (abs >= limit) {
      const scaled = abs / limit;
      return `${sign}${prefix}${scaled.toFixed(scaled >= 100 ? 1 : 2)}${unit}${suffix}`;
    }
  }
  return `${sign}${prefix}${abs.toFixed(0)}${suffix}`;
}

// Per-share amounts: 7.46 -> "$7.46"
function fmtPerShare(value, currency) {
  if (!isNum(value)) return NA;
  const { prefix, suffix } = moneyParts(currency);
  return `${value < 0 ? "−" : ""}${prefix}${Math.abs(value).toFixed(2)}${suffix}`;
}

function fmtPrice(value) {
  if (!isNum(value)) return NA;
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Takes a FRACTION: 0.064 -> "6.4%" (or "+6.4%" when signed)
function fmtPct(fraction, signed = false, decimals = 1) {
  if (!isNum(fraction)) return NA;
  const pct = fraction * 100;
  const sign = signed && pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `${sign}${Math.abs(pct).toFixed(decimals)}%`;
}

// Change in a margin, in percentage points: 0.0069 -> "+0.7 pts"
function fmtPts(fraction) {
  if (!isNum(fraction)) return NA;
  const pts = fraction * 100;
  return `${pts > 0 ? "+" : pts < 0 ? "−" : ""}${Math.abs(pts).toFixed(1)} pts`;
}

// Ratios / multiples: 38.6 -> "38.6×"
function fmtMultiple(value, decimals = 1) {
  return isNum(value) ? `${value.toFixed(decimals)}×` : NA;
}

function fmtNumber(value, decimals = 2) {
  return isNum(value) ? value.toLocaleString("en-US", { maximumFractionDigits: decimals }) : NA;
}

// Volumes / headcounts: 54300000 -> "54.3M"
function fmtCompact(value) {
  if (!isNum(value)) return NA;
  return value.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

// "2025-09-27" -> "Sep 2025" (period end dates from financial statements)
function fmtPeriod(dateText) {
  if (!dateText) return "N/A";
  const date = new Date(`${dateText}T00:00:00Z`);
  return date.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

// ISO timestamp -> "Oct 1, 4:00 PM" in the user's local time
function fmtDateTime(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// "up" / "down" CSS class for green/red, or "" for zero/missing
function signClass(value) {
  if (!isNum(value) || value === 0) return "";
  return value > 0 ? "up" : "down";
}

// Tiny trend line drawn as SVG. `points` = [{value}, ...] oldest -> newest.
// It shows SHAPE only (no axis); exact numbers are in the expandable table.
function sparkline(points, width = 88, height = 22) {
  const values = points.map((p) => p.value).filter(isNum);
  if (values.length < 2) return '<span class="na">—</span>';

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 3;
  const x = (i) => pad + (i * (width - 2 * pad)) / (values.length - 1);
  const y = (v) => height - pad - ((v - min) / range) * (height - 2 * pad);

  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  // Dashed zero line when the series crosses from losses to profits (or back)
  const zero = min < 0 && max > 0 ? `<line x1="0" x2="${width}" y1="${y(0)}" y2="${y(0)}"/>` : "";
  const last = values.length - 1;

  return `<svg class="spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">
    ${zero}<path d="${path}"/><circle cx="${x(last)}" cy="${y(values[last])}" r="2"/></svg>`;
}

// Plain-text versions (no HTML) for sentences like "Holds $24.1B more in cash..."
function fmtMoneyText(value, currency) {
  return isNum(value) ? fmtMoney(value, currency) : "N/A";
}
function fmtPerShareText(value, currency) {
  return isNum(value) ? fmtPerShare(value, currency) : "N/A";
}

// "2 min ago", "3 h ago", "Aug 6" — for freshness labels
function fmtRelative(iso) {
  if (!iso) return null;
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} d ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// "2026-08-06" -> "Aug 6, 2026" (filing dates)
function fmtDay(dateText) {
  if (!dateText) return "N/A";
  return new Date(`${dateText}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}


// ---------- Appearance: chart / canvas colours follow the active theme ----------
function isDarkTheme() { return document.documentElement.dataset.theme === "dark"; }
const CHART_LIGHT = { text: "#6b7381", grid1: "rgba(22,34,58,0.04)", grid2: "rgba(22,34,58,0.06)", cross1: "rgba(22,34,58,0.3)", cross2: "rgba(22,34,58,0.22)",
  label: "#1a2230", ink: "#1a2230", up: "#16a06a", down: "#e0493d", upFill: "rgba(22,160,106,0.14)", downFill: "rgba(224,73,61,0.12)",
  upVol: "rgba(22,160,106,0.28)", downVol: "rgba(224,73,61,0.26)", blue: "#3b6fe0", violet: "#8a5ae6", amber: "#e08a1e", teal: "#12a3a0",
  gray: "#8a929e", suspect: "#b8bec8", level: "rgba(59,111,224,.7)", upZone: "rgba(22,160,106,.8)", downZone: "rgba(224,73,61,.8)" };
const CHART_DARK = { text: "#9aa2ad", grid1: "rgba(255,255,255,0.04)", grid2: "rgba(255,255,255,0.05)", cross1: "rgba(255,255,255,0.3)", cross2: "rgba(255,255,255,0.22)",
  label: "#3e4651", ink: "#eef1f6", up: "#5fd39c", down: "#ff8a7f", upFill: "rgba(95,211,156,0.16)", downFill: "rgba(255,138,127,0.14)",
  upVol: "rgba(95,211,156,0.3)", downVol: "rgba(255,138,127,0.28)", blue: "#8fb8ff", violet: "#c3a6ff", amber: "#ffc380", teal: "#7fe0d0",
  gray: "#9aa2ad", suspect: "#5c626b", level: "rgba(143,184,255,.75)", upZone: "rgba(95,211,156,.8)", downZone: "rgba(255,138,127,.8)" };
function CC() { return isDarkTheme() ? CHART_DARK : CHART_LIGHT; }
