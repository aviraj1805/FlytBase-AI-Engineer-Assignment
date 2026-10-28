// Search › Event log: every logged detection ("Blue Ford F150 spotted at garage, 12:00.") from the index.
import { api, empty, esc, failure, flink, hhmm, lead, loading, meter, zoneName } from "../lib.js";

export const HELP = `The event log the pipeline writes while processing: one line per <b>notable change</b> (a new visit, a zone change, an action
  such as entering or climbing), not one per frame. Confidence is the analyzer's perception confidence; re-ID is how sure the tracker was that
  this is the same entity it saw before (amber below 0.70). Click a row to open the exact frame.
  <div class="req">Covers: identify and log objects / events ("Blue Ford F150 spotted at garage, 12:00").</div>`;

const COLUMNS = [["ts", "Time"], ["message", "Event"], ["entity_id", "Entity"], ["zone", "Zone"], ["kind", "Kind"],
  ["confidence", "Confidence"], ["track_confidence", "Re-ID"]];
const CATS = ["person", "vehicle", "animal", "object", "infrastructure"];
let sortKey = "ts", sortDir = 1;

export async function render(view) {
  view.innerHTML = lead("The log the system writes as it watches: one line each time something new arrives, moves zone or does something.") + `
    <div class="searchbar mb"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      <input id="ev-q" type="search" placeholder="Filter the log, e.g. F150, garage, climbing…" aria-label="Filter events"></div>
    <div class="row mb">
      <div class="seg" id="ev-kind"><button data-kind="" class="on">All</button><button data-kind="visit">New arrivals</button><button data-kind="sighting">Changes</button></div>
      <div class="row" id="ev-cat">${CATS.map((c) => `<button class="chip" data-cat="${c}"><span class="cat cat-${c}"></span>${c}</button>`).join("")}</div>
      <span class="spacer"></span><span class="muted small" id="ev-count"></span></div>
    <div id="ev-table">${loading()}</div>`;
  let rows;
  try {
    rows = (await api("/api/events")).events;
  } catch (err) {
    view.querySelector("#ev-table").innerHTML = failure(err, "Could not load the event log");
    return;
  }
  let kind = "", category = "";
  const draw = () => {
    const q = view.querySelector("#ev-q").value.trim().toLowerCase();
    const shown = rows.filter((r) => (!q || `${r.message} ${r.entity_id} ${r.zone} ${r.display_name} f${r.frame_id}`.toLowerCase().includes(q)) &&
      (!kind || r.kind === kind) && (!category || r.category === category))
      .sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sortDir);
    view.querySelector("#ev-count").textContent = `${shown.length} of ${rows.length} events`;
    const table = view.querySelector("#ev-table");
    if (!shown.length) { table.innerHTML = empty(rows.length ? "No events match." : "The event log is empty."); return; }
    table.innerHTML = `<div class="table-wrap"><table class="tbl"><thead><tr>${COLUMNS.map(([k, l]) =>
      `<th class="sortable" data-k="${k}">${l}${sortKey === k ? (sortDir > 0 ? " &#9650;" : " &#9660;") : ""}</th>`).join("")}</tr></thead><tbody>
      ${shown.map((r) => `<tr class="clickable" data-frame="${r.frame_id}">
        <td class="mono">${hhmm(r.ts)}</td>
        <td>${r.category ? `<span class="cat cat-${esc(r.category)}"></span>` : ""}${esc(r.message)} ${flink(r.frame_id)}</td>
        <td>${r.entity_id ? `<a href="#/insights/identities?id=${esc(r.entity_id)}" onclick="event.stopPropagation()">${esc(r.entity_id)}</a>` : "–"}</td>
        <td>${esc(zoneName(r.zone))}</td><td class="muted">${r.kind === "visit" ? "arrival" : "change"}</td>
        <td>${meter(r.confidence)}</td><td>${meter(r.track_confidence)}</td></tr>`).join("")}</tbody></table></div>`;
    table.querySelectorAll("th.sortable").forEach((th) => th.onclick = () => {
      sortDir = sortKey === th.dataset.k ? -sortDir : 1;
      sortKey = th.dataset.k;
      draw();
    });
  };
  view.querySelector("#ev-q").addEventListener("input", draw);
  view.querySelector("#ev-kind").onclick = (e) => {
    const b = e.target.closest("[data-kind]");
    if (!b) return;
    kind = b.dataset.kind;
    view.querySelectorAll("#ev-kind button").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  };
  view.querySelector("#ev-cat").onclick = (e) => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    category = category === b.dataset.cat ? "" : b.dataset.cat;
    view.querySelectorAll("#ev-cat .chip").forEach((x) => x.classList.toggle("on", x.dataset.cat === category));
    draw();
  };
  draw();
}
