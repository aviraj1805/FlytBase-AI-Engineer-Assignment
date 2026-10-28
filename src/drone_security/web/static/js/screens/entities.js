// Insights › Identities: every re-identified entity, its visits over the day, and why the tracker matched it.
import { api, countUp, empty, esc, failure, fixed, flink, hhmm, lead, loading, meter, num, sev, zoneName } from "../lib.js";

export const HELP = `The tracker re-identifies objects across frames from their appearance attributes (colour, make, model for vehicles;
  colour, clothing, mask for people). An attribute both sides know adds its weight when it agrees and subtracts twice its weight when it
  conflicts; recent sightings add a continuity bonus. Match confidence = best score − ½ runner-up, so ambiguous matches show up as low confidence.
  <div class="req">Covers: tracking and logging objects across the day, e.g. "the blue Ford F150 entered twice".</div>`;

const CATS = ["vehicle", "person", "animal", "object", "infrastructure"];
let selectedCat = "";

export async function render(view, { params }) {
  view.innerHTML = lead("Everyone and everything the tracker recognised across the day: when each one came and went, and why the system believes it is the same one.") + `
    <div class="row mb" id="cats"></div>
    <div class="grid" style="grid-template-columns: minmax(280px, 0.8fr) minmax(0, 1.6fr); align-items:start">
      <div id="cards" class="entity-list">${loading()}</div>
      <div id="detail" class="card">${empty("Select an entity to see its timeline and why it was matched.")}</div>
    </div>`;
  let entities;
  try {
    entities = (await api("/api/entities")).entities;
  } catch (err) {
    view.querySelector("#cards").innerHTML = failure(err, "Could not load entities");
    return;
  }
  const cats = view.querySelector("#cats");
  const counts = Object.fromEntries(CATS.map((c) => [c, entities.filter((e) => e.category === c).length]));
  cats.innerHTML = `<button class="chip ${!selectedCat ? "on" : ""}" data-cat="">all ${entities.length}</button>` +
    CATS.filter((c) => counts[c]).map((c) => `<button class="chip ${selectedCat === c ? "on" : ""}" data-cat="${c}"><span class="cat cat-${c}"></span>${c} ${counts[c]}</button>`).join("");
  const cards = view.querySelector("#cards");
  let current = params.get("id") || (entities.find((e) => e.visit_count > 1 && e.category === "vehicle") || entities[0] || {}).entity_id;
  const drawCards = () => {
    const shown = entities.filter((e) => !selectedCat || e.category === selectedCat);
    cards.innerHTML = shown.map((e) => `
      <div class="card entity-card ${e.entity_id === current ? "selected" : ""}" data-id="${esc(e.entity_id)}" tabindex="0" role="button" aria-label="${esc(e.display_name)}">
        <div class="row"><span class="cat cat-${esc(e.category)}"></span><span class="name">${esc(e.display_name)}</span><span class="muted mono small" style="margin-left:auto">${esc(e.entity_id)}</span></div>
        ${dayBar(e.visits)}
        <div class="row small muted"><span>${e.visit_count} visit${e.visit_count !== 1 ? "s" : ""}</span><span>${e.sightings} sightings</span>
          ${e.alerts ? `<span class="bad-text">${e.alerts} alert${e.alerts > 1 ? "s" : ""}</span>` : ""}</div>
      </div>`).join("") || empty("No entities in this category.");
  };
  drawCards();
  cats.onclick = (e) => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    selectedCat = b.dataset.cat;
    cats.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
    drawCards();
  };
  const pick = (id) => {
    current = id;
    cards.querySelectorAll(".entity-card").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
    showDetail(view.querySelector("#detail"), id);
  };
  cards.onclick = (e) => { const c = e.target.closest(".entity-card"); if (c) pick(c.dataset.id); };
  cards.onkeydown = (e) => { const c = e.target.closest(".entity-card"); if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(c.dataset.id); } };
  if (current) pick(current);
}

// 24-hour bar with one segment per visit.
function dayBar(visits, big = false) {
  const pos = (ts) => ((Number(ts.slice(11, 13)) * 60 + Number(ts.slice(14, 16))) / 1440) * 100;
  return `<div class="daybar ${big ? "big" : ""}" title="Visits over the day (00:00–24:00)">${[6, 12, 18].map((h) => `<span class="tick" style="left:${(h / 24) * 100}%"></span>`).join("")}
    ${visits.map((v) => `<span class="seg-v" style="left:${pos(v.start)}%;width:${Math.max(pos(v.end) - pos(v.start), 0.4)}%" title="${hhmm(v.start)}–${hhmm(v.end)} ${esc(v.zones.map(zoneName).join(", "))}"></span>`).join("")}</div>`;
}

