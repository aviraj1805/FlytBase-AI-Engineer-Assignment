// App shell: five sections with sub-views (hash router), status badge, frame drawer, alert toasts, keyboard shortcuts.
import { api, charts, esc, failure, fixed, flink, hhmm, loading, meter, sev, zoneName } from "./lib.js";
import { replay, subscribe, loadTimeline, positionOfFrame, seek, toggle } from "./replay.js";
import { startDemo } from "./demo.js";

const icon = (paths) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
const ICONS = {
  live: icon('<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/>'),
  search: icon('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>'),
  ask: icon('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/>'),
  insights: icon('<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>'),
  proof: icon('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>'),
  sun: icon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  moon: icon('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
};

// [section, label, key, views]; a view is [id ("" = the section's default), label, module]. Modules load on first visit.
const SECTIONS = [
  ["live", "Live", "1", [["", "Mission", "./screens/mission.js"], ["alerts", "Alerts", "./screens/alerts.js"]]],
  ["search", "Search", "2", [["", "Frame index", "./screens/explorer.js"], ["events", "Event log", "./screens/events.js"]]],
  ["ask", "Ask AI", "3", [["", "Analyst agent", "./screens/chat.js"]]],
  ["insights", "Insights", "4", [["", "Briefing", "./screens/summaries.js"], ["identities", "Identities", "./screens/entities.js"], ["memory", "Memory", "./screens/context.js"]]],
  ["proof", "Proof", "5", [["", "Results", "./screens/evaluation.js"], ["tests", "Tests", "./screens/qa.js"], ["architecture", "Architecture", "./screens/architecture.js"]]],
];
// Links from the earlier one-page-per-screen layout (docs, bookmarks) land on the matching view.
const LEGACY = { mission: "live", alerts: "live/alerts", events: "search/events", explorer: "search", chat: "ask",
  entities: "insights/identities", summaries: "insights", context: "insights/memory", qa: "proof/tests", evaluation: "proof", architecture: "proof/architecture" };

const view = document.getElementById("view");
const nav = document.getElementById("nav");
const subtabs = document.getElementById("subtabs");
const helpBtn = document.getElementById("help-btn");
const helpPop = document.getElementById("help-pop");
const toasts = document.getElementById("toasts");
let cleanup = null;
let renderToken = 0;
let active = ["live", ""]; // [section, view] being shown
let unseen = 0;            // alerts raised by a playing replay while the Alerts view was closed
export const app = { status: null };

nav.innerHTML = SECTIONS.map(([id, label, key]) => `<a href="#/${id}" data-sec="${id}" title="${label} (${key})">${ICONS[id]}<span>${label}</span>${
  id === "live" ? '<i class="live-dot" id="live-dot" hidden></i><span class="count" id="alert-count" hidden></span>' : ""}</a>`).join("");

async function route() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [sec = "", sub = ""] = path.split("/");
  if (LEGACY[sec]) { location.replace(`#/${LEGACY[sec]}${query ? `?${query}` : ""}`); return; }
  const section = SECTIONS.find((s) => s[0] === sec) || SECTIONS[0];
  const [viewId, , module] = section[3].find((v) => v[0] === sub) || section[3][0];
  active = [section[0], viewId];
  nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.sec === section[0]));
  subtabs.innerHTML = section[3].map(([id, label]) =>
    `<a href="#/${section[0]}${id ? `/${id}` : ""}" class="${id === viewId ? "active" : ""}">${esc(label)}</a>`).join("");
  if (onAlertsView()) unseen = 0;
  showBadges();
  closeHelp();
  if (cleanup) { try { cleanup(); } catch { /* view already gone */ } cleanup = null; }
  toasts.innerHTML = "";
  while (charts.length) charts.pop().destroy();
  const token = ++renderToken;
  const page = document.createElement("div");
  page.className = "page";
  page.innerHTML = loading();
  view.replaceChildren(page);
  view.scrollTop = 0;
  try {
    const mod = await import(module);
    if (token !== renderToken) return; // navigated again meanwhile
    helpPop.innerHTML = mod.HELP;
    page.innerHTML = "";
    cleanup = (await mod.render(page, { params: new URLSearchParams(query) })) || null;
  } catch (err) {
    if (token === renderToken) page.innerHTML = failure(err, "This view failed to load");
    console.error(err);
  }
  view.focus({ preventScroll: true });
}

