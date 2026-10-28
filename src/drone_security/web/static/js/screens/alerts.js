// Live › Alerts: the alerts the rule engine raised in the running replay, or for the whole processed day, newest first.
import { api, empty, esc, failure, flink, hhmm, lead, loading, sev, zoneName, SEVERITIES } from "../lib.js";
import { replay, subscribe, play } from "../replay.js";

export const HELP = `A real-time feed of alerts as the replay runs (or every alert stored for the processed day). Each alert carries its
  <b>severity</b>, <b>rule id</b>, time, zone, a plain-English <b>explanation</b> (exactly the rule conditions that held), a recommended action
  and its <b>evidence frames</b>. Cooldown and correlation fold repeated firings into one alert per incident step.
  Acknowledgements are kept in this browser session only.
  <div class="req">Covers: real-time alerts from predefined rules (config/rules.yaml).</div>`;

const acked = new Set();  // key: ts|rule|frame; survives navigation, not a reload
const opened = new Set(); // expanded alerts
const filters = { severity: new Set(), rule: "", zone: "", showAcked: true, source: "live" };
let dayAlerts = null;

const key = (a) => `${a.ts}|${a.rule_id}|${a.frame_id}`;

export async function render(view) {
  view.innerHTML = lead("Every alert the rule engine raised, newest first. Click an alert to see why it fired, what to do, and the frames that prove it.",
    '<div class="seg" id="al-source"><button data-source="live">Live replay</button><button data-source="day">Whole day</button></div>') + `
    <div class="row mb"><div class="sev-filter" id="al-sev"></div><span class="spacer"></span><button class="btn btn-sm" id="al-more-btn">Filters</button></div>
    <div class="card mb" id="al-more" hidden></div>
    <div class="row small muted mb" id="al-summary"></div>
    <div id="al-list" class="stack" style="gap:10px"></div>`;

  view.querySelector("#al-source").onclick = async (e) => {
    const b = e.target.closest("[data-source]");
    if (b) { filters.source = b.dataset.source; await draw(view, { rebuild: true }); }
  };
  view.querySelector("#al-sev").onclick = (e) => {
    const b = e.target.closest("[data-sev]");
    if (!b) return;
    filters.severity.has(b.dataset.sev) ? filters.severity.delete(b.dataset.sev) : filters.severity.add(b.dataset.sev);
    draw(view);
  };
  view.querySelector("#al-more-btn").onclick = (e) => {
    const box = view.querySelector("#al-more");
    box.hidden = !box.hidden;
    e.target.classList.toggle("on", !box.hidden);
  };
  view.querySelector("#al-list").onclick = (e) => {
    const item = e.target.closest(".alert-item");
    if (!item) return;
    if (e.target.closest(".ack")) {
      acked.has(item.dataset.key) ? acked.delete(item.dataset.key) : acked.add(item.dataset.key);
      draw(view);
    } else if (!e.target.closest("a, button")) {
      opened.has(item.dataset.key) ? opened.delete(item.dataset.key) : opened.add(item.dataset.key);
      item.classList.toggle("open");
    }
  };
  await draw(view, { rebuild: true });
  let lastCount = replay.alerts.length;
  return subscribe((kind) => {
    if (filters.source === "live" && (kind === "alerts" || kind === "snapshot" || kind === "state")) {
      const fresh = kind === "alerts" ? replay.alerts.length - lastCount : 0;
      lastCount = replay.alerts.length;
      draw(view, { fresh });
    }
  });
}

async function source() {
  if (filters.source === "live") return replay.alerts;
  if (!dayAlerts) dayAlerts = (await api("/api/alerts")).alerts;
  return dayAlerts;
}

