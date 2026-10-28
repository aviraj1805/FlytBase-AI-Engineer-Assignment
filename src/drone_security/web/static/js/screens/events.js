// Event Log: every logged detection ("Blue Ford F150 spotted at garage, 12:00.") from the index.
import { api, empty, esc, failure, fixed, flink, hhmm, loading, pageHead, zoneName } from "../lib.js";

const HELP = `The event log the pipeline writes while processing: one line per <b>notable change</b> (a new visit, a zone change, an action
  such as entering or climbing), not one per frame. Confidence is the analyzer's perception confidence; re-ID is the tracker's.<br><br>
  Covers: <b>identify and log objects/events</b> ("Blue Ford F150 spotted at garage, 12:00"). Click a row to open the exact frame.`;

const COLUMNS = [["ts", "Time"], ["frame_id", "Frame"], ["message", "Event"], ["entity_id", "Entity"], ["zone", "Zone"],
  ["kind", "Kind"], ["confidence", "Confidence"], ["track_confidence", "Re-ID"]];
let sortKey = "ts", sortDir = 1;

export async function render(view) {
  view.innerHTML = pageHead("Event Log", HELP) + `
    <div class="card" style="margin-bottom:12px"><div class="row">
      <input id="ev-q" type="search" placeholder="Search events, e.g. F150, garage, climbing…" style="flex:1;min-width:240px" aria-label="Search events">
      <select id="ev-kind" aria-label="Kind"><option value="">All kinds</option><option value="visit">visit (new arrival)</option><option value="sighting">sighting (change)</option></select>
      <select id="ev-cat" aria-label="Category"><option value="">All categories</option>${["person", "vehicle", "animal", "object", "infrastructure"].map((c) => `<option>${c}</option>`).join("")}</select>
      <span class="muted small" id="ev-count"></span></div></div>
    <div id="ev-table">${loading()}</div>`;
  let rows;
  try {
    rows = (await api("/api/events")).events;
  } catch (err) {
    view.querySelector("#ev-table").innerHTML = failure(err, "Could not load the event log");
    return;
  }
  const draw = () => {
    const q = view.querySelector("#ev-q").value.trim().toLowerCase();
    const kind = view.querySelector("#ev-kind").value;
    const category = view.querySelector("#ev-cat").value;
    const shown = rows.filter((r) => (!q || `${r.message} ${r.entity_id} ${r.zone} ${r.display_name} f${r.frame_id}`.toLowerCase().includes(q)) &&
      (!kind || r.kind === kind) && (!category || r.category === category))
      .sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sortDir);
    view.querySelector("#ev-count").textContent = `${shown.length} of ${rows.length} events`;
    const table = view.querySelector("#ev-table");
    if (!shown.length) { table.innerHTML = empty(rows.length ? "No events match the search." : "The event log is empty."); return; }
    table.innerHTML = `<div class="table-wrap"><table class="tbl"><thead><tr>${COLUMNS.map(([k, l]) =>
      `<th class="sortable" data-k="${k}">${l}${sortKey === k ? (sortDir > 0 ? " &#9650;" : " &#9660;") : ""}</th>`).join("")}</tr></thead><tbody>
      ${shown.map((r) => `<tr class="clickable" data-frame="${r.frame_id}">
        <td class="mono">${hhmm(r.ts)}</td><td>${flink(r.frame_id)}</td>
        <td>${r.category ? `<span class="cat cat-${esc(r.category)}"></span>` : ""}${esc(r.message)}</td>
        <td>${r.entity_id ? `<a href="#/entities?id=${esc(r.entity_id)}" onclick="event.stopPropagation()">${esc(r.entity_id)}</a>` : "–"}</td>
        <td>${esc(zoneName(r.zone))}</td><td>${esc(r.kind)}</td>
        <td class="num">${fixed(r.confidence, 2)}</td><td class="num">${fixed(r.track_confidence, 2)}</td></tr>`).join("")}</tbody></table></div>`;
    table.querySelectorAll("th.sortable").forEach((th) => th.onclick = () => {
      sortDir = sortKey === th.dataset.k ? -sortDir : 1;
      sortKey = th.dataset.k;
      draw();
    });
  };
  ["#ev-q", "#ev-kind", "#ev-cat"].forEach((s) => view.querySelector(s).addEventListener("input", draw));
  draw();
}
