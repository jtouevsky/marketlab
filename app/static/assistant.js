// =========================================================
// assistant.js — the "Ask MarketLab" panel.
//
// One conversation per ticker, kept in this browser tab:
//   chats["IONQ"] = { info: {...}, messages: [{role, content}, ...] }
// The whole conversation is sent with each question; the server keeps no
// chat state. The server builds the company context (assistant.py), so
// the AI always knows which company you mean.
//
// "Explain this" anywhere in the app:
//   openAssistant(ticker, { question, focus: {label, value, section} })
// Any element with class "explain-btn" and data-explain-* attributes does
// this automatically (see app.js), so new features can add one with plain HTML.
// =========================================================

const askPanel   = document.getElementById("ask");
const askTicker  = document.getElementById("ask-ticker");
const askContext = document.getElementById("ask-context");
const askBody    = document.getElementById("ask-body");
const askForm    = document.getElementById("ask-form");
const askInput   = document.getElementById("ask-input");
const askSend    = document.getElementById("ask-send");

const chats = {};
let activeTicker = null;
let busy = false;

document.getElementById("ask-close").addEventListener("click", closeAssistant);


// ---------- Opening / closing ----------
async function openAssistant(ticker, { question = null, focus = null } = {}) {
  activeTicker = ticker;
  chats[ticker] = chats[ticker] || { info: null, messages: [] };
  askTicker.textContent = ticker;
  askPanel.classList.remove("closed");
  renderChat();

  if (!chats[ticker].info) {
    askBody.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
    try {
      chats[ticker].info = await fetchJson(`/api/stock/${encodeURIComponent(ticker)}/assistant`);
    } catch (error) {
      askBody.innerHTML = `<div class="ask-setup">${escapeHtml(error.message)}</div>`;
      return;
    }
    if (activeTicker !== ticker) return;
    renderChat();
  }
  if (question) ask(question, focus);
  else askInput.focus();
}

function closeAssistant() {
  askPanel.classList.add("closed");
}


// ---------- Control lines in the answer stream ----------
// The server may put `[[status]]text` (e.g. "Searching current sources…") and `[[web]]{json}` (the web sources
// used) on their own lines before the answer. They are not part of the answer text.
function parseStream(raw) {
  let status = "", web = null, text = raw;
  text = text.replace(/\[\[status\]\]([^\n]*)\n/g, (_, t) => { status = t.trim(); return ""; });
  text = text.replace(/\[\[web\]\](\{[^\n]*\})\n/, (_, j) => { try { web = JSON.parse(j); } catch (e) { /* ignore */ } return ""; });
  return { text, status, web };
}

function webSourcesHtml(web) {
  if (!web) return "";
  const BUCKET = { today: "Today", week: "This week", older: "Older background", undated: "Date unconfirmed" };
  const rows = (web.sources || []).map((s) => {
    const when = s.published ? (s.precision === "time" ? fmtDateTime(s.published) : fmtDay(s.published.slice(0, 10))) : "date unknown";
    return `<li><span class="cite-n">${escapeHtml(s.id.slice(1))}</span>
      <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.headline)}</a>
      <span class="web-meta">${escapeHtml(s.publisher)} · ${escapeHtml(when)} · <span class="web-bucket b-${escapeHtml(s.bucket)}">${BUCKET[s.bucket] || ""}</span> · ${escapeHtml(s.quality)}</span></li>`;
  }).join("");
  const problems = (web.problems || []).map((p) => `<div class="web-problem">${escapeHtml(p)}</div>`).join("");
  if (!rows && !problems) return "";
  const queries = (web.queries || []).length ? `<div class="web-meta">Searched: ${web.queries.map((q) => escapeHtml(q)).join(" · ")}</div>` : "";
  return `<details class="web-sources"><summary>${web.used ? `Sources used: ${web.used}` : "Web search: nothing usable found"}</summary>
    ${rows ? `<ol>${rows}</ol>` : ""}${problems}${queries}</details>`;
}

function aiMessageHtml(message, infoSources) {
  const parsed = message.web !== undefined ? { text: message.content, web: message.web } : parseStream(message.content);
  const sources = (infoSources || []).concat((parsed.web && parsed.web.sources) || []);
  return renderAnswer(parsed.text, sources) + webSourcesHtml(parsed.web);
}