const onAlertsView = () => active[0] === "live" && active[1] === "alerts";

function showBadges() {
  const count = document.getElementById("alert-count");
  count.hidden = !unseen;
  count.textContent = unseen > 9 ? "9+" : String(unseen);
  document.getElementById("live-dot").hidden = !replay.playing;
  const mini = document.getElementById("replay-mini");
  mini.hidden = !replay.current || (active[0] === "live" && !active[1]);
  mini.className = `replay-mini ${replay.playing ? "playing" : ""}`;
  mini.textContent = replay.current ? `${replay.playing ? "Replay" : "Paused"} ${hhmm(replay.current.frame.ts)}` : "";
}

// ------------------------------------------------------------------ help popover
function closeHelp() {
  helpPop.hidden = true;
  helpBtn.classList.remove("on");
  helpBtn.setAttribute("aria-expanded", "false");
}
helpBtn.onclick = (e) => {
  e.stopPropagation();
  const open = helpPop.hidden;
  closeHelp();
  if (open) { helpPop.hidden = false; helpBtn.classList.add("on"); helpBtn.setAttribute("aria-expanded", "true"); }
};
document.addEventListener("click", (e) => { if (!helpPop.hidden && !e.target.closest("#help-pop")) closeHelp(); });

// ------------------------------------------------------------------ status badge
async function loadStatus() {
  const badge = document.getElementById("mode-badge");
  try {
    const s = await api("/api/status");
    app.status = s;
    const prop = document.getElementById("property-name");
    prop.textContent = `${s.property} · drone ${s.drone_id}`;
    prop.title = `Footage ${s.index.first_ts.slice(0, 10)} · ${s.index.frames} frames · ${s.run.alerts} alerts`;
    const g = s.gemini;
    badge.className = `badge ${g.mode}`;
    badge.textContent = g.mode === "gemini" ? `Gemini · ${g.model}` : "Offline mode";
    badge.title = g.mode === "gemini" ? "The agent and the briefing use Gemini; alerts stay deterministic." : `Deterministic offline path (${g.reason}). Set GEMINI_API_KEY to enable Gemini.`;
  } catch (err) {
    badge.className = "badge offline";
    badge.textContent = "API unreachable";
    badge.title = String(err.message || err);
  }
}

// ------------------------------------------------------------------ frame drawer
const drawer = document.getElementById("drawer");
const drawerBody = document.getElementById("drawer-body");

async function openFrame(frameId) {
  drawer.hidden = false;
  document.getElementById("drawer-title").textContent = `Frame f${frameId}`;
  drawerBody.innerHTML = loading();
  try {
    const d = await api(`/api/frames/${frameId}`);
    const f = d.frame;
    const tele = f.battery_pct == null ? '<p class="muted">Telemetry packet lost for this frame.</p>' : `
      <dl class="kv">
        <dt>Position</dt><dd>${fixed(f.lat, 5)}, ${fixed(f.lon, 5)}</dd>
        <dt>Altitude</dt><dd>${fixed(f.altitude_m)} m</dd>
        <dt>Battery</dt><dd>${fixed(f.battery_pct, 0)} %</dd>
        <dt>Wind / GPS HDOP</dt><dd>${fixed(f.wind_mps)} m/s · ${fixed(f.gps_hdop, 2)}</dd>
        <dt>Mode</dt><dd>${esc(f.mode)} · ${fixed(f.distance_from_home_m, 0)} m from dock</dd>
      </dl>`;
    drawerBody.innerHTML = `
      <div class="row small muted"><span class="mono">${esc(f.ts)}</span><span class="chip">${esc(zoneName(f.zone))}</span><span class="chip">${esc(f.source)}</span></div>
      <p class="drawer-caption">“${esc(f.description)}”</p>
      <div class="row">
        ${d.prev ? `<button class="btn btn-sm" data-frame="${d.prev}">&larr; f${d.prev}</button>` : ""}
        ${d.next ? `<button class="btn btn-sm" data-frame="${d.next}">f${d.next} &rarr;</button>` : ""}
        <span class="spacer"></span><button class="btn btn-sm btn-accent" id="drawer-replay">&#9654; Show in replay</button>
      </div>
      <h3>Detections (${d.detections.length})</h3>
      ${d.detections.map((x) => `<div class="det">${x.category ? `<span class="cat cat-${esc(x.category)}" style="margin-top:5px"></span>` : ""}<div style="flex:1"><b>${esc(x.display_name)}</b>${x.action ? ` <span class="muted">${esc(x.action)}</span>` : ""}
        <div class="meta">${esc(x.category)} · ${esc(x.label)} · ${esc(zoneName(x.zone))} · perception ${meter(x.confidence)}
        ${x.entity_id ? ` · <a href="#/insights/identities?id=${esc(x.entity_id)}" data-close>${esc(x.entity_id)}</a> re-ID ${meter(x.track_confidence)}` : ""}</div></div></div>`).join("") || '<p class="muted">No objects in this frame.</p>'}
      <h3>Alerts raised on this frame</h3>
      ${d.alerts_raised.map(alertBlock).join("") || '<p class="muted">None.</p>'}
      ${d.evidence_for.length ? `<h3>Evidence for later alerts</h3>${d.evidence_for.map(alertBlock).join("")}` : ""}
      <h3>Telemetry</h3>${tele}
      <h3>Event log lines</h3>
      ${d.events.map((e) => `<div class="small">${esc(e.message)} <span class="muted">(${esc(e.kind)}, ${esc(e.entity_id)})</span></div>`).join("") || '<p class="muted">None.</p>'}`;
    document.getElementById("drawer-replay").onclick = () => {
      drawer.hidden = true;
      location.hash = "#/live";
      loadTimeline().then(() => seek(positionOfFrame(frameId)));
    };
  } catch (err) {
    drawerBody.innerHTML = failure(err, "Could not load the frame");
  }
}

