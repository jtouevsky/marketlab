// =========================================================
// tab_news.js — the News tab: EVENTS, not a raw article feed.
// Data: /api/stock/<T>/news (tab_news.py): articles from many sources,
// grouped into events, categorized and ranked (reasons included).
// =========================================================

const NEWS_WINDOWS = [["today", "Today", 1], ["week", "This week", 7], ["month", "This month", 30]];
const SOURCE_TYPE_LABEL = { primary_regulatory: "SEC filing", primary_government: "Government", primary_company: "Company",
                            company: "Company source", reporting: "Reporting", aggregator: "Aggregator" };

function newsWindowEvents(n, windowId) {
  const days = NEWS_WINDOWS.find((w) => w[0] === windowId)[2];
  const now = Date.now();
  return n.events.filter((e) => now - new Date(e.published).getTime() <= days * 86400000);
}

function renderNews(win) {
  const n = win.data.news;
  if (!win.newsWindow) win.newsWindow = n.counts.today >= 3 ? "today" : "week";
  const inWindow = newsWindowEvents(n, win.newsWindow);
  const category = win.newsCategory && inWindow.some((e) => e.categories.includes(win.newsCategory)) ? win.newsCategory : null;
  const scoped = category ? inWindow.filter((e) => e.categories.includes(category)) : inWindow;
  const ranked = [...scoped].sort((a, b) => b.relevance - a.relevance || b.published.localeCompare(a.published));
  const cats = {};
  inWindow.forEach((e) => e.categories.forEach((c) => { cats[c] = (cats[c] || 0) + 1; }));
  const live = Object.values(n.providers).filter((p) => p.ok && p.count).length;

  const toolbar = `
    <div class="news-toolbar">
      <div class="news-toolbar-row">
        <div class="seg" role="group" aria-label="Time window">${NEWS_WINDOWS.map(([id, label]) => {
          const count = newsWindowEvents(n, id).length;
          return `<button class="${id === win.newsWindow ? "on" : ""}" data-news-window="${id}" aria-pressed="${id === win.newsWindow}">${label} <small>${count}</small></button>`;
        }).join("")}</div>
        <span class="news-tools">
          <button class="small-btn" data-news-sources data-tip="Which sources contributed">${icon("data", 13)}${live} sources</button>
          <button class="small-btn" data-news-refresh data-tip="Fetch again from every source">${icon("rerun", 13)}Refresh</button>
          <button class="small-btn lab-jump" data-lab-preset='${escapeHtml(JSON.stringify({ conditions: [{ id: "c1", type: "relative_volume", params: { op: "above", multiple: 2, lookback: 20 } }], outcome: { type: "forward_return", horizon: 10 } }))}'
            data-lab-from="News" data-lab-note="News-event conditions come later. Until then this tests days with at least 2× normal volume, which often coincide with news."
            data-tip="Open the Lab: what happens after unusually heavy trading days?">${icon("lab", 13)}Test high-attention days</button>
        </span>
      </div>
      <div class="chips" role="group" aria-label="Category">
        <button class="chip ${category ? "" : "on"}" data-news-cat="">All</button>
        ${Object.entries(cats).sort((a, b) => b[1] - a[1]).map(([c, k]) => `<button class="chip ${c === category ? "on" : ""}" data-news-cat="${escapeHtml(c)}">${escapeHtml(c)} <small>${k}</small></button>`).join("")}
      </div>
    </div>`;

  if (!ranked.length) {
    return toolbar + `<div class="empty-note"><p>No events ${win.newsWindow === "today" ? "in the last 24 hours" : "in this period"}${category ? ` tagged ${escapeHtml(category)}` : ""}.</p>
      ${win.newsWindow !== "month" ? '<button class="pill-btn" data-news-window="month">Show this month</button>' : ""}</div>` + newsFoot(n);
  }

  const top = ranked[0];
  const keyEvents = ranked.slice(1, win.newsWindow === "today" ? 6 : 9);
  const shownIds = new Set([top.id, ...keyEvents.map((e) => e.id)]);
  const policy = scoped.filter((e) => e.government);
  const rest = scoped.filter((e) => !shownIds.has(e.id)).sort((a, b) => b.published.localeCompare(a.published));
  const timeline = win.newsWindow !== "today" ? scoped.filter((e) => e.material).sort((a, b) => b.published.localeCompare(a.published)).slice(0, 14) : [];
  const limit = win.newsLimit || 25;

  return toolbar + `
    <section class="section">
      <header class="section-head"><span class="idx">01</span><h3>Top story</h3><span class="source">ranked by relevance and materiality, not recency</span></header>
      ${eventHtml(top, win, "top")}
    </section>
    ${keyEvents.length ? section("02", win.newsWindow === "today" ? "Also today" : "Key events", `${keyEvents.length} of ${scoped.length}`, `<div class="events-list">${keyEvents.map((e) => eventHtml(e, win, "key")).join("")}</div>`) : ""}
    ${policy.length ? section("03", "Government & policy", `${policy.length} event${policy.length === 1 ? "" : "s"}`, `
      <div class="policy-list">${policy.map(policyRow).join("")}</div>
      <p class="note">Shown because these events involve governments, regulators, courts or public policy. The tag describes what kind of development it is.
        <b>Proposal</b>, <b>Statement</b> and <b>Investigation</b> are not enacted policy. Tags come from official sources where available (Federal Register document types, agency press releases), otherwise from headline wording. Check the source before relying on a tag.</p>`) : ""}
    ${timeline.length >= 3 ? section(policy.length ? "04" : "03", "Timeline", "material events only", `
      <ol class="timeline">${timeline.map((e) => `<li><time>${new Date(e.published).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</time>
        <span class="cat-dot" data-cat="${escapeHtml(e.category)}"></span><a href="${escapeHtml(e.sources[0].url)}" target="_blank" rel="noopener">${escapeHtml(e.headline)}</a>
        <span class="na">${escapeHtml(e.category)}${e.source_count > 1 ? ` · ${e.source_count} sources` : ""}</span></li>`).join("")}</ol>`) : ""}
    ${rest.length ? section("", "All coverage", `${rest.length} more event${rest.length === 1 ? "" : "s"}, newest first`, `
      <div class="events-list compact">${rest.slice(0, limit).map((e) => eventHtml(e, win, "compact")).join("")}</div>
      ${rest.length > limit ? `<button class="pill-btn" data-news-more style="margin-top:12px">Show ${Math.min(25, rest.length - limit)} more</button>` : ""}`) : ""}
    ${newsFoot(n)}`;
}

