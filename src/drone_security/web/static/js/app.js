// App shell: hash router, navigation, status badge, frame drawer, alert toasts, keyboard shortcuts.
import { api, charts, esc, failure, flink, hhmm, loading, sev, zoneName, fixed } from "./lib.js";
import { replay, subscribe, loadTimeline, positionOfFrame, seek, toggle } from "./replay.js";
import { startDemo } from "./demo.js";

// [route, label, module, group, key] — modules are loaded on first visit
export const ROUTES = [
  ["mission", "Mission Control", "./screens/mission.js", "Live", "1"],
  ["alerts", "Live Alerts", "./screens/alerts.js", "Live", "2"],
  ["events", "Event Log", "./screens/events.js", "Investigate", "3"],
  ["explorer", "Frame Index", "./screens/explorer.js", "Investigate", "4"],
  ["chat", "Agent Chat", "./screens/chat.js", "Investigate", "5"],
  ["entities", "Entity Tracker", "./screens/entities.js", "Understand", "6"],
  ["summaries", "Summaries", "./screens/summaries.js", "Understand", "7"],
  ["context", "Context Manager", "./screens/context.js", "Understand", "8"],
  ["qa", "QA / Tests", "./screens/qa.js", "Prove it", "9"],
  ["evaluation", "Evaluation", "./screens/evaluation.js", "Prove it", "0"],
  ["architecture", "Architecture", "./screens/architecture.js", "Prove it", ""],
];

const view = document.getElementById("view");
const toasts = document.getElementById("toasts");
const nav = document.getElementById("nav");
let cleanup = null;
let renderToken = 0;
export const app = { status: null };

function buildNav() {
  let group = "";
  nav.innerHTML = ROUTES.map(([id, label, , g, key]) => {
    const head = g !== group ? `<div class="group">${esc((group = g))}</div>` : "";
    return `${head}<a href="#/${id}" data-route="${id}">${esc(label)}${id === "alerts" ? '<span class="dot" id="alerts-dot" hidden></span>' : ""}${key ? `<span class="key">${key}</span>` : ""}</a>`;
  }).join("");
}

async function route() {
  const id = (location.hash.match(/^#\/(\w+)/) || [])[1] || "mission";
  const entry = ROUTES.find((r) => r[0] === id) || ROUTES[0];
  nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.route === entry[0]));
  if (entry[0] === "alerts") document.getElementById("alerts-dot").hidden = true;
  if (cleanup) { try { cleanup(); } catch { /* screen already gone */ } cleanup = null; }
  toasts.innerHTML = "";
  while (charts.length) charts.pop().destroy();
  const token = ++renderToken;
  view.innerHTML = loading();
  try {
    const mod = await import(entry[2]);
    if (token !== renderToken) return; // user navigated again meanwhile
    view.innerHTML = "";
    cleanup = (await mod.render(view, { params: new URLSearchParams(location.hash.split("?")[1] || "") })) || null;
  } catch (err) {
    if (token === renderToken) view.innerHTML = failure(err, "This screen failed to load");
    console.error(err);
  }
  view.focus({ preventScroll: true });
}

// ------------------------------------------------------------------ status badge
async function loadStatus() {
  const badge = document.getElementById("mode-badge");
  try {
    const s = await api("/api/status");
    app.status = s;
    document.getElementById("property-name").textContent = `${s.property} · drone ${s.drone_id}`;
    document.getElementById("footage-day").textContent = `footage ${s.index.first_ts.slice(0, 10)} · ${s.index.frames} frames · ${s.run.alerts} alerts`;
    const g = s.gemini;
    badge.className = `badge ${g.mode}`;
    badge.textContent = g.mode === "gemini" ? `Gemini: ${g.model}` : "Offline mode";
    badge.title = g.mode === "gemini" ? "Agent chat and the briefing use Gemini; alerts stay deterministic." : `Deterministic offline path (${g.reason}). Set GEMINI_API_KEY to enable Gemini.`;
  } catch (err) {
    badge.className = "badge offline";
    badge.textContent = "API unreachable";
    badge.title = String(err.message || err);
  }
}

// ------------------------------------------------------------------ frame drawer
const drawer = document.getElementById("drawer");
const drawerBody = document.getElementById("drawer-body");