function alertBlock(a) {
  return `<div class="det"><div>${sev(a.severity_name)} <b>${esc(a.message)}</b>
    <div class="meta">${esc(a.rule_id)} · ${hhmm(a.ts)} · evidence ${a.evidence_frame_ids.map(flink).join(" ")}</div>
    <div class="meta">${esc(a.explanation)}</div></div></div>`;
}

drawer.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) drawer.hidden = true; });

// Any [data-frame] element anywhere opens the drawer.
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-frame]");
  if (el) { e.preventDefault(); openFrame(Number(el.dataset.frame)); }
});

// ------------------------------------------------------------------ toasts for new serious alerts
function toast(html, color, onClick) {
  const t = document.createElement("div");
  t.className = "toast";
  t.style.borderLeftColor = color;
  t.innerHTML = html;
  t.onclick = () => { onClick(); t.remove(); };
  toasts.prepend(t);
  setTimeout(() => t.remove(), 5000);
  while (toasts.children.length > 4) toasts.lastChild.remove();
}

subscribe((kind, data) => {
  // seeking re-runs earlier frames: only alerts from a playing replay count as new
  if (kind === "alerts" && replay.playing && !onAlertsView()) {
    unseen += data.length;
    for (const a of data) {
      if (a.severity < 30) continue; // MEDIUM and above pop up
      const color = getComputedStyle(document.documentElement).getPropertyValue(`--sev-${a.severity_name.toLowerCase()}`);
      toast(`${sev(a.severity_name)}<b>${hhmm(a.ts)}</b><div style="margin-top:4px">${esc(a.message)}</div>`, color, () => { location.hash = "#/live/alerts"; });
    }
  }
  showBadges();
});

// ------------------------------------------------------------------ keyboard
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Escape") { drawer.hidden = true; closeHelp(); return; }
  const s = SECTIONS.find((x) => x[2] === e.key);
  if (s) { location.hash = `#/${s[0]}`; return; }
  // a focused button handles Space itself; elsewhere on the mission view Space plays or pauses
  if (e.key === " " && active[0] === "live" && !active[1] && !e.target.closest("button, a")) { e.preventDefault(); toggle(); }
  if (e.key.toLowerCase() === "d") startDemo();
});

// ------------------------------------------------------------------ theme
const themeBtn = document.getElementById("theme-btn");
const showThemeIcon = () => { themeBtn.innerHTML = document.documentElement.dataset.theme === "dark" ? ICONS.sun : ICONS.moon; };
themeBtn.onclick = () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch { /* storage blocked */ }
  showThemeIcon();
  route(); // charts pick their colours from the theme when drawn
};
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* storage blocked */ }
showThemeIcon();
document.getElementById("demo-btn").onclick = () => startDemo();

window.addEventListener("hashchange", route);
loadStatus();
route();