function newsTime(iso) {
  const hours = (Date.now() - new Date(iso).getTime()) / 3600000;
  return hours < 24 ? fmtRelative(iso) : new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function eventHtml(e, win, size) {
  const lead = e.sources[0];
  const others = e.sources.slice(1);
  const open = (win.newsOpen || new Set()).has(e.id);
  const primary = e.primary && e.primary.url !== lead.url ? e.primary : null;
  const typeBadge = lead.type !== "reporting" && lead.type !== "aggregator" ? `<span class="src-type ${lead.type}">${SOURCE_TYPE_LABEL[lead.type]}</span>` : "";
  return `
    <article class="story ${size}">
      <a href="${escapeHtml(lead.url)}" target="_blank" rel="noopener" class="story-title">${escapeHtml(e.headline)}</a>
      <div class="story-meta">${typeBadge}<b>${escapeHtml(lead.name)}</b><span>${escapeHtml(newsTime(e.published))}</span>
        <span class="cat">${escapeHtml(e.category)}</span>
        ${lead.paywall_possible ? '<span class="na" data-tip="This publisher often requires a subscription">may be paywalled</span>' : ""}
        <span class="why" data-tip="${escapeHtml(`Relevance ${e.relevance}: ${e.reasons.join("; ")}`)}">why here?</span></div>
      ${size !== "compact" && e.summary ? `<p class="story-snippet">${escapeHtml(e.summary.length > 280 ? `${e.summary.slice(0, 277)}…` : e.summary)}</p>` : ""}
      ${primary ? `<div class="primary-line">${icon("filings", 13)} Primary source: <a href="${escapeHtml(primary.url)}" target="_blank" rel="noopener">${escapeHtml(primary.name)} ↗</a></div>` : ""}
      ${others.length ? `<button class="link-btn small" data-news-coverage="${e.id}" aria-expanded="${open}">${open ? "Hide coverage" : `View coverage · ${e.source_count} source${e.source_count === 1 ? "" : "s"}`}</button>
        ${open ? `<ul class="coverage">${e.sources.map((s) => `<li><span class="src-type ${s.type}">${SOURCE_TYPE_LABEL[s.type] || ""}</span>
          <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)}</a><span class="na">${escapeHtml(newsTime(s.published))}</span>
          <span class="cov-title">${escapeHtml(s.title)}</span></li>`).join("")}</ul>` : ""}` : ""}
      ${size === "top" ? `<div class="story-actions"><a class="small-btn" href="${escapeHtml(lead.url)}" target="_blank" rel="noopener">Read article ↗</a>
        <span class="na">MarketLab shows headlines and short snippets only; it hasn't read the full article.</span></div>` : ""}
    </article>`;
}

function policyRow(e) {
  const kind = e.government.kind;
  const cls = /Proposal|Statement|Investigation|Report/.test(kind) ? "proposal" : "action";
  const lead = e.sources[0];
  return `
    <div class="policy-row">
      <span class="kind ${cls}">${escapeHtml(kind)}</span>
      <div><a href="${escapeHtml(lead.url)}" target="_blank" rel="noopener">${escapeHtml(e.headline)}</a>
        <div class="story-meta">${lead.type === "primary_government" ? '<span class="src-type primary_government">Government</span>' : ""}<b>${escapeHtml(lead.name)}</b>
          <span>${new Date(e.published).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</span>
          ${e.source_count > 1 ? `<span>${e.source_count} sources</span>` : ""}</div></div>
    </div>`;
}

function newsFoot(n) {
  const st = n.stats;
  return `<p class="data-foot">${escapeHtml(n.method)} ${st.raw} items collected → ${st.articles} about ${escapeHtml(n.ticker)} in the last 30 days → ${st.events} events from ${st.publishers} publishers.
    Articles are links to the original publishers; MarketLab doesn't host or summarise their text.</p>`;
}

