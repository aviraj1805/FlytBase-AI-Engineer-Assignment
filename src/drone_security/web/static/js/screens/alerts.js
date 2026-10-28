// Live Alerts: the alerts the rule engine raised in the running replay, or the whole processed day.
import { api, empty, esc, failure, flink, hhmm, loading, pageHead, sev, zoneName, SEVERITIES } from "../lib.js";
import { replay, subscribe, play } from "../replay.js";

const HELP = `A real-time feed of alerts as the replay runs. Each alert carries its <b>severity</b>, <b>rule id</b>, time, zone,
  a plain-English <b>explanation</b> (exactly the rule conditions that held) and <b>evidence frames</b>.<br><br>
  Covers: <b>real-time alerts from predefined rules</b> (config/rules.yaml). Acknowledgements are kept in this browser session only.`;

const acked = new Set(); // key: ts|rule|frame — survives navigation, not a reload
const filters = { severity: new Set(), rule: "", zone: "", showAcked: true, source: "live" };
let dayAlerts = null;

const key = (a) => `${a.ts}|${a.rule_id}|${a.frame_id}`;

export async function render(view) {
  view.innerHTML = pageHead("Live Alerts", HELP, `
    <div class="row">
      <button class="chip" data-source="live">Live replay</button>
      <button class="chip" data-source="day">Whole day (index)</button>
    </div>`) + `<div class="card" style="margin-bottom:14px"><div class="row" id="al-filters"></div></div><div id="al-counts" class="row" style="margin-bottom:12px"></div><div id="al-list" class="stack"></div>`;

  view.querySelectorAll("[data-source]").forEach((b) => b.onclick = async () => { filters.source = b.dataset.source; await draw(view, true); });
  await draw(view, true);
  let lastCount = replay.alerts.length;
  const unsub = subscribe((kind) => {
    if (filters.source === "live" && (kind === "alerts" || kind === "snapshot" || kind === "state")) {
      const fresh = kind === "alerts" ? replay.alerts.length - lastCount : 0;
      lastCount = replay.alerts.length;
      draw(view, false, fresh);
    }
  });
  return unsub;
}

async function source() {
  if (filters.source === "live") return replay.alerts;
  if (!dayAlerts) dayAlerts = (await api("/api/alerts")).alerts;
  return dayAlerts;
}

