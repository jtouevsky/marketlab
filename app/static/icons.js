// =========================================================
// icons.js — MarketLab's own icon family.
//
// One drawing language: 24×24 grid, 1.6 stroke, round caps and joins,
// no fills except small "data dots". Built to read at 16–24 px.
//
//   icon("lab")            -> <svg …> string
//   icon("lab", 18, "x")   -> 18px, extra class "x"
// =========================================================

const ICON_PATHS = {
  // The MarketLab mark: a lens (circle) with a rising, stepped reading inside it
  mark: '<circle cx="12" cy="12" r="9"/><path d="M7 15l3-3 2.5 2L17 9"/><circle cx="17" cy="9" r="1.3" class="dot"/>',
  research: '<rect x="3.5" y="4.5" width="17" height="15" rx="4"/><path d="M3.5 9h17"/><path d="M8 13.5h5M8 16h3"/>',
  // Lab: a flask whose liquid line is a small chart
  lab: '<path d="M9.5 3.5h5M10.5 3.5v5.2L5.6 17.2A2.2 2.2 0 0 0 7.5 20.5h9a2.2 2.2 0 0 0 1.9-3.3L13.5 8.7V3.5"/><path d="M7.6 15.2l2.4-1.6 2 1.2 4.3-2.4"/>',
  notebook: '<rect x="5" y="3.5" width="14" height="17" rx="3"/><path d="M9 3.5v17"/><path d="M12 8.5h4M12 12h4"/>',
  replay: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4v3.2h3.2"/><path d="M10.5 9.2v5.6l4.2-2.8z"/>',
  portfolio: '<rect x="3.5" y="7.5" width="17" height="12" rx="3"/><path d="M9 7.5V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v1.5"/><path d="M3.5 12.5h17"/>',
  invest: '<circle cx="9" cy="10" r="5"/><circle cx="15" cy="10" r="5"/><circle cx="12" cy="15" r="5"/>',
  ask: '<path d="M12 3.5l1.7 4.8 4.8 1.7-4.8 1.7L12 16.5l-1.7-4.8L5.5 10l4.8-1.7z"/><path d="M18 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
  overview: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v4.5l3 2"/>',
  profile: '<rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="12" cy="10" r="2.6"/><path d="M7.8 17c.8-1.8 2.4-2.8 4.2-2.8s3.4 1 4.2 2.8"/>',
  earnings: '<rect x="4" y="5" width="16" height="15" rx="3.5"/><path d="M4 9.5h16M8.5 3.5v3M15.5 3.5v3"/><path d="M9 16l2-2.2 1.6 1.2 2.4-2.8"/>',
  chart: '<path d="M4 19.5h16"/><path d="M5 15.5l4-5 3.5 3L19 6"/><circle cx="19" cy="6" r="1.2" class="dot"/>',
  news: '<rect x="4" y="4.5" width="13" height="15" rx="3"/><path d="M17 8.5h1.5a1.5 1.5 0 0 1 1.5 1.5v7a2.5 2.5 0 0 1-2.5 2.5"/><path d="M7.5 8.5h6M7.5 12h6M7.5 15.5h4"/>',
  risk: '<path d="M12 3.5l7.5 3v5.3c0 4.3-3.2 7.6-7.5 8.7-4.3-1.1-7.5-4.4-7.5-8.7V6.5z"/><path d="M12 8.5v4.5"/><circle cx="12" cy="16" r=".9" class="dot"/>',
  filings: '<path d="M7 3.5h7l4 4v11.5a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5z"/><path d="M14 3.5v4h4"/><path d="M8.5 12.5h7M8.5 16h5"/>',
  external: '<path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19h11a1.5 1.5 0 0 0 1.5-1.5V14"/><path d="M13.5 4.5h6v6M19.5 4.5l-8 8"/>',
  plus: '<path d="M12 5.5v13M5.5 12h13"/>',
  close: '<path d="M7 7l10 10M17 7L7 17"/>',
  check: '<path d="M5.5 12.5l4 4 9-9.5"/>',
  warn: '<path d="M12 4.5l8.5 14.5h-17z"/><path d="M12 10v4"/><circle cx="12" cy="16.6" r=".8" class="dot"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><circle cx="12" cy="8" r=".9" class="dot"/>',
  save: '<path d="M6.5 4h9l3.5 3.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 18.5v-13A1.5 1.5 0 0 1 6.5 4z"/><path d="M8.5 4v4.5h6V4M8.5 20v-5.5h7V20"/>',
  fork: '<circle cx="7" cy="5.5" r="2"/><circle cx="17" cy="5.5" r="2"/><circle cx="12" cy="18.5" r="2"/><path d="M7 7.5v1.5a3 3 0 0 0 3 3h4a3 3 0 0 0 3-3V7.5M12 12v4.5"/>',
  rerun: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4v3.5H16"/>',
  duplicate: '<rect x="8" y="8" width="12" height="12" rx="3"/><path d="M16 8V6.5A2.5 2.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A2.5 2.5 0 0 0 6.5 16H8"/>',
  trash: '<path d="M4.5 7h15M9.5 7V5a1.5 1.5 0 0 1 1.5-1.5h2A1.5 1.5 0 0 1 14.5 5v2M6.5 7l.8 11.6a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9L17.5 7"/>',
  edit: '<path d="M14.5 5.5l4 4L9 19H5v-4z"/><path d="M12.5 7.5l4 4"/>',
  min: '<path d="M6 12h12"/>',
  "chevron-up": '<path d="M7 14.5l5-5 5 5"/>',
  "chevron-down": '<path d="M7 9.5l5 5 5-5"/>',
  challenge: '<path d="M12 3.5l7.5 3v5.3c0 4.3-3.2 7.6-7.5 8.7-4.3-1.1-7.5-4.4-7.5-8.7V6.5z"/><path d="M8.8 12.2l2.2 2.2 4.4-4.6"/>',
  method: '<rect x="4.5" y="3.5" width="15" height="17" rx="3"/><path d="M8.5 8h7M8.5 12h7M8.5 16h4"/>',
  max: '<path d="M7 17L17 7M10 7h7v7"/>',
  data: '<ellipse cx="12" cy="6.5" rx="7" ry="2.8"/><path d="M5 6.5v11c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-11"/><path d="M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8"/>',
};

function icon(name, size = 18, extra = "") {
  const paths = ICON_PATHS[name];
  if (!paths) return "";
  return `<svg class="ico ${extra}" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">${paths}</svg>`;
}

// Company logo with an initials fallback. The image comes from MarketLab's
// /api/logo route; if there's no logo, the image removes itself and the
// initials underneath show.
function logoHtml(ticker, name, size = 28) {
  const initials = monogram(ticker, name);
  return `<span class="logo" style="--s:${size}px" aria-hidden="true"><span class="mono-ini">${escapeHtml(initials)}</span>` +
    `<img src="/api/logo/${encodeURIComponent(ticker)}" alt="" loading="lazy" onload="this.parentNode.classList.add('has-img')" onerror="this.remove()"></span>`;
}

function monogram(ticker, name) {
  const clean = String(name || "").replace(/\b(inc|corp|corporation|co|ltd|plc|holdings|group|company|the|class [a-c])\b\.?/gi, "").trim();
  const words = clean.split(/[\s,.-]+/).filter((w) => /^[A-Za-z]/.test(w));
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  if (words.length === 1) return words[0][0].toUpperCase();
  return String(ticker || "?").slice(0, 2).toUpperCase();
}