function newsSourcesPopover(win, anchor) {
  const providers = Object.values(win.data.news.providers);
  const order = (p) => (p.ok && p.count ? 0 : p.ok ? 1 : p.configured ? 2 : 3);
  popover.innerHTML = `<h4>News sources</h4><p class="full">Every source is queried in parallel; one failing never blocks the others.</p>
    ${providers.sort((a, b) => order(a) - order(b)).map((p) => `<div class="src-row"><span class="dot ${p.ok ? (p.count ? "ok" : "") : p.configured ? "err" : ""}"></span>
      <b>${escapeHtml(p.label)}</b><span>${p.ok ? `${p.count} items` : escapeHtml(p.error || "unavailable")}</span><small class="na">${escapeHtml(p.kind)}</small></div>`).join("")}
    <p class="note">Optional sources switch on when you add a free API key to .env (see env.example).</p>`;
  popover.hidden = false;
  tooltip.hidden = true;
  placeNear(popover, anchor.getBoundingClientRect());
}

function newsClick(win, event) {
  const windowBtn = event.target.closest("[data-news-window]");
  const cat = event.target.closest("[data-news-cat]");
  const cov = event.target.closest("[data-news-coverage]");
  if (event.target.closest("[data-news-sources]")) { newsSourcesPopover(win, event.target.closest("[data-news-sources]")); return true; }
  if (event.target.closest("[data-news-refresh]")) {
    win.status.news = undefined;
    PARTS.news.endpoint = () => "/news?refresh=1";
    showTab(win, "news").finally(() => { PARTS.news.endpoint = () => "/news"; });
    return true;
  }
  if (!windowBtn && !cat && !cov && !event.target.closest("[data-news-more]")) return false;
  if (windowBtn) { win.newsWindow = windowBtn.dataset.newsWindow; win.newsLimit = 25; }
  if (cat) win.newsCategory = cat.dataset.newsCat || null;
  if (cov) {
    win.newsOpen = win.newsOpen || new Set();
    if (win.newsOpen.has(cov.dataset.newsCoverage)) win.newsOpen.delete(cov.dataset.newsCoverage); else win.newsOpen.add(cov.dataset.newsCoverage);
  }
  if (event.target.closest("[data-news-more]")) win.newsLimit = (win.newsLimit || 25) + 25;
  const body = win.el.querySelector(".window-body");
  const scroll = body.scrollTop;
  body.innerHTML = renderNews(win);
  renumberSections(body);
  body.scrollTop = scroll;
  return true;
}