// ---------- Rendering ----------
function renderChat() {
  const chat = chats[activeTicker];
  const info = chat.info;
  askInput.placeholder = `Ask anything about ${activeTicker}…`;

  // What the assistant can and can't see (honesty about context)
  if (info) {
    const yes = Object.entries(info.available).filter(([, ok]) => ok).map(([name]) => name);
    const no = [...Object.entries(info.available).filter(([, ok]) => !ok).map(([name]) => name), "article text", "filing text"];
    askContext.innerHTML =
      yes.map((name) => `<span class="ctx-chip yes">${escapeHtml(name)}</span>`).join("") +
      no.map((name) => `<span class="ctx-chip no" data-tip="Not retrieved by MarketLab yet. The assistant will say so instead of guessing.">${escapeHtml(name)}</span>`).join("");
  } else {
    askContext.innerHTML = "";
  }

  askSend.disabled = busy || !(info && info.ai_enabled);
  askInput.disabled = !(info && info.ai_enabled);

  if (!info) return;
  let html = "";

  if (!info.ai_enabled) {
    html += connectClaudeHtml(info.ai, activeTicker);
  } else if (!chat.messages.length) {
    html += `<div class="ask-intro">Questions here are about <b>${escapeHtml(info.name)}</b> (${escapeHtml(activeTicker)}).
      Answers are built from MarketLab's data and cite their sources, like <span class="cite">1</span>. For current events (“why is it down today?”) it also searches the web.
      ${info.ai && info.ai.label ? `<span class="ask-provider">Answered by Claude · ${escapeHtml(info.ai.label)}</span>` : ""}</div>`;
  }

  if (!chat.messages.length) {
    html += `<div class="suggestions">${info.suggestions.map((q) =>
      `<button class="suggestion" ${info.ai_enabled ? "" : "disabled"} data-question="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join("")}</div>`;
  }

  for (const message of chat.messages) {
    html += message.role === "user"
      ? `<div class="msg user">${escapeHtml(message.content)}</div>`
      : `<div class="msg ai">${aiMessageHtml(message, info.sources)}</div>`;
  }
  askBody.innerHTML = html;
  askBody.scrollTop = info.ai_enabled || chat.messages.length ? askBody.scrollHeight : 0;
}

// ---------- Connect Claude ----------
// MarketLab never handles Claude credentials. You sign in with Claude Code's
// own login in Terminal; the server only checks whether Claude Code is signed in.
function connectClaudeHtml(ai, ticker) {
  ai = ai || {};
  const copy = (cmd) => `<span class="cmd"><code>${escapeHtml(cmd)}</code><button type="button" class="link-btn" data-copy="${escapeHtml(cmd)}">Copy</button></span>`;
  let steps;
  if (ai.setup === "api_key") {
    steps = `<p>MarketLab is set to use an Anthropic API key only (<code>AI_PROVIDER=anthropic_api</code>).</p>
      <ol><li>Add <code>ANTHROPIC_API_KEY=…</code> to the <code>.env</code> file (usage is billed per question).</li>
      <li>Restart MarketLab.</li></ol>`;
  } else {
    const installed = ai.claude_code && ai.claude_code.installed;
    steps = `<p>Ask MarketLab can use your existing Claude subscription through the official Claude Code program on this Mac. MarketLab never sees your Claude password or tokens.</p>
      <ol>
        ${installed ? "" : `<li>Install Claude Code: <a href="https://code.claude.com/docs/en/setup" target="_blank" rel="noopener">official setup guide ↗</a></li>`}
        <li>In Terminal, run ${copy("claude auth login")} and sign in to your Claude account on Claude's page.</li>
        <li>Come back here and click <b>Check connection</b>.</li>
      </ol>
      ${ai.claude_code && ai.claude_code.error ? `<p class="ask-warn">${escapeHtml(ai.claude_code.error)}</p>` : ""}
      <p class="ask-fine">For personal use. Questions count toward your Claude plan's usage limits. Claude Code runs with its tools switched off, so it only answers questions.</p>`;
  }
  return `<div class="ask-setup ask-connect">
      <b>Connect Claude</b>
      ${steps}
      <button type="button" class="pill-btn" data-check-ai>Check connection</button>
      <span class="ask-check-msg" data-check-msg aria-live="polite"></span>
      <p class="ask-fine">Once connected, these are the kinds of questions you can ask about ${escapeHtml(ticker)}:</p>
    </div>`;
}

async function checkAiConnection(button) {
  const msg = askBody.querySelector("[data-check-msg]");
  button.disabled = true;
  if (msg) msg.textContent = "Checking…";
  try {
    const ai = await fetchJson("/api/ai/status?refresh=1");
    for (const chat of Object.values(chats)) {
      if (chat.info) { chat.info.ai = ai; chat.info.ai_enabled = ai.enabled; }
    }
    if (typeof refreshStatusPills === "function") refreshStatusPills();
    if (ai.enabled) { renderChat(); askInput.focus(); return; }
    if (msg) msg.textContent = ai.setup === "install" ? "Claude Code wasn't found on this Mac yet."
                              : ai.setup === "login" ? "Claude Code is installed but not signed in yet."
                              : "Still not connected.";
  } catch (error) {
    if (msg) msg.textContent = error.message;
  }
  button.disabled = false;
}

