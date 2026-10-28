// Mission Control: live replay of the day. Each frame comes from the server's pipeline over SSE.
import { api, esc, failure, fixed, flink, hhmm, pageHead, sev, zoneName, SEV_COLOR } from "../lib.js";
import { replay, subscribe, loadTimeline, play, pause, seek, setSpeed, toggle } from "../replay.js";

const SPEEDS = [[60, "60× (1 min/s)"], [300, "300×"], [600, "600×"], [1800, "1800×"], [3600, "3600× (1 h/s)"]];
const HELP = `Replays the simulated patrol day frame by frame. The server runs the <b>real pipeline</b> (parse → track → rules → index)
  on each frame as it streams, so telemetry, detections, logs and alerts appear live.<br><br>
  Covers: <b>process telemetry + video frames</b>, <b>real-time alerts</b>, the property map (zones: main gate, garage, perimeter,
  restricted warehouse). Space = play/pause, ←/→ = step.`;

let property = null;

export async function render(view) {
  view.innerHTML = pageHead("Mission Control", HELP, `<span id="mc-counters" class="row small muted"></span>`) +
    `<div id="mc-body"><div class="telemetry">${'<div class="tele skeleton" style="height:58px"></div>'.repeat(8)}</div></div>`;
  try {
    [property] = await Promise.all([property || api("/api/property"), loadTimeline()]);
  } catch (err) {
    view.querySelector("#mc-body").innerHTML = failure(err, "Could not load the replay timeline");
    return;
  }
  const body = view.querySelector("#mc-body");
  body.innerHTML = `
    <div class="telemetry" id="tele"></div>
    <div class="card" style="margin-bottom:14px" id="controls-card">
          <div class="controls">
            <button class="btn btn-accent" id="play" title="Play / pause (Space)" style="min-width:92px"></button>
            <button class="btn" id="back" title="Previous frame (←)">&#9664;&#9664;</button>
            <button class="btn" id="fwd" title="Next frame (→)">&#9654;&#9654;</button>
            <button class="btn" id="restart" title="Back to 00:00">&#8634; Restart</button>
            <button class="btn" id="next-alert" title="Jump to the next alert on the timeline (N)">Next alert &#8677;</button>
            <select id="speed" title="Replay speed: footage time per real second">${SPEEDS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
            <span class="spacer" style="flex:1"></span>
            <span class="clock" id="clock">--:--</span>
            <span class="muted small" id="pos"></span>
          </div>
          <div class="scrubber">
            <div class="markers" id="markers"></div>
            <input type="range" id="scrub" min="0" max="${replay.frames.length - 1}" value="${replay.position}" aria-label="Timeline">
            <div class="hours">${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span>${String(h).padStart(2, "0")}:00</span>`).join("")}</div>
          </div>
          <div id="mc-error"></div>
        </div>
    <div class="mission">
      <div class="stack">
        <div class="card map-card" id="map-card"></div>
      </div>
      <div class="stack">
        <div class="card frame-panel" id="frame-panel"></div>
        <div class="card"><div class="card-head"><h3>Alerts in this replay</h3><a href="#/alerts" class="small">All alerts &rarr;</a></div><div class="feed" id="mc-alerts"></div></div>
        <div class="card"><div class="card-head"><h3>Event log</h3><a href="#/events" class="small">Full log &rarr;</a></div><div class="feed" id="mc-logs"></div></div>
      </div>
    </div>`;

  body.querySelector("#speed").value = String(replay.speed);
  body.querySelector("#play").onclick = toggle;
  body.querySelector("#back").onclick = () => { if (replay.playing) pause(); seek(replay.position - 1); };
  body.querySelector("#fwd").onclick = () => { if (replay.playing) pause(); seek(replay.position + 1); };
  body.querySelector("#restart").onclick = () => seek(0);
  body.querySelector("#next-alert").onclick = nextAlert;
  body.querySelector("#speed").onchange = (e) => setSpeed(Number(e.target.value));
  const scrub = body.querySelector("#scrub");
  scrub.oninput = () => { body.querySelector("#clock").textContent = hhmm(replay.frames[scrub.value].ts); };
  scrub.onchange = () => seek(Number(scrub.value));
  drawMarkers(body.querySelector("#markers"));
  drawMap(body.querySelector("#map-card"));
  update();

  const onKey = (e) => {
    if (e.target.closest("input, textarea, select")) return;
    if (e.key === "ArrowRight") { if (replay.playing) pause(); seek(replay.position + 1); }
    if (e.key === "ArrowLeft") { if (replay.playing) pause(); seek(replay.position - 1); }
    if (e.key.toLowerCase() === "n") nextAlert();
  };
  document.addEventListener("keydown", onKey);
  const unsub = subscribe((kind, data) => {
    update();
    if (kind === "alerts") pulse(data);
  });
  return () => { unsub(); document.removeEventListener("keydown", onKey); };
}

// Jump (paused) to the frame of the next alert marker after the current position.
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
  $("play").innerHTML = replay.playing ? "&#10073;&#10073; Pause" : "&#9654; Play";
  $("scrub").value = replay.position;
  $("clock").textContent = cur ? hhmm(cur.frame.ts) : "--:--";
  $("pos").textContent = `frame ${replay.position + 1} / ${replay.frames.length}`;
  $("mc-error").innerHTML = replay.error ? `<div class="state error" style="margin-top:10px">${esc(replay.error)}</div>` : "";
  const counts = {};
  replay.alerts.forEach((a) => (counts[a.severity_name] = (counts[a.severity_name] || 0) + 1));
  $("mc-counters").innerHTML = replay.started
    ? `<span class="chip">${replay.position + 1} frame${replay.position ? "s" : ""} processed</span><span class="chip">${replay.alerts.length} alert${replay.alerts.length === 1 ? "" : "s"}</span>
       <span class="chip" title="Rule firings before cooldown and correlation">${replay.rawFirings} raw rule firings</span>`
    : "";
  renderTelemetry($("tele"), cur);
  renderFrame($("frame-panel"), cur);
  updateMap(cur);
  $("mc-alerts").innerHTML = replay.alerts.length
    ? replay.alerts.slice(-30).reverse().map((a) => `<div class="line"><span class="t">${hhmm(a.ts)}</span>${sev(a.severity_name)}<span>${esc(a.message)} ${flink(a.frame_id)}</span></div>`).join("")
    : `<div class="muted">${replay.started ? "No alerts yet." : "Press Play: alerts appear here as the rule engine raises them."}</div>`;
  $("mc-logs").innerHTML = replay.logs.length
    ? replay.logs.slice(-40).reverse().map((l) => `<div class="line"><span class="t">${hhmm(l.ts)}</span><span>${esc(l.message)} ${flink(l.frame_id)}</span></div>`).join("")
    : `<div class="muted">${replay.started ? "No log lines yet." : "Event log lines (what changed) stream here."}</div>`;
}

function renderTelemetry(el, cur) {
  const t = cur && cur.frame.telemetry;
  const cell = (l, v, extra = "") => `<div class="tele"><div class="l">${l}</div><div class="v">${v}</div>${extra}</div>`;
  if (!cur) {
    el.innerHTML = cell("Time", "--:--") + cell("Zone", "–") + cell("Altitude", "–") + cell("Battery", "–") +
      cell("Mode", "–") + cell("Wind", "–") + cell("GPS HDOP", "–") + cell("From dock", "–");
    return;
  }
  if (!t) {
    el.innerHTML = cell("Time", hhmm(cur.frame.ts)) + cell("Zone", esc(zoneName(cur.frame.zone))) +
      `<div class="tele" style="grid-column: span 6"><div class="l">Telemetry</div><div class="v warn-text" style="font-size:14px">Packet lost for this frame (handled by ingest)</div></div>`;
    return;
  }
  const batColor = t.battery_pct < 15 ? "var(--bad)" : t.battery_pct < 30 ? "var(--warn)" : "var(--ok)";
  el.innerHTML =
    cell("Time", `${hhmm(cur.frame.ts)}<span class="muted small"> ${cur.frame.ts.slice(17)}s</span>`) +
    cell("Zone (camera)", esc(zoneName(cur.frame.zone))) +
    cell("Altitude", `${fixed(t.altitude_m)} m`) +
    cell("Battery", `${fixed(t.battery_pct, 0)} %`, `<div class="bar"><i style="width:${t.battery_pct}%;background:${batColor}"></i></div>`) +
    cell("Mode", esc(t.mode)) +
    cell("Wind", `${fixed(t.wind_mps)} m/s`) +
    cell("GPS HDOP", `<span class="${t.gps_hdop > 2 ? "bad-text" : ""}">${fixed(t.gps_hdop, 2)}</span>`) +
    cell("From dock", `${fixed(t.distance_from_home_m, 0)} m`);
}

function renderFrame(el, cur) {
  if (!cur) {
    el.innerHTML = `<h3>Current frame</h3><p class="muted">Nothing processed yet. Press <b>Play</b> (or Space) to stream the day through the pipeline, or click a marker on the timeline to jump to an incident.</p>`;
    return;
  }
  const f = cur.frame;
  el.innerHTML = `<div class="card-head"><h3>Current frame</h3>${flink(f.frame_id)}</div>
    <div class="caption">“${esc(f.description)}”</div>
    ${cur.detections.map((d) => `<div class="det"><span class="cat cat-${esc(d.category)}"></span><div><b>${esc(d.display_name)}</b>${d.action ? ` <span class="muted">${esc(d.action)}</span>` : ""}
      <div class="meta">${esc(d.category)} · ${esc(zoneName(d.zone))} · perception ${fixed(d.confidence, 2)}${d.entity_id ? ` · ${esc(d.entity_id)} (re-ID ${fixed(d.track_confidence, 2)})` : ""}</div></div></div>`).join("")
      || '<div class="det muted">No objects detected (patrol frame).</div>'}
    ${cur.alerts.map((a) => `<div class="det">${sev(a.severity_name)}<div><b>${esc(a.message)}</b><div class="meta">${esc(a.explanation)}</div></div></div>`).join("")}`;
}

function drawMarkers(el) {
  const n = replay.frames.length - 1;
  const colors = SEV_COLOR();
  el.innerHTML = replay.markers.map((m) => `<button class="marker" style="left:${(m.position / n) * 100}%;background:${colors[m.severity_name]}"
    data-pos="${m.position}" title="${esc(hhmm(m.ts))} ${esc(m.severity_name)} ${esc(m.rule_id)}: ${esc(m.message)}" aria-label="Jump to ${esc(m.rule_id)} at ${esc(hhmm(m.ts))}"></button>`).join("");
  el.onclick = (e) => { const b = e.target.closest(".marker"); if (b) seek(Number(b.dataset.pos)); };
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
  const minX = Math.min(...zones.map((z) => z.xy[0] - z.radius_m)) - pad, maxX = Math.max(...zones.map((z) => z.xy[0] + z.radius_m)) + pad;
  const minY = Math.min(...zones.map((z) => z.xy[1] - z.radius_m)) - pad, maxY = Math.max(...zones.map((z) => z.xy[1] + z.radius_m)) + pad;
  proj = { zones, box: [minX, minY, maxX, maxY] };
  const zoneSvg = zones.map((z) => {
    const [x, y] = z.xy;
    const stroke = z.restricted ? "var(--warn)" : z.perimeter ? "var(--bad)" : "var(--accent)";
    const dash = z.perimeter ? "5 3" : z.restricted ? "2 2" : "";
    const fill = z.restricted ? "url(#hatch)" : "var(--map-zone)";
    return `<g><circle cx="${x}" cy="${y}" r="${z.radius_m}" fill="${fill}" stroke="${stroke}" stroke-width="0.8" stroke-dasharray="${dash}"/>
      <text class="zone-label" x="${x}" y="${y + z.radius_m - 3}" text-anchor="middle">${esc(z.name)}${z.restricted ? " · restricted" : z.perimeter ? " · perimeter" : ""}</text></g>`;
  }).join("");
  card.innerHTML = `<svg viewBox="${minX} ${minY} ${maxX - minX} ${maxY - minY}" id="map" role="img" aria-label="Property map with zones, drone and detected objects">
      <defs><pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="4" fill="rgba(245,158,11,.08)"/><line x1="0" y1="0" x2="0" y2="4" stroke="rgba(245,158,11,.35)" stroke-width="1.2"/></pattern></defs>
      <circle cx="0" cy="0" r="${property.geofence_radius_m}" fill="none" stroke="var(--muted)" stroke-dasharray="2 4" stroke-width="0.7"/>
      ${zoneSvg}
      <text class="zone-label" x="${maxX - 4}" y="${minY + 8}" text-anchor="end">N &#8593;</text>
      <polyline id="trail" fill="none" stroke="var(--accent)" stroke-width="1" stroke-opacity=".45" points=""/>
      <g id="objects"></g>
      <g id="pulses"></g>
      <g id="drone" class="drone-icon"><circle r="7" fill="var(--accent)" fill-opacity=".18"/><path d="M-4.5-4.5L4.5 4.5M4.5-4.5L-4.5 4.5" stroke="var(--accent)" stroke-width="1.4"/>
        <circle r="2" fill="var(--accent)"/><circle cx="-4.5" cy="-4.5" r="1.8" fill="var(--accent)"/><circle cx="4.5" cy="-4.5" r="1.8" fill="var(--accent)"/><circle cx="-4.5" cy="4.5" r="1.8" fill="var(--accent)"/><circle cx="4.5" cy="4.5" r="1.8" fill="var(--accent)"/>
        <text id="drone-off" class="obj-label" x="9" y="3" fill="var(--bad)"></text></g>
    </svg>
    <div class="map-legend"><span>&#9673; drone (GPS)</span><span><span class="cat cat-person"></span>person</span><span><span class="cat cat-vehicle"></span>vehicle</span>
      <span><span class="cat cat-animal"></span>animal</span><span><span class="cat cat-object"></span>object</span><span><span class="cat cat-infrastructure"></span>gate/door</span>
      <span style="color:var(--bad)">- - perimeter</span><span style="color:var(--warn)">/// restricted</span><span>&middot;&middot;&middot; ${property.geofence_radius_m} m geofence</span></div>
    <div class="map-note">Drone at its GPS fix; objects at the zone the analyzer assigned (captions give a zone, not coordinates).</div>`;
}

function updateMap(cur) {
  const svg = document.getElementById("map");
  if (!svg || !proj) return;
  const drone = svg.querySelector("#drone");
  const t = cur && cur.frame.telemetry;
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
  } else {
    drone.style.opacity = cur ? 0.35 : 1; // no fix this frame: keep the last position, dimmed
  }
  svg.querySelector("#trail").setAttribute("points", replay.trail.map(([la, lo]) => project(la, lo).map((v) => v.toFixed(1)).join(",")).join(" "));
  const byZone = {};
  const objs = (cur ? cur.detections : []).map((d) => {
    const z = proj.zones.find((zz) => zz.id === d.zone);
    if (!z) return "";
    const i = (byZone[d.zone] = (byZone[d.zone] || 0) + 1) - 1;
    const angle = i * 2.1 + 0.6; // spread several objects in one zone around its centre
    const r = Math.min(z.radius_m * 0.45, 12);
    const x = z.xy[0] + Math.cos(angle) * r, y = z.xy[1] + Math.sin(angle) * r;
    return `<g><circle cx="${x}" cy="${y}" r="3.6" fill="var(--cat-${esc(d.category)})" stroke="var(--map-bg)" stroke-width="1.5"><title>${esc(d.display_name)}</title></circle>
      <text class="obj-label" x="${x + 5.5}" y="${y + 2.4}">${esc(d.display_name)}${d.action ? ` · ${esc(d.action)}` : ""}</text></g>`;
  });
  svg.querySelector("#objects").innerHTML = objs.join("");
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

export { play };