async function showDetail(el, id) {
  el.innerHTML = loading();
  let d;
  try {
    d = await api(`/api/entities/${encodeURIComponent(id)}`);
  } catch (err) {
    el.innerHTML = failure(err, `Could not load ${id}`);
    return;
  }
  const e = d.entity, m = d.match;
  const why = m.matched_on.length
    ? `<div class="row" style="margin:8px 0">${m.matched_on.map((t) => `<span class="trait">${esc(t.trait)} <b>${esc(t.value)}</b> <span class="muted small">weight ${t.weight}</span></span>`).join("")}</div>
       <p class="small muted" style="margin:8px 0 0">The first sighting created ${esc(e.entity_id)}; later sightings were matched to it because these identity traits agreed
       (score ≥ ${m.accept_score} needed). Lowest match confidence ${fixed(m.min_confidence, 2)}${m.low_confidence_sightings
         ? `, <span class="warn-text">${m.low_confidence_sightings} sighting(s) below 0.70 (another similar entity was close)</span>` : ""}.</p>`
    : `<p class="small muted">${esc(e.category)}s are matched by ${e.category === "animal" ? "label while recently seen" : "label in the same zone"} (no identity attributes).</p>`;
  el.innerHTML = `
    <div class="row"><span class="cat cat-${esc(e.category)}"></span><h2>${esc(e.display_name)}</h2><span class="muted mono">${esc(e.entity_id)}</span></div>
    <div class="small muted" style="margin-top:2px">first seen ${hhmm(e.first_seen)} · last seen ${hhmm(e.last_seen)} at ${esc(zoneName(e.last_zone))}${
      d.known_vehicle ? ` · <span class="ok-text">known vehicle: ${esc(d.known_vehicle)}</span>` : ""}</div>
    <div class="stat-row">
      <div class="stat"><div class="v">${num(e.visit_count)}</div><div class="l">visits</div></div>
      <div class="stat"><div class="v">${num(d.sightings.length)}</div><div class="l">sightings</div></div>
      <div class="stat"><div class="v">${num(m.mean_confidence, 2)}</div><div class="l">mean match confidence</div></div>
      <div class="stat"><div class="v ${d.alerts.length ? "bad-text" : ""}">${num(d.alerts.length)}</div><div class="l">alerts</div></div>
    </div>
    <h3>Why the tracker says it is the same one</h3>${why}
    <h3 style="margin-top:18px">Visits</h3>${dayBar(e.visits, true)}
    ${e.visits.map((v, i) => `<div class="visit"><span class="no">${i + 1}</span><div style="flex:1"><b class="mono">${hhmm(v.start)}–${hhmm(v.end)}</b> · ${esc(v.zones.map(zoneName).join(" → "))}
      ${v.left ? '<span class="chip">left</span>' : '<span class="chip">no exit seen</span>'}
      <div class="small muted" style="margin-top:3px">frames ${v.frame_ids.slice(0, 8).map(flink).join(" ")}${v.frame_ids.length > 8 ? ` … ${flink(v.frame_ids[v.frame_ids.length - 1])}` : ""}</div></div></div>`).join("")}
    ${d.alerts.length ? `<h3 style="margin-top:18px">Alerts</h3>${d.alerts.map((a) => `<div class="det">${sev(a.severity_name)}<div>${esc(a.message)} ${flink(a.frame_id)}<div class="meta">${esc(a.explanation)}</div></div></div>`).join("")}` : ""}
    <details class="more" style="margin-top:16px"><summary>Every sighting (${d.sightings.length})</summary>
      <div class="table-wrap" style="max-height:320px;margin-top:8px"><table class="tbl"><thead><tr><th>Time</th><th>Frame</th><th>Zone</th><th>Action</th><th>Re-ID</th><th>Caption</th></tr></thead><tbody>
      ${d.sightings.map((s) => `<tr class="clickable" data-frame="${s.frame_id}"><td class="mono">${hhmm(s.ts)}</td><td>${flink(s.frame_id)}</td><td>${esc(zoneName(s.zone))}</td>
        <td>${esc(s.action || "")}</td><td>${meter(s.track_confidence)}</td><td>${esc(s.description)}</td></tr>`).join("")}</tbody></table></div></details>`;
  countUp(el);
}
