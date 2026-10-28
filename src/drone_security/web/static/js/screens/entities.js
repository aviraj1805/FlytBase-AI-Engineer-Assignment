// Entity Tracker: every re-identified identity, its visits over the day, and why the tracker matched it.
import { api, empty, esc, failure, fixed, flink, hhmm, loading, pageHead, sev, zoneName } from "../lib.js";

const HELP = `The tracker re-identifies objects across frames from their appearance attributes (colour, make, model for vehicles;
  colour, clothing, mask for people). An attribute both sides know adds its weight when it agrees and subtracts twice its weight when it
  conflicts; recent sightings add a continuity bonus. Match confidence = best score − ½ runner-up, so ambiguous matches show up as low confidence.<br><br>
  Covers: <b>tracking and logging objects</b> across the day, e.g. "the blue Ford F150 entered twice".`;

const CATS = ["vehicle", "person", "animal", "object", "infrastructure"];
let selectedCat = "";

export async function render(view, { params }) {
  view.innerHTML = pageHead("Entity Tracker", HELP) + `
    <div class="row" style="margin-bottom:12px" id="cats"></div>
    <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); align-items:start">
      <div id="cards" class="grid cols-2">${loading()}</div>
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
  cats.innerHTML = `<button class="chip ${!selectedCat ? "on" : ""}" data-cat="">all (${entities.length})</button>` +
    CATS.filter((c) => counts[c]).map((c) => `<button class="chip ${selectedCat === c ? "on" : ""}" data-cat="${c}"><span class="cat cat-${c}"></span>${c} (${counts[c]})</button>`).join("");
  const drawCards = () => {
    const shown = entities.filter((e) => !selectedCat || e.category === selectedCat);
    view.querySelector("#cards").innerHTML = shown.map((e) => `
      <div class="card entity-card" data-id="${esc(e.entity_id)}" tabindex="0" role="button" aria-label="${esc(e.display_name)}">
        <div class="row"><span class="cat cat-${esc(e.category)}"></span><span class="name">${esc(e.display_name)}</span><span class="muted mono small" style="margin-left:auto">${esc(e.entity_id)}</span></div>
        ${dayBar(e.visits)}
        <div class="row small muted"><span><b class="num" style="color:var(--text)">${e.visit_count}</b> visit${e.visit_count !== 1 ? "s" : ""}</span>
          <span><b class="num" style="color:var(--text)">${e.sightings}</b> sightings</span><span>re-ID ${fixed(e.mean_confidence, 2)}</span>
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
  const cards = view.querySelector("#cards");
  const pick = (id) => {
    cards.querySelectorAll(".entity-card").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
    showDetail(view.querySelector("#detail"), id);
  };
  cards.onclick = (e) => { const c = e.target.closest(".entity-card"); if (c) pick(c.dataset.id); };
  cards.onkeydown = (e) => { const c = e.target.closest(".entity-card"); if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(c.dataset.id); } };
  pick(params.get("id") || (entities.find((e) => e.visit_count > 1 && e.category === "vehicle") || entities[0] || {}).entity_id);
}

// 24-hour bar with one segment per visit.
function dayBar(visits) {
  const pos = (ts) => ((Number(ts.slice(11, 13)) * 60 + Number(ts.slice(14, 16))) / 1440) * 100;
  return `<div class="daybar" title="Visits over the day (00:00–24:00)">${[6, 12, 18].map((h) => `<span class="tick" style="left:${(h / 24) * 100}%"></span>`).join("")}
    ${visits.map((v) => `<span class="seg" style="left:${pos(v.start)}%;width:${Math.max(pos(v.end) - pos(v.start), 0.4)}%" title="${hhmm(v.start)}–${hhmm(v.end)} ${esc(v.zones.map(zoneName).join(", "))}"></span>`).join("")}</div>`;
}

async function showDetail(el, id) {
  if (!id) return;
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
    ? `<div class="row" style="margin:6px 0">${m.matched_on.map((t) => `<span class="trait">${esc(t.trait)} <b>${esc(t.value)}</b> <span class="muted">weight ${t.weight}</span></span>`).join("")}</div>
       <p class="small muted" style="margin:6px 0">The first sighting created ${esc(e.entity_id)}; later sightings were matched to it because these identity traits agreed (score ≥ ${m.accept_score} needed). The first sighting's confidence is how sure the tracker was that it was someone new.
       Mean match confidence <b>${fixed(m.mean_confidence, 2)}</b>, lowest ${fixed(m.min_confidence, 2)}${m.low_confidence_sightings ? `, <span class="warn-text">${m.low_confidence_sightings} sighting(s) below 0.70 (ambiguous: another similar entity was close)</span>` : ""}.</p>`
    : `<p class="small muted">${esc(e.category)}s are matched by ${e.category === "animal" ? "label while recently seen" : "label in the same zone"} (no identity attributes).</p>`;
  el.innerHTML = `
    <div class="card-head"><div><div class="row"><span class="cat cat-${esc(e.category)}"></span><h2>${esc(e.display_name)}</h2><span class="muted mono">${esc(e.entity_id)}</span></div>
      <div class="small muted">first seen ${hhmm(e.first_seen)} · last seen ${hhmm(e.last_seen)} at ${esc(zoneName(e.last_zone))}${d.known_vehicle ? ` · <span class="ok-text">known vehicle: ${esc(d.known_vehicle)}</span>` : ""}</div></div></div>
    <h3>Why it was matched</h3>${why}
    <h3 style="margin-top:14px">Visits (${e.visit_count})</h3>${dayBar(e.visits)}
    ${e.visits.map((v, i) => `<div class="det"><b class="num">#${i + 1}</b><div>${hhmm(v.start)}–${hhmm(v.end)} · ${esc(v.zones.map(zoneName).join(" → "))}
      ${v.left ? '<span class="chip">left</span>' : '<span class="chip">no exit seen</span>'}<div class="meta">frames ${v.frame_ids.slice(0, 8).map(flink).join(" ")}${v.frame_ids.length > 8 ? ` … ${flink(v.frame_ids[v.frame_ids.length - 1])}` : ""}</div></div></div>`).join("")}
    ${d.alerts.length ? `<h3 style="margin-top:14px">Alerts (${d.alerts.length})</h3>${d.alerts.map((a) => `<div class="det">${sev(a.severity_name)}<div>${esc(a.message)} ${flink(a.frame_id)}<div class="meta">${esc(a.explanation)}</div></div></div>`).join("")}` : ""}
    <h3 style="margin-top:14px">Every sighting (${d.sightings.length})</h3>
    <div class="table-wrap" style="max-height:300px"><table class="tbl"><thead><tr><th>Time</th><th>Frame</th><th>Zone</th><th>Action</th><th>Re-ID conf.</th><th>Caption</th></tr></thead><tbody>
      ${d.sightings.map((s) => `<tr class="clickable" data-frame="${s.frame_id}"><td class="mono">${hhmm(s.ts)}</td><td>${flink(s.frame_id)}</td><td>${esc(zoneName(s.zone))}</td>
        <td>${esc(s.action || "")}</td><td class="num ${s.track_confidence < 0.7 ? "warn-text" : ""}">${fixed(s.track_confidence, 2)}</td><td>${esc(s.description)}</td></tr>`).join("")}</tbody></table></div>`;
}