async function draw(view, { rebuild = false, fresh = 0 } = {}) {
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
  if (rebuild) buildMoreFilters(view, alerts);

  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
  alerts.forEach((a) => counts[a.severity_name]++);
  view.querySelector("#al-sev").innerHTML = SEVERITIES.filter((s) => counts[s]).map((s) =>
    `<button data-sev="${s}" class="${filters.severity.has(s) ? "on" : ""}" title="Show only ${s} (click again to clear)"><span class="dot dot-${s}"></span>${s}<span class="n">${counts[s]}</span></button>`).join("");
  view.querySelector("#al-summary").innerHTML = `${alerts.length} alert${alerts.length === 1 ? "" : "s"}${
    filters.source === "live" && replay.current ? ` · replay at ${hhmm(replay.current.frame.ts)}` : ""} · ${acked.size} acknowledged`;

  const shown = alerts.filter((a) => (!filters.severity.size || filters.severity.has(a.severity_name)) &&
    (!filters.rule || a.rule_id === filters.rule) && (!filters.zone || a.zone === filters.zone) &&
    (filters.showAcked || !acked.has(key(a))));

  if (!alerts.length) {
    list.innerHTML = filters.source === "live"
      ? `<div class="state">No alerts in the live replay yet.<div style="margin-top:12px"><button class="btn btn-accent" id="al-play">&#9654; Start the replay</button></div>
         <div class="small" style="margin-top:10px">or switch to <b>Whole day</b> to see every alert stored for the processed day.</div></div>`
      : empty("No alerts in the index.");
    const b = list.querySelector("#al-play");
    if (b) b.onclick = () => play();
    return;
  }
  if (!shown.length) { list.innerHTML = empty("No alerts match these filters."); return; }

  list.innerHTML = shown.slice().reverse().map((a, i) => `
    <div class="alert-item ${acked.has(key(a)) ? "acked" : ""} ${opened.has(key(a)) ? "open" : ""} ${i < fresh ? "fresh" : ""}" data-key="${esc(key(a))}">
      <div class="stripe stripe-${a.severity_name}"></div>
      <div>
        <div class="row">${sev(a.severity_name)}<span class="msg">${esc(a.message)}</span></div>
        <div class="sub"><code>${esc(a.rule_id)}</code><span>${esc(zoneName(a.zone))}</span>
          ${a.entity_id ? `<a href="#/insights/identities?id=${esc(a.entity_id)}">${esc(a.entity_id)}</a>` : ""}
          ${a.related_rules && a.related_rules.length ? `<span title="Rules folded into this alert by correlation">+ ${a.related_rules.map(esc).join(", ")}</span>` : ""}</div>
        <div class="why"><b>Why:</b> ${esc(a.explanation)}</div>
        <div class="detail">
          ${a.recommended_action ? `<div><b>Action:</b> ${esc(a.recommended_action)}</div>` : ""}
          <div class="row small"><span class="muted">Evidence frames:</span> ${a.evidence_frame_ids.map(flink).join(" ")}</div>
        </div>
      </div>
      <div class="side"><span class="t">${hhmm(a.ts)}</span><button class="btn btn-sm ack">${acked.has(key(a)) ? "&#10003; Done" : "Acknowledge"}</button></div>
    </div>`).join("");
}

function buildMoreFilters(view, alerts) {
  const rules = [...new Set(alerts.map((a) => a.rule_id))].sort();
  const zones = [...new Set(alerts.map((a) => a.zone).filter(Boolean))].sort();
  const el = view.querySelector("#al-more");
  el.innerHTML = `<div class="row" style="gap:12px">
    <label class="field">Rule<select id="f-rule"><option value="">All rules</option>${rules.map((r) => `<option ${r === filters.rule ? "selected" : ""}>${esc(r)}</option>`).join("")}</select></label>
    <label class="field">Zone<select id="f-zone"><option value="">All zones</option>${zones.map((z) => `<option value="${esc(z)}" ${z === filters.zone ? "selected" : ""}>${esc(zoneName(z))}</option>`).join("")}</select></label>
    <label class="row small muted" style="align-self:flex-end;padding-bottom:8px"><input type="checkbox" id="f-acked" ${filters.showAcked ? "checked" : ""}> show acknowledged</label></div>`;
  el.querySelector("#f-rule").onchange = (e) => { filters.rule = e.target.value; draw(view); };
  el.querySelector("#f-zone").onchange = (e) => { filters.zone = e.target.value; draw(view); };
  el.querySelector("#f-acked").onchange = (e) => { filters.showAcked = e.target.checked; draw(view); };
}