export async function openFrame(frameId) {
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
      <p style="font-size:16px;font-weight:600;margin:10px 0">“${esc(f.description)}”</p>
      <div class="row" style="margin-bottom:14px">
        ${d.prev ? `<button class="btn btn-sm" data-frame="${d.prev}">&larr; f${d.prev}</button>` : ""}
        ${d.next ? `<button class="btn btn-sm" data-frame="${d.next}">f${d.next} &rarr;</button>` : ""}
        <button class="btn btn-sm btn-accent" id="drawer-replay">Show in replay</button>
      </div>
      <h3>Telemetry</h3>${tele}
      <h3 style="margin-top:16px">Detections (${d.detections.length})</h3>
      ${d.detections.map((x) => `<div class="det">${x.category ? `<span class="cat cat-${esc(x.category)}"></span>` : ""}<div><b>${esc(x.display_name)}</b>
        <div class="meta">${esc(x.category)} · ${esc(x.label)}${x.action ? ` · ${esc(x.action)}` : ""} · ${esc(zoneName(x.zone))} · perception ${fixed(x.confidence, 2)}
        ${x.entity_id ? ` · entity <a href="#/entities?id=${esc(x.entity_id)}" data-close>${esc(x.entity_id)}</a> (re-ID ${fixed(x.track_confidence, 2)})` : ""}</div></div></div>`).join("") || '<p class="muted">No objects in this frame.</p>'}
      <h3 style="margin-top:16px">Alerts raised on this frame</h3>
      ${d.alerts_raised.map(alertBlock).join("") || '<p class="muted">None.</p>'}
      ${d.evidence_for.length ? `<h3 style="margin-top:16px">Evidence for later alerts</h3>${d.evidence_for.map(alertBlock).join("")}` : ""}
      <h3 style="margin-top:16px">Event log lines</h3>
      ${d.events.map((e) => `<div class="small">${esc(e.message)} <span class="muted">(${esc(e.kind)}, ${esc(e.entity_id)})</span></div>`).join("") || '<p class="muted">None.</p>'}`;
    document.getElementById("drawer-replay").onclick = () => {
      drawer.hidden = true;
      location.hash = "#/mission";
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

// Any [data-frame] button anywhere opens the drawer.
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-frame]");
  if (el) { e.preventDefault(); openFrame(Number(el.dataset.frame)); }
});

// ------------------------------------------------------------------ toasts for new serious alerts
export function toast(html, color, onClick) {
  const t = document.createElement("div");
  t.className = "toast";
  t.style.borderLeftColor = color;
  t.innerHTML = html;
  t.onclick = () => { onClick && onClick(); t.remove(); };
  toasts.prepend(t);
  setTimeout(() => t.remove(), 5000);
  while (toasts.children.length > 4) toasts.lastChild.remove();
}

subscribe((kind, data) => {
  const mini = document.getElementById("replay-mini");
  if (kind === "frame" || kind === "state" || kind === "snapshot") {
    mini.hidden = !replay.started;
    mini.innerHTML = replay.current ? `<span class="chip">${replay.playing ? "&#9654; replay" : "&#10073;&#10073; paused"} ${hhmm(replay.current.frame.ts)}</span>` : "";
  }
  if (kind === "alerts") {
    if (!location.hash.startsWith("#/alerts")) document.getElementById("alerts-dot").hidden = false;
    if (!replay.playing) return; // seeking re-runs earlier frames: only a playing replay is "live"
    for (const a of data) {
      if (a.severity < 30) continue; // MEDIUM and above pop up
      const color = getComputedStyle(document.documentElement).getPropertyValue(`--sev-${a.severity_name.toLowerCase()}`);
      toast(`${sev(a.severity_name)} <b>${esc(a.rule_id)}</b><div>${esc(a.message)}</div>`, color, () => { location.hash = "#/alerts"; });
    }
  }
});

// ------------------------------------------------------------------ keyboard
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Escape") { drawer.hidden = true; return; }
  const r = ROUTES.find((x) => x[4] === e.key);
  if (r) { location.hash = `#/${r[0]}`; return; }
  if (e.key === " " && location.hash.startsWith("#/mission") ) { e.preventDefault(); toggle(); }
  if (e.key.toLowerCase() === "d") startDemo();
});

document.getElementById("theme-btn").onclick = () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch { /* storage blocked */ }
  route();
};
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* storage blocked */ }
document.getElementById("demo-btn").onclick = () => startDemo();

buildNav();
window.addEventListener("hashchange", route);
loadStatus();
route();