askBody.addEventListener("click", (event) => {
  const check = event.target.closest("[data-check-ai]");
  if (check) { checkAiConnection(check); return; }
  const copyBtn = event.target.closest("[data-copy]");
  if (copyBtn) {
    navigator.clipboard?.writeText(copyBtn.dataset.copy).then(() => { copyBtn.textContent = "Copied"; setTimeout(() => { copyBtn.textContent = "Copy"; }, 1400); }).catch(() => {});
    return;
  }
  const suggestion = event.target.closest(".suggestion");
  if (suggestion && !suggestion.disabled) ask(suggestion.dataset.question);
});


// ---------- Asking (with a streamed answer) ----------
askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = askInput.value.trim();
  if (question) ask(question);
});

askInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {   // Enter sends, Shift+Enter = new line
    event.preventDefault();
    askForm.requestSubmit();
  }
});
askInput.addEventListener("input", () => {        // grow the box as you type
  askInput.style.height = "auto";
  askInput.style.height = `${Math.min(askInput.scrollHeight, 120)}px`;
});

async function ask(question, focus = null) {
  const ticker = activeTicker;
  const chat = chats[ticker];
  if (busy || !chat.info || !chat.info.ai_enabled) return;

  busy = true;
  askInput.value = "";
  askInput.style.height = "auto";
  chat.messages.push({ role: "user", content: question });
  renderChat();

  // Placeholder bubble that fills in as the answer streams
  const bubble = document.createElement("div");
  bubble.className = "msg ai";
  bubble.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
  askBody.appendChild(bubble);
  askBody.scrollTop = askBody.scrollHeight;

  let answer = "";
  try {
    const response = await fetch(`/api/stock/${encodeURIComponent(ticker)}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Failed exchanges stay on screen but are never sent back to the AI
      body: JSON.stringify({
        messages: chat.messages.filter((m) => !m.failed).map(({ role, content }) => ({ role, content })),
        focus,
      }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Request failed (${response.status}).`);
    }
    // Read the answer piece by piece as the server sends it
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      answer += decoder.decode(value, { stream: true });
      if (activeTicker === ticker) {
        const live = parseStream(answer);
        const searching = live.status ? `<div class="ask-searching" aria-live="polite"><span class="typing"><span></span><span></span><span></span></span> ${escapeHtml(live.status)}</div>` : "";
        bubble.innerHTML = searching + (live.text.trim() ? renderAnswer(live.text, (chat.info.sources || []).concat((live.web && live.web.sources) || [])) : "") + webSourcesHtml(live.web);
        if (!live.status && !live.text.trim() && !live.web) bubble.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
        askBody.scrollTop = askBody.scrollHeight;
      }
    }
  } catch (error) {
    answer += `\n\n[[error]] ${error.message}`;
  }

  const failed = answer.includes("[[error]]");
  if (failed) chat.messages[chat.messages.length - 1].failed = true;   // the question
  const finalParsed = parseStream(answer);
  chat.messages.push({ role: "assistant", content: finalParsed.text, web: finalParsed.web, failed });
  busy = false;
  if (activeTicker === ticker) renderChat();
}


// ---------- A tiny, safe Markdown renderer ----------
// Escapes everything first, then turns a few Markdown patterns into HTML:
// ### headings, **bold**, *italic*, - bullets, 1. lists, [text](url), [S1] citations.
function renderAnswer(text, sources) {
  const [main, errorPart] = text.split("[[error]]");
  const sourceById = Object.fromEntries((sources || []).map((s) => [s.id, s]));

  const inline = (line) => escapeHtml(line)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\[(S\d+(?:\s*,\s*S\d+)*)\]/g, (match, ids) => ids.split(/\s*,\s*/).map((id) => {
      const source = sourceById[id];
      if (!source) return "";
      const tip = escapeHtml(`${source.label}${source.retrieved_at ? ` · retrieved ${fmtDateTime(source.retrieved_at)}` : ""}`);
      const number = id.slice(1);
      return source.url
        ? `<a class="cite" href="${escapeHtml(source.url)}" target="_blank" rel="noopener" data-tip="${tip}">${number}</a>`
        : `<span class="cite" data-tip="${tip}">${number}</span>`;
    }).join(""));

  let html = "";
  let list = null;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };

  for (const raw of main.split("\n")) {
    const line = raw.trim();
    if (!line) { closeList(); continue; }
    let match;
    if ((match = line.match(/^#{1,4}\s+(.*)/))) {
      closeList(); html += `<h4>${inline(match[1])}</h4>`;
    } else if ((match = line.match(/^[-*•]\s+(.*)/))) {
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(match[1])}</li>`;
    } else if ((match = line.match(/^\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(match[1])}</li>`;
    } else {
      closeList(); html += `<p>${inline(line)}</p>`;
    }
  }
  closeList();
  if (errorPart !== undefined) html += `<div class="error-note">${escapeHtml(errorPart.trim())}</div>`;
  return html || '<div class="typing"><span></span><span></span><span></span></div>';
}
