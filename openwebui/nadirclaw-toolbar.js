/**
 * NadirClaw Floating Toolbar for Open WebUI
 *
 * Adds a collapsible side toolbar with quick-access buttons for:
 *   - Analytics Dashboard
 *   - Routing Dashboard
 *   - Export Chat as Markdown
 *   - Search History
 *
 * Communicates directly with NadirClaw API (localhost:8856) and
 * renders results in a slide-out panel.
 */
(function () {
  "use strict";

  const NADIRCLAW_URL = "http://localhost:8856";
  const PANEL_WIDTH = 480;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    setTimeout(init, 1500);
  }

  function init() {
    injectStyles();
    createToolbar();
    createPanel();
  }

  function injectStyles() {
    const css = `
      #nc-toolbar {
        position: fixed;
        right: 0;
        top: 50%;
        transform: translateY(-50%);
        z-index: 9998;
        display: flex;
        flex-direction: column;
        gap: 2px;
        transition: right 0.3s ease;
      }
      #nc-toolbar.nc-shifted { right: ${PANEL_WIDTH}px; }
      #nc-toolbar button {
        width: 40px; height: 40px; border: none;
        border-radius: 8px 0 0 8px; cursor: pointer;
        font-size: 18px; display: flex; align-items: center;
        justify-content: center; transition: all 0.15s ease;
        box-shadow: -2px 2px 8px rgba(0,0,0,0.2); position: relative;
      }
      html.dark #nc-toolbar button { background: #2a2a2a; color: #e0e0e0; }
      html:not(.dark) #nc-toolbar button { background: #fff; color: #333; border: 1px solid #ddd; border-right: none; }
      #nc-toolbar button:hover { width: 48px; filter: brightness(1.2); }
      #nc-toolbar button .nc-tip {
        display: none; position: absolute; right: 50px; white-space: nowrap;
        padding: 4px 10px; border-radius: 6px; font-size: 12px; pointer-events: none;
      }
      html.dark #nc-toolbar button .nc-tip { background: #444; color: #fff; }
      html:not(.dark) #nc-toolbar button .nc-tip { background: #333; color: #fff; }
      #nc-toolbar button:hover .nc-tip { display: block; }
      #nc-panel {
        position: fixed; right: -${PANEL_WIDTH + 10}px; top: 0;
        width: ${PANEL_WIDTH}px; height: 100vh; z-index: 9997;
        overflow-y: auto; transition: right 0.3s ease;
        box-shadow: -4px 0 20px rgba(0,0,0,0.15);
      }
      html.dark #nc-panel { background: #1a1a1a; color: #e0e0e0; }
      html:not(.dark) #nc-panel { background: #fff; color: #333; }
      #nc-panel.nc-open { right: 0; }
      #nc-ph {
        display: flex; align-items: center; justify-content: space-between;
        padding: 16px 20px; font-weight: 600; font-size: 15px;
        border-bottom: 1px solid rgba(128,128,128,0.2);
        position: sticky; top: 0; z-index: 1;
      }
      html.dark #nc-ph { background: #1a1a1a; }
      html:not(.dark) #nc-ph { background: #fff; }
      #nc-ph button {
        background: none; border: none; cursor: pointer;
        font-size: 20px; padding: 4px 8px; border-radius: 6px;
      }
      html.dark #nc-ph button { color: #aaa; }
      html.dark #nc-ph button:hover { background: #333; color: #fff; }
      html:not(.dark) #nc-ph button { color: #666; }
      html:not(.dark) #nc-ph button:hover { background: #eee; color: #000; }
      #nc-pb { padding: 20px; font-size: 14px; line-height: 1.6; }
      #nc-pb table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }
      #nc-pb th, #nc-pb td { padding: 6px 10px; text-align: left; }
      html.dark #nc-pb th { border-bottom: 2px solid #444; }
      html.dark #nc-pb td { border-bottom: 1px solid #333; }
      html:not(.dark) #nc-pb th { border-bottom: 2px solid #ddd; }
      html:not(.dark) #nc-pb td { border-bottom: 1px solid #eee; }
      #nc-pb h2 { font-size: 16px; margin: 20px 0 8px 0; }
      #nc-pb h3 { font-size: 14px; margin: 14px 0 6px 0; }
      .nc-stat {
        display: inline-block; padding: 8px 14px; border-radius: 8px;
        margin: 4px; font-size: 13px;
      }
      html.dark .nc-stat { background: #2a2a2a; }
      html:not(.dark) .nc-stat { background: #f5f5f5; }
      .nc-loading { text-align: center; padding: 40px; opacity: 0.6; }
      .nc-error { padding: 20px; border-radius: 8px; text-align: center; }
      html.dark .nc-error { background: #3a1a1a; color: #ff8888; }
      html:not(.dark) .nc-error { background: #fff0f0; color: #cc3333; }
      #nc-si {
        width: 100%; padding: 10px 14px; border-radius: 8px;
        border: 1px solid rgba(128,128,128,0.3); font-size: 14px;
        margin-bottom: 12px; box-sizing: border-box; outline: none;
      }
      html.dark #nc-si { background: #2a2a2a; color: #e0e0e0; }
      html:not(.dark) #nc-si { background: #f8f8f8; color: #333; }
      #nc-si:focus { border-color: #6366f1; }
      #nc-ea {
        width: 100%; min-height: 400px; padding: 12px; border-radius: 8px;
        border: 1px solid rgba(128,128,128,0.3);
        font-family: 'Fira Code','Consolas',monospace; font-size: 12px;
        resize: vertical; box-sizing: border-box;
      }
      html.dark #nc-ea { background: #1e1e1e; color: #d4d4d4; }
      html:not(.dark) #nc-ea { background: #fafafa; color: #333; }
      .nc-btn {
        padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer;
        font-size: 13px; font-weight: 500; margin: 4px; transition: filter 0.15s;
      }
      .nc-btn:hover { filter: brightness(1.15); }
      .nc-btn-p { background: #6366f1; color: #fff; }
      .nc-btn-s { background: rgba(128,128,128,0.2); }
      html.dark .nc-btn-s { color: #ccc; }
      html:not(.dark) .nc-btn-s { color: #555; }
    `;
    const s = document.createElement("style");
    s.textContent = css;
    document.head.appendChild(s);
  }

  function createToolbar() {
    const tb = document.createElement("div");
    tb.id = "nc-toolbar";
    [
      ["\u{1F4CA}", "Analytics", showAnalytics],
      ["\u{1F3AF}", "Dashboard", showDashboard],
      ["\u{1F50D}", "Search History", showSearch],
      ["\u{1F4CB}", "Export Chat", showExport],
    ].forEach(([icon, label, fn]) => {
      const b = document.createElement("button");
      b.innerHTML = icon + '<span class="nc-tip">' + label + "</span>";
      b.addEventListener("click", fn);
      tb.appendChild(b);
    });
    document.body.appendChild(tb);
  }

  let pTitle, pBody;

  function createPanel() {
    const p = document.createElement("div");
    p.id = "nc-panel";
    const h = document.createElement("div");
    h.id = "nc-ph";
    pTitle = document.createElement("span");
    pTitle.textContent = "NadirClaw";
    const cb = document.createElement("button");
    cb.textContent = "\u2715";
    cb.addEventListener("click", closePanel);
    h.appendChild(pTitle);
    h.appendChild(cb);
    pBody = document.createElement("div");
    pBody.id = "nc-pb";
    p.appendChild(h);
    p.appendChild(pBody);
    document.body.appendChild(p);
  }

  function openPanel(title) {
    pTitle.textContent = title;
    pBody.innerHTML = '<div class="nc-loading">Loading...</div>';
    document.getElementById("nc-panel").classList.add("nc-open");
    document.getElementById("nc-toolbar").classList.add("nc-shifted");
  }

  function closePanel() {
    document.getElementById("nc-panel").classList.remove("nc-open");
    document.getElementById("nc-toolbar").classList.remove("nc-shifted");
  }

  async function ncFetch(path) {
    const r = await fetch(NADIRCLAW_URL + path, {
      headers: { Authorization: "Bearer local", Accept: "application/json" },
    });
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    return r.json();
  }

  // --- Analytics ---
  async function showAnalytics() {
    openPanel("Analytics");
    try {
      const d = await ncFetch("/v1/analytics?since=30d");
      const a = d.analytics || {};
      const t = (a.totals || [])[0] || {};
      const bm = a.by_model || [];
      const bt = a.by_tier || [];
      const tr = t.total_requests || 0;
      let h = `<h2>Last 30 Days</h2><div>
        <span class="nc-stat"><strong>${tr.toLocaleString()}</strong> requests</span>
        <span class="nc-stat"><strong>${(t.total_tokens||0).toLocaleString()}</strong> tokens</span>
        <span class="nc-stat"><strong>$${(t.total_cost_usd||0).toFixed(4)}</strong> cost</span>
      </div><h3>By Model</h3><table>
        <tr><th>Model</th><th>Reqs</th><th>Tokens</th><th>Avg Latency</th></tr>
        ${bm.map(m=>`<tr><td>${sm(m.selected_model)}</td><td>${m.requests}</td><td>${(m.total_tokens||0).toLocaleString()}</td><td>${Math.round(m.avg_latency_ms||0)}ms</td></tr>`).join("")}
      </table><h3>By Tier</h3><table>
        <tr><th>Tier</th><th>Reqs</th><th>%</th></tr>
        ${bt.map(x=>`<tr><td>${x.tier||"\u2014"}</td><td>${x.requests}</td><td>${tr?((x.requests/tr)*100).toFixed(1):0}%</td></tr>`).join("")}
      </table>`;
      pBody.innerHTML = h;
    } catch (e) { pBody.innerHTML = `<div class="nc-error">Failed: ${e.message}</div>`; }
  }

  // --- Dashboard ---
  async function showDashboard() {
    openPanel("Routing Dashboard");
    try {
      const d = await ncFetch("/v1/dashboard?limit=20");
      const rr = d.recent_requests || [];
      let h = "<h2>Recent Requests</h2>";
      if (!rr.length) { h += "<p>No requests yet.</p>"; }
      else {
        h += `<table><tr><th>Model</th><th>Tier</th><th>Latency</th><th>Prompt</th></tr>
        ${rr.map(r=>`<tr><td>${sm(r.selected_model)}</td><td>${r.tier||"\u2014"}</td><td>${r.total_latency_ms||"\u2014"}ms</td><td title="${esc(r.prompt_text||"")}">${esc(tc(r.prompt_text||"",40))}</td></tr>`).join("")}
        </table>`;
      }
      pBody.innerHTML = h;
    } catch (e) { pBody.innerHTML = `<div class="nc-error">Failed: ${e.message}</div>`; }
  }

  // --- Search ---
  function showSearch() {
    openPanel("Search History");
    pBody.innerHTML = '<input id="nc-si" type="text" placeholder="Search conversation history..." autofocus /><div id="nc-sr"></div>';
    const inp = document.getElementById("nc-si");
    let db;
    inp.addEventListener("input", () => { clearTimeout(db); db = setTimeout(() => doSearch(inp.value), 400); });
    inp.focus();
  }

  async function doSearch(q) {
    const el = document.getElementById("nc-sr");
    if (!q || q.length < 2) { el.innerHTML = "<p style='opacity:0.5'>Type at least 2 characters...</p>"; return; }
    el.innerHTML = '<div class="nc-loading">Searching...</div>';
    try {
      const d = await ncFetch("/v1/search?q=" + encodeURIComponent(q) + "&limit=15");
      const items = d.results || [];
      if (!items.length) { el.innerHTML = "<p>No results.</p>"; return; }
      const dk = document.documentElement.classList.contains("dark");
      el.innerHTML = `<p><strong>${items.length}</strong> result(s)</p>` +
        items.map(r => `<div style="margin:12px 0;padding:10px;border-radius:8px;background:${dk?"#2a2a2a":"#f5f5f5"}">
          <div style="font-size:12px;opacity:0.6">${sm(r.selected_model||"")} &middot; ${r.tier||""} &middot; ${r.timestamp||""}</div>
          <div style="margin-top:6px"><strong>Prompt:</strong> ${esc(tc(r.prompt_text||"",120))}</div>
          ${r.response_text?`<div style="margin-top:4px"><strong>Response:</strong> ${esc(tc(r.response_text,120))}</div>`:""}
        </div>`).join("");
    } catch (e) { el.innerHTML = `<div class="nc-error">Search failed: ${e.message}</div>`; }
  }

  // --- Export ---
  function showExport() {
    openPanel("Export Chat");
    const msgs = grabMessages();
    if (!msgs.length) { pBody.innerHTML = '<div class="nc-error">No chat messages found. Open a conversation first.</div>'; return; }
    const now = new Date().toLocaleString();
    let md = `# Chat Export\n\n- **Date:** ${now}\n- **Messages:** ${msgs.length}\n\n---\n\n`;
    msgs.forEach((m, i) => {
      md += `## ${m.role === "user" ? "User" : "Assistant"}\n\n${m.content}\n\n`;
      if (i < msgs.length - 1) md += "---\n\n";
    });
    md += `---\n\n*Exported from Open WebUI on ${now}*\n`;

    pBody.innerHTML = `
      <p><strong>${msgs.length}</strong> messages</p>
      <div style="margin:12px 0">
        <button class="nc-btn nc-btn-p" id="nc-cp">Copy to Clipboard</button>
        <button class="nc-btn nc-btn-s" id="nc-dl">Download .md</button>
      </div>
      <textarea id="nc-ea">${esc(md)}</textarea>`;

    document.getElementById("nc-cp").addEventListener("click", () => {
      navigator.clipboard.writeText(document.getElementById("nc-ea").value).then(() => {
        const b = document.getElementById("nc-cp"); b.textContent = "Copied!";
        setTimeout(() => b.textContent = "Copy to Clipboard", 2000);
      });
    });
    document.getElementById("nc-dl").addEventListener("click", () => {
      const blob = new Blob([document.getElementById("nc-ea").value], {type:"text/markdown"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "chat-export-" + new Date().toISOString().slice(0,10) + ".md";
      a.click(); URL.revokeObjectURL(a.href);
    });
  }

  function grabMessages() {
    const msgs = [];
    // Strategy 1: OpenWebUI renders user messages in divs with specific structure
    // User messages have a "rounded-3xl" container, assistant messages have prose content
    const userDivs = document.querySelectorAll("div.w-full [data-message-id]");
    if (userDivs.length) {
      userDivs.forEach(el => {
        // Check for user indicator (avatar/name area)
        const isUser = !!el.querySelector(".flex-shrink-0 img[src*='user'], button[aria-label*='Edit']");
        const prose = el.querySelector(".prose, .whitespace-pre-wrap, .chat-assistant, .chat-user");
        const content = prose ? prose.innerText.trim() : el.innerText.trim();
        if (content && content.length < 50000) {
          msgs.push({ role: isUser ? "user" : "assistant", content });
        }
      });
      if (msgs.length) return msgs;
    }

    // Strategy 2: Walk the main chat area looking for alternating user/assistant blocks
    const chatContainer = document.querySelector("#messages-container, [class*='messages'], main .flex.flex-col");
    if (chatContainer) {
      const blocks = chatContainer.querySelectorAll(":scope > div");
      blocks.forEach(block => {
        const text = block.innerText.trim();
        if (!text || text.length > 50000) return;
        // User blocks typically contain an edit button or user avatar
        const hasEdit = !!block.querySelector("button[aria-label*='Edit'], button[aria-label*='edit']");
        const hasProse = !!block.querySelector(".prose");
        if (hasEdit && !hasProse) {
          msgs.push({ role: "user", content: text });
        } else if (hasProse) {
          msgs.push({ role: "assistant", content: text });
        }
      });
    }
    return msgs;
  }

  function sm(m) { return (m||"").replace("ollama/",""); }
  function tc(s,n) { return s.length>n ? s.slice(0,n)+"..." : s; }
  function esc(s) { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
})();