async function draw(view, rebuildFilters, fresh = 0) {
  view.querySelectorAll("[data-source]").forEach((b) => b.classList.toggle("on", b.dataset.source === filters.source));
  const list = view.querySelector("#al-list");
  let alerts;
  try {
    if (filters.source === "day" && !dayAlerts) list.innerHTML = loading();
    alerts = await source();
  } catch (err) {
    list.innerHTML = failure(err, "Could not load alerts");
    return;
  }
  if (rebuildFilters) buildFilters(view, alerts);

  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
  alerts.forEach((a) => counts[a.severity_name]++);
  view.querySelector("#al-counts").innerHTML = SEVERITIES.filter((s) => counts[s]).map((s) => `${sev(s)} <span class="num">${counts[s]}</span>`).join("&nbsp;&nbsp;") +
    `<span class="muted small" style="margin-left:auto">${alerts.length} alerts${filters.source === "live" && replay.current ? ` · replay at ${hhmm(replay.current.frame.ts)}` : ""} · ${acked.size} acknowledged</span>`;

  const shown = alerts.filter((a) => (!filters.severity.size || filters.severity.has(a.severity_name)) &&
    (!filters.rule || a.rule_id === filters.rule) && (!filters.zone || a.zone === filters.zone) &&
    (filters.showAcked || !acked.has(key(a))));

  if (!alerts.length) {
    list.innerHTML = filters.source === "live"
      ? `<div class="state">No alerts in the live replay yet. <button class="btn btn-accent" id="al-play" style="margin-left:8px">&#9654; Start the replay</button>
         <div class="small" style="margin-top:8px">or switch to <b>Whole day (index)</b> to see every alert stored for the processed day.</div></div>`
      : empty("No alerts in the index.");
    const b = list.querySelector("#al-play");
    if (b) b.onclick = () => { play(); };
    return;
  }
  if (!shown.length) { list.innerHTML = empty("No alerts match these filters."); return; }

  // newest first, like a live feed
  list.innerHTML = shown.slice().reverse().map((a, i) => `
    <div class="alert-item ${acked.has(key(a)) ? "acked" : ""} ${i < fresh ? "fresh" : ""}" data-key="${esc(key(a))}">
      <div class="stripe stripe-${a.severity_name}"></div>
      <div>
        <div class="row">${sev(a.severity_name)}<code>${esc(a.rule_id)}</code><span class="muted mono">${hhmm(a.ts)}</span><span class="chip">${esc(zoneName(a.zone))}</span>
          ${a.entity_id ? `<a class="chip" href="#/entities?id=${esc(a.entity_id)}">${esc(a.entity_id)}</a>` : ""}
          ${a.related_rules && a.related_rules.length ? `<span class="chip" title="Rules folded into this alert by correlation">+ ${a.related_rules.map(esc).join(", ")}</span>` : ""}</div>
        <div class="msg" style="margin-top:5px">${esc(a.message)}</div>
        <div class="why"><b>Why:</b> ${esc(a.explanation)}</div>
        ${a.recommended_action ? `<div class="act"><b>Action:</b> ${esc(a.recommended_action)}</div>` : ""}
        <div class="row small" style="margin-top:6px"><span class="muted">Evidence frames:</span> ${a.evidence_frame_ids.map(flink).join(" ")}</div>
      </div>
      <div><button class="btn btn-sm ack">${acked.has(key(a)) ? "Acknowledged &#10003;" : "Acknowledge"}</button></div>
    </div>`).join("");
  list.querySelectorAll(".ack").forEach((b) => b.onclick = () => {
    const k = b.closest(".alert-item").dataset.key;
    acked.has(k) ? acked.delete(k) : acked.add(k);
    draw(view, false);
  });
}

function buildFilters(view, alerts) {
  const rules = [...new Set(alerts.map((a) => a.rule_id))].sort();
  const zones = [...new Set(alerts.map((a) => a.zone).filter(Boolean))].sort();
  const el = view.querySelector("#al-filters");
  el.innerHTML = `<span class="muted small">Severity</span>
    ${SEVERITIES.slice(0, 4).map((s) => `<button class="chip ${filters.severity.has(s) ? "on" : ""}" data-sev="${s}">${s}</button>`).join("")}
    <select id="f-rule" aria-label="Rule"><option value="">All rules</option>${rules.map((r) => `<option ${r === filters.rule ? "selected" : ""}>${esc(r)}</option>`).join("")}</select>
    <select id="f-zone" aria-label="Zone"><option value="">All zones</option>${zones.map((z) => `<option value="${esc(z)}" ${z === filters.zone ? "selected" : ""}>${esc(zoneName(z))}</option>`).join("")}</select>
    <label class="row small muted"><input type="checkbox" id="f-acked" ${filters.showAcked ? "checked" : ""}> show acknowledged</label>`;
  el.querySelectorAll("[data-sev]").forEach((b) => b.onclick = () => {
    filters.severity.has(b.dataset.sev) ? filters.severity.delete(b.dataset.sev) : filters.severity.add(b.dataset.sev);
    b.classList.toggle("on");
    draw(view, false);
  });
  el.querySelector("#f-rule").onchange = (e) => { filters.rule = e.target.value; draw(view, false); };
  el.querySelector("#f-zone").onchange = (e) => { filters.zone = e.target.value; draw(view, false); };
  el.querySelector("#f-acked").onchange = (e) => { filters.showAcked = e.target.checked; draw(view, false); };
}
