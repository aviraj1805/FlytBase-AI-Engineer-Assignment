// Live › Mission: the day replayed through the server's pipeline over SSE: property map, player, the current frame and live feeds.
import { api, esc, failure, fixed, flink, hhmm, lead, sev, zoneName, SEV_COLOR } from "../lib.js";
import { replay, subscribe, loadTimeline, pause, seek, setSpeed, toggle } from "../replay.js";

export const HELP = `Replays the simulated patrol day frame by frame. The server runs the <b>real pipeline</b> (parse → track → rules → index)
  on each frame as it streams, so telemetry, detections, the activity log and alerts appear live. The drone is drawn at its GPS fix,
  objects at the zone the analyzer assigned (captions name a zone, not coordinates), and the highlighted zone is where the camera is looking.
  Coloured ticks on the timeline are the day's alerts: click one to jump there.<br><br>
  <b>Keys:</b> Space play / pause · ← → one frame · N next alert.
  <div class="req">Covers: process telemetry + video frames, real-time alerts, the property map (main gate, garage, perimeter, restricted warehouse).</div>`;

const SPEEDS = [[60, "1 min / s"], [300, "5 min / s"], [600, "10 min / s"], [900, "15 min / s"], [1800, "30 min / s"], [3600, "1 hour / s"]];
const PLAY = '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>';
const PAUSE = '<svg viewBox="0 0 24 24"><path d="M6 4h4.5v16H6zM13.5 4H18v16h-4.5z"/></svg>';
const FEED_LIMIT = 40;

let property = null;
let feedTab = "alerts";     // the side feed shows alerts or the activity log
const expanded = new Set(); // alerts opened in the side feed, kept while new frames arrive
let epoch = 0;              // bumps when a seek replaces the replay's lists

export async function render(view) {
  view.innerHTML = lead("Watch one patrol day go through the real pipeline. Press play: every frame is analysed, tracked and checked against the alert rules as it streams in.",
    '<span id="mc-counters" class="row"></span>') + '<div id="mc-body"><div class="skeleton" style="height:480px"></div></div>';
  try {
    [property] = await Promise.all([property || api("/api/property"), loadTimeline()]);
  } catch (err) {
    view.querySelector("#mc-body").innerHTML = failure(err, "Could not load the replay timeline");
    return;
  }
  const body = view.querySelector("#mc-body");
  body.innerHTML = `
    <div class="mission">
      <div class="stack">
        <div class="card map-card" id="map-card"></div>
        <div class="card player" id="player">
          <div class="player-row">
            <button class="play-btn" id="play" title="Play / pause (Space)" aria-label="Play or pause"></button>
            <div class="btn-group">
              <button class="icon-btn" id="restart" title="Back to 00:00" aria-label="Restart">&#8634;</button>
              <button class="icon-btn" id="back" title="Previous frame (←)" aria-label="Previous frame">&#9664;</button>
              <button class="icon-btn" id="fwd" title="Next frame (→)" aria-label="Next frame">&#9654;</button>
            </div>
            <button class="btn" id="next-alert" title="Jump to the next alert on the timeline (N)">Next alert &#8677;</button>
            <span class="spacer"></span>
            <label class="speed">Speed <select id="speed">${SPEEDS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
            <span class="pos" id="pos"></span>
          </div>
          <div class="scrubber">
            <div class="markers" id="markers"></div>
            <input type="range" id="scrub" min="0" max="${replay.frames.length - 1}" value="${replay.position}" aria-label="Timeline">
            <div class="hours">${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span>${String(h).padStart(2, "0")}:00</span>`).join("")}</div>
          </div>
          <div id="mc-error"></div>
        </div>
      </div>
      <div class="stack">
        <div class="card now" id="frame-panel"></div>
        <div class="card feed-card">
          <div class="seg" id="feed-tabs"><button data-tab="alerts">Alerts <span class="muted num" id="n-alerts"></span></button><button data-tab="activity">Activity log <span class="muted num" id="n-logs"></span></button></div>
          <div class="feed" id="mc-feed"></div>
        </div>
      </div>
    </div>`;

  body.querySelector("#speed").value = String(replay.speed);
  body.querySelector("#play").onclick = toggle;
  body.querySelector("#back").onclick = () => step(-1);
  body.querySelector("#fwd").onclick = () => step(1);
  body.querySelector("#restart").onclick = () => seek(0);
  body.querySelector("#next-alert").onclick = nextAlert;
  body.querySelector("#speed").onchange = (e) => setSpeed(Number(e.target.value));
  const scrub = body.querySelector("#scrub");
  scrub.oninput = () => { body.querySelector("#clock").textContent = replay.frames[scrub.value].ts.slice(11, 19); };
  scrub.onchange = () => seek(Number(scrub.value));
  body.querySelector("#feed-tabs").onclick = (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) { feedTab = b.dataset.tab; update(); }
  };
  body.querySelector("#mc-feed").onclick = (e) => {
    if (e.target.closest("[data-frame], a, button")) return;
    const row = e.target.closest(".feed-row[data-key]");
    if (!row) return;
    expanded.has(row.dataset.key) ? expanded.delete(row.dataset.key) : expanded.add(row.dataset.key);
    row.classList.toggle("open");
  };
  drawMarkers(body.querySelector("#markers"));
  drawMap(body.querySelector("#map-card"));
  update();

  const onKey = (e) => {
    if (e.target.closest("input, textarea, select")) return;
    if (e.key === "ArrowRight") step(1);
    if (e.key === "ArrowLeft") step(-1);
    if (e.key.toLowerCase() === "n") nextAlert();
  };
  document.addEventListener("keydown", onKey);
  const unsub = subscribe((kind, data) => {
    if (kind === "snapshot") epoch++;
    update();
    if (kind === "alerts") pulse(data);
  });
  return () => { unsub(); document.removeEventListener("keydown", onKey); };
}

function step(delta) {
  if (replay.playing) pause();
  seek(replay.position + delta);
}

// Jump (paused) to the next alert marker after the current position.
function nextAlert() {
  const next = replay.markers.find((m) => m.position > (replay.current ? replay.position : -1));
  if (!next) return;
  if (replay.playing) pause();
  seek(next.position);
}

// ------------------------------------------------------------------ rendering
function update() {
  const $ = (id) => document.getElementById(id);
  if (!$("tele")) return;
  const cur = replay.current;
  $("play").innerHTML = replay.playing ? PAUSE : PLAY;
  $("speed").value = String(replay.speed);
  $("map-card").classList.toggle("paused", !replay.playing);
  $("scrub").value = replay.position;
  $("clock").textContent = cur ? cur.frame.ts.slice(11, 19) : "--:--:--";
  $("clock").classList.toggle("on", replay.playing);
  $("pos").textContent = `frame ${replay.position + 1} / ${replay.frames.length}`;
  $("mc-error").innerHTML = replay.error ? `<div class="state error" style="margin-top:10px">${esc(replay.error)}</div>` : "";
  $("mc-counters").innerHTML = replay.started
    ? `<span class="chip">${replay.position + 1} frames processed</span><span class="chip">${replay.alerts.length} alert${replay.alerts.length === 1 ? "" : "s"}</span>
       <span class="chip" title="Rule firings before cooldown and correlation folded them into alerts">${replay.rawFirings} raw rule firings</span>`
    : "";
  $("n-alerts").textContent = replay.alerts.length || "";
  $("n-logs").textContent = replay.logs.length || "";
  $("feed-tabs").querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === feedTab));
  renderHud(cur);
  renderTelemetry($("tele"), cur);
  renderFrame($("frame-panel"), cur);
  updateMap(cur);
  syncFeed($("mc-feed"));
}

function renderHud(cur) {
  const t = cur && cur.frame.telemetry;
  document.getElementById("hud-zone").textContent = cur ? `Camera · ${zoneName(cur.frame.zone)}` : "Ready";
  const mode = document.getElementById("hud-mode");
  mode.hidden = !cur;
  mode.className = `tag ${cur && !t ? "warn" : ""}`;
  mode.textContent = t ? t.mode.toUpperCase() : "NO TELEMETRY";
}

function renderTelemetry(el, cur) {
  const t = cur && cur.frame.telemetry;
  if (cur && !t) {
    el.innerHTML = '<div class="tele lost"><div class="l">Telemetry</div><div class="v warn-text">Packet lost for this frame (handled by ingest)</div></div>';
    return;
  }
  const cell = (l, v, extra = "") => `<div class="tele"><div class="l">${l}</div><div class="v">${v}</div>${extra}</div>`;
  const bat = t ? t.battery_pct : 0;
  const batColor = bat < 15 ? "var(--bad)" : bat < 30 ? "var(--warn)" : "var(--ok)";
  el.innerHTML =
    cell("Altitude", t ? `${fixed(t.altitude_m)} m` : "–") +
    cell("Battery", t ? `${fixed(bat, 0)} %` : "–", `<div class="bar"><i style="width:${bat}%;background:${batColor}"></i></div>`) +
    cell("Wind", t ? `${fixed(t.wind_mps)} m/s` : "–") +
    cell("GPS HDOP", t ? `<span class="${t.gps_hdop > 2 ? "bad-text" : ""}">${fixed(t.gps_hdop, 2)}</span>` : "–") +
    cell("From dock", t ? `${fixed(t.distance_from_home_m, 0)} m` : "–") +
    cell("Objects in view", cur ? String(cur.detections.length) : "–");
}

function renderFrame(el, cur) {
  const id = cur ? String(cur.frame.frame_id) : "";
  if (el.dataset.frame === id && el.innerHTML) return; // same frame: keep the DOM (and its entry animations) as is
  el.dataset.frame = id;
  if (!cur) {
    el.innerHTML = `<h3>Now</h3><p class="muted" style="margin:6px 0 0">Nothing processed yet. Press <b>play</b> to stream the day through the pipeline,
      or click a coloured tick on the timeline to jump straight to an incident.</p>`;
    return;
  }
  const f = cur.frame;
  el.innerHTML = `<div class="card-head" style="margin-bottom:4px"><h3>Now · ${hhmm(f.ts)}</h3>${flink(f.frame_id)}</div>
    <div class="caption">“${esc(f.description)}”</div>
    <div class="objs">${cur.detections.map((d) => `<span class="obj-chip" title="${esc(d.category)} · ${esc(zoneName(d.zone))} · perception ${fixed(d.confidence, 2)}${
      d.entity_id ? ` · ${esc(d.entity_id)} (re-ID ${fixed(d.track_confidence, 2)})` : ""}"><span class="cat cat-${esc(d.category)}"></span>${esc(d.display_name)}${
      d.action ? ` <em>${esc(d.action)}</em>` : ""}</span>`).join("") || '<span class="muted small">No objects detected (routine patrol frame).</span>'}</div>
    ${cur.alerts.map((a) => `<div class="banner banner-${a.severity_name}">${sev(a.severity_name)} <code class="small muted">${esc(a.rule_id)}</code>
      <b>${esc(a.message)}</b><div class="meta">${esc(a.explanation)}</div></div>`).join("")}`;
}

const alertKey = (a) => `${a.ts}|${a.rule_id}|${a.frame_id}`;
const alertRow = (a, fresh) => `<div class="feed-row ${fresh ? "fresh" : ""} ${expanded.has(alertKey(a)) ? "open" : ""}" data-key="${esc(alertKey(a))}">
  <span class="dot dot-${a.severity_name}" title="${a.severity_name}"></span><span class="t">${hhmm(a.ts)}</span><span>${esc(a.message)}</span><span class="chev">&#8250;</span>
  <div class="more">${sev(a.severity_name)} <code>${esc(a.rule_id)}</code> · ${esc(zoneName(a.zone))}
    <div style="margin-top:4px">${esc(a.explanation)}</div><div style="margin-top:4px">Evidence ${a.evidence_frame_ids.map(flink).join(" ")}</div></div></div>`;
const logRow = (l, fresh) => `<div class="feed-row log ${fresh ? "fresh" : ""}"><span class="t">${hhmm(l.ts)}</span><span>${esc(l.message)}</span>${flink(l.frame_id)}</div>`;

// While the replay plays the lists only grow, so new rows are prepended and animate in once; a seek or a tab switch redraws.
function syncFeed(el) {
  const items = feedTab === "alerts" ? replay.alerts : replay.logs;
  const row = feedTab === "alerts" ? alertRow : logRow;
  const tag = `${feedTab}:${epoch}`;
  const shown = Number(el.dataset.count || 0);
  if (el.dataset.tag === tag && shown > 0 && items.length >= shown) {
    if (items.length === shown) return;
    el.insertAdjacentHTML("afterbegin", items.slice(shown).reverse().map((x) => row(x, true)).join(""));
    while (el.children.length > FEED_LIMIT) el.lastElementChild.remove();
  } else {
    el.innerHTML = items.length ? items.slice(-FEED_LIMIT).reverse().map((x) => row(x, false)).join("")
      : `<div class="state">${replay.started ? `No ${feedTab === "alerts" ? "alerts" : "log lines"} yet.`
        : feedTab === "alerts" ? "Press play: alerts appear here the moment the rule engine raises them." : "What changed in each frame (new arrivals, zone changes, actions) streams here."}</div>`;
  }
  el.dataset.tag = tag;
  el.dataset.count = String(items.length);
}

function drawMarkers(el) {
  const n = replay.frames.length - 1;
  const colors = SEV_COLOR();
  el.innerHTML = replay.markers.map((m) => `<button class="marker" style="left:${(m.position / n) * 100}%;background:${colors[m.severity_name]}"
    data-pos="${m.position}" title="${esc(hhmm(m.ts))} ${esc(m.severity_name)} ${esc(m.rule_id)}: ${esc(m.message)}" aria-label="Jump to ${esc(m.rule_id)} at ${esc(hhmm(m.ts))}"></button>`).join("");
  el.onclick = (e) => { const b = e.target.closest(".marker"); if (b) { if (replay.playing) pause(); seek(Number(b.dataset.pos)); } };
}

// ------------------------------------------------------------------ property map (SVG)
// Local metres around the dock: x east, y north (flipped for SVG). Good enough at property scale.
let proj = null;
function project(lat, lon) {
  const { home } = property;
  const x = (lon - home.lon) * 111320 * Math.cos((home.lat * Math.PI) / 180);
  const y = -(lat - home.lat) * 110540;
  return [x, y];
}

function drawMap(card) {
  const zones = property.zones.map((z) => ({ ...z, xy: project(z.lat, z.lon) }));
  // fit the view to the zones (+ margin); the drone can fly outside it (GPS drift), see updateMap
  const pad = 22;
  let minX = Math.min(...zones.map((z) => z.xy[0] - z.radius_m)) - pad, maxX = Math.max(...zones.map((z) => z.xy[0] + z.radius_m)) + pad;
  let minY = Math.min(...zones.map((z) => z.xy[1] - z.radius_m)) - pad, maxY = Math.max(...zones.map((z) => z.xy[1] + z.radius_m)) + pad + 30; // room for the legend
  const zoneSvg = zones.map((z) => {
    const [x, y] = z.xy;
    const stroke = z.restricted ? "var(--warn)" : z.perimeter ? "var(--bad)" : "var(--accent)";
    const dash = z.perimeter ? "5 3" : z.restricted ? "2 2" : "";
    const fill = z.restricted ? "url(#hatch)" : "var(--map-zone)";
    return `<g class="zone" data-zone="${esc(z.id)}"><circle class="area" cx="${x}" cy="${y}" r="${z.radius_m}" fill="${fill}" stroke="${stroke}" stroke-width="0.8" stroke-dasharray="${dash}"/>
      <text class="zone-label" x="${x}" y="${y + z.radius_m - 3}" text-anchor="middle">${esc(z.name)}${z.restricted ? " · restricted" : z.perimeter ? " · perimeter" : ""}</text></g>`;
  }).join("");
  const blades = [[-4.5, -4.5], [4.5, -4.5], [-4.5, 4.5], [4.5, 4.5]].map(([x, y]) =>
    `<g transform="translate(${x},${y})"><g class="blade"><path d="M-2.6 0H2.6M0-2.6V2.6" stroke="var(--accent-2)" stroke-width=".9"/></g></g>`).join("");
  card.innerHTML = `<div style="position:relative">
      <svg viewBox="${minX} ${minY} ${maxX - minX} ${maxY - minY}" id="map" role="img" aria-label="Property map with zones, drone and detected objects">
        <defs>
          <pattern id="grid" width="10" height="10" patternUnits="userSpaceOnUse"><path d="M10 0H0V10" fill="none" stroke="var(--map-grid)" stroke-width=".5"/></pattern>
          <pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="4" fill="rgba(245,158,11,.08)"/><line x1="0" y1="0" x2="0" y2="4" stroke="rgba(245,158,11,.35)" stroke-width="1.2"/></pattern>
        </defs>
        <rect id="grid-bg" fill="url(#grid)"/>
        <circle cx="0" cy="0" r="${property.geofence_radius_m}" fill="none" stroke="var(--muted)" stroke-dasharray="2 4" stroke-width="0.7"/>
        ${zoneSvg}
        <text class="zone-label" id="north" text-anchor="end">N &#8593;</text>
        <line id="gaze" stroke="var(--accent-2)" stroke-width=".7" stroke-dasharray="2 2" opacity="0"/>
        <polyline id="trail" fill="none" stroke="var(--accent)" stroke-width="1.1" stroke-opacity=".5" stroke-linejoin="round" points=""/>
        <g id="objects"></g>
        <g id="pulses"></g>
        <g id="drone" class="drone-icon"><g transform="scale(1.5)"><circle r="9" fill="var(--accent)" fill-opacity=".15"/>
          <path d="M-4.5-4.5L4.5 4.5M4.5-4.5L-4.5 4.5" stroke="var(--accent)" stroke-width="1.4"/><circle r="2.2" fill="var(--accent)"/>${blades}</g>
          <text id="drone-off" class="obj-label" x="16" y="3" fill="var(--bad)"></text></g>
      </svg>
      <div class="hud hud-tl"><span class="rec" id="clock">--:--:--</span><span class="tag" id="hud-zone">Ready</span></div>
      <div class="hud hud-tr"><span class="tag" id="hud-mode" hidden></span></div>
      <div class="map-legend"><span><span class="cat cat-person"></span>person</span><span><span class="cat cat-vehicle"></span>vehicle</span>
        <span><span class="cat cat-animal"></span>animal</span><span><span class="cat cat-object"></span>object</span><span><span class="cat cat-infrastructure"></span>gate / door</span>
        <span style="color:var(--bad)">- - perimeter</span><span style="color:var(--warn)">/// restricted</span><span>&middot;&middot;&middot; ${property.geofence_radius_m} m geofence</span></div>
    </div>
    <div class="tele-strip" id="tele"></div>`;
  // widen (or heighten) the view to the map's on-screen shape, so the grid fills the card instead of leaving bands
  const svg = card.querySelector("svg");
  const aspect = svg.clientWidth / svg.clientHeight;
  if (aspect > (maxX - minX) / (maxY - minY)) {
    const grow = ((maxY - minY) * aspect - (maxX - minX)) / 2;
    minX -= grow; maxX += grow;
  } else if (aspect) {
    const grow = ((maxX - minX) / aspect - (maxY - minY)) / 2;
    minY -= grow; maxY += grow;
  }
  svg.setAttribute("viewBox", `${minX} ${minY} ${maxX - minX} ${maxY - minY}`);
  Object.entries({ x: minX, y: minY, width: maxX - minX, height: maxY - minY }).forEach(([k, v]) => svg.querySelector("#grid-bg").setAttribute(k, v));
  svg.querySelector("#north").setAttribute("x", maxX - 10);
  svg.querySelector("#north").setAttribute("y", minY + 50);
  proj = { zones, box: [minX, minY, maxX, maxY], objects: "" };
}

function updateMap(cur) {
  const svg = document.getElementById("map");
  if (!svg || !proj) return;
  const drone = svg.querySelector("#drone");
  const gaze = svg.querySelector("#gaze");
  const t = cur && cur.frame.telemetry;
  const zone = cur && proj.zones.find((z) => z.id === cur.frame.zone);
  svg.querySelectorAll(".zone").forEach((g) => g.classList.toggle("active", !!zone && g.dataset.zone === zone.id));
  if (t) {
    let [x, y] = project(t.lat, t.lon);
    const [x0, y0, x1, y1] = proj.box;
    const off = x < x0 || x > x1 || y < y0 || y > y1;
    // outside the property view (e.g. the GPS-drift episode): pin it to the edge and say how far out it is
    x = Math.min(Math.max(x, x0 + 8), x1 - 8);
    y = Math.min(Math.max(y, y0 + 8), y1 - 8);
    drone.setAttribute("transform", `translate(${x.toFixed(1)},${y.toFixed(1)})`);
    drone.querySelector("#drone-off").textContent = off ? `off map: ${t.distance_from_home_m.toFixed(0)} m from dock` : "";
    drone.style.opacity = 1;
    if (zone) {
      gaze.setAttribute("x1", x.toFixed(1)); gaze.setAttribute("y1", y.toFixed(1));
      gaze.setAttribute("x2", zone.xy[0].toFixed(1)); gaze.setAttribute("y2", zone.xy[1].toFixed(1));
    }
    gaze.setAttribute("opacity", zone ? ".8" : "0");
  } else {
    drone.style.opacity = cur ? 0.35 : 1; // no fix this frame: keep the last position, dimmed
    gaze.setAttribute("opacity", "0");
  }
  svg.querySelector("#trail").setAttribute("points", replay.trail.map(([la, lo]) => project(la, lo).map((v) => v.toFixed(1)).join(",")).join(" "));
  const byZone = {};
  const objects = (cur ? cur.detections : []).map((d) => {
    const z = proj.zones.find((zz) => zz.id === d.zone);
    if (!z) return "";
    const i = (byZone[d.zone] = (byZone[d.zone] || 0) + 1) - 1;
    const angle = i * 2.1 + 0.6; // spread several objects in one zone around its centre
    const r = Math.min(z.radius_m * 0.45, 12);
    const x = z.xy[0] + Math.cos(angle) * r, y = z.xy[1] + Math.sin(angle) * r;
    return `<g class="obj"><circle cx="${x}" cy="${y}" r="5" fill="var(--cat-${esc(d.category)})" stroke="var(--map-bg)" stroke-width="1.8"><title>${esc(d.display_name)}</title></circle>
      <text class="obj-label" x="${x + 8}" y="${y + 3}">${esc(d.display_name)}${d.action ? ` · ${esc(d.action)}` : ""}</text></g>`;
  }).join("");
  if (objects !== proj.objects) { // unchanged objects keep their DOM, so they do not re-animate every frame
    proj.objects = objects;
    svg.querySelector("#objects").innerHTML = objects;
  }
}

function pulse(alerts) {
  const svg = document.getElementById("map");
  if (!svg || !proj) return;
  const colors = SEV_COLOR();
  svg.querySelector("#pulses").innerHTML = alerts.map((a) => {
    const z = proj.zones.find((zz) => zz.id === a.zone);
    if (!z) return "";
    return `<circle class="alert-pulse" cx="${z.xy[0]}" cy="${z.xy[1]}" r="${z.radius_m}" fill="none" stroke="${colors[a.severity_name]}" stroke-width="2"/>`;
  }).join("");
}
