// Search › Frame index: real queries against the SQLite index, with the SQL that actually ran.
import { api, empty, esc, failure, fixed, flink, hhmm, lead, loading, meter, zoneName } from "../lib.js";

export const HELP = `Queries the <b>SQLite frame index</b> directly. Structured filters (object, zone, entity, time) use B-tree indexes on
  (label, ts), (zone, ts) and (entity_id, ts); free text uses <b>FTS5 / BM25</b> plus TF-IDF vectors merged by reciprocal-rank fusion.
  "Show the SQL" lists the statements SQLite executed for this query (captured with <code>set_trace_callback</code>), so nothing here is canned.
  Click any row to open that frame.
  <div class="req">Covers: frame-by-frame indexing and queries like "show all truck events".</div>`;

const PRESETS = [
  ["Show all truck events", { object: "truck" }],
  ["Near the main gate, 00:00–01:00", { zone: "main_gate", start: "00:00", end: "01:00" }],
  ["Blue Ford F150 sightings", { object: "blue Ford F150" }],
  ["Person climbing the fence", { text: "person climbing the fence" }],
  ["People in the warehouse at night", { object: "person", zone: "warehouse", start: "00:00", end: "06:00" }],
  ["Masked + fence, 00:00–03:00", { text: "masked fence", start: "00:00", end: "03:00" }],
];
const FIELDS = ["text", "object", "zone", "entity", "start", "end", "mode"];
const PILLS = { object: "object", zone: "zone", entity: "entity", start: "from", end: "to" };

export async function render(view, { params }) {
  view.innerHTML = lead("Search every indexed frame by words, object, place or time. Results come straight from the SQLite index, together with the SQL that ran.") + `
    <form id="qx">
      <div class="searchbar"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        <input name="text" placeholder="Describe what you are looking for, e.g. someone hanging around the entrance" aria-label="Free-text search" autocomplete="off">
        <button class="btn btn-sm" type="button" id="qx-filters-btn">Filters</button>
        <button class="btn btn-accent" type="submit">Search</button></div>
      <div class="presets"><span class="muted small">Try:</span>${PRESETS.map(([label], i) => `<button type="button" class="chip" data-preset="${i}">${esc(label)}</button>`).join("")}</div>
      <div class="card mb" id="qx-filters" hidden><div class="filters">
        <label class="field">Object (parsed like a caption)<input name="object" placeholder="truck, blue Ford F150, person"></label>
        <label class="field">Zone<select name="zone"><option value="">any</option></select></label>
        <label class="field">Entity<select name="entity"><option value="">any</option></select></label>
        <label class="field narrow">From<input name="start" placeholder="00:00" size="5"></label>
        <label class="field narrow">To<input name="end" placeholder="23:59" size="5"></label>
        <label class="field">Text matching<select name="mode"><option value="hybrid">hybrid (BM25 + vectors)</option><option value="keyword">keyword (BM25)</option><option value="semantic">semantic (TF-IDF)</option></select></label>
        <button class="btn btn-ghost" type="reset">Clear all</button></div></div>
    </form>
    <div class="active-filters mb" id="qx-active"></div>
    <div id="qx-out">${empty("Type a description, pick an example above, or open Filters. Searching with everything empty lists all frames.")}</div>`;

  const form = view.querySelector("#qx");
  try {
    const opts = await api("/api/index/options");
    form.zone.innerHTML += opts.zones.map((z) => `<option value="${esc(z.id)}">${esc(z.name)}</option>`).join("");
    form.entity.innerHTML += opts.entities.map((e) => `<option value="${esc(e.entity_id)}">${esc(e.entity_id)} · ${esc(e.display_name)}</option>`).join("");
  } catch (err) {
    view.querySelector("#qx-out").innerHTML = failure(err, "Could not load the index options");
  }
  const run = () => {
    const values = Object.fromEntries(FIELDS.map((f) => [f, form[f].value.trim()]).filter(([, v]) => v));
    showPills(view, form, values, run);
    query(view, values);
  };
  form.onsubmit = (e) => { e.preventDefault(); run(); };
  form.onreset = () => setTimeout(() => { view.querySelector("#qx-active").innerHTML = ""; view.querySelector("#qx-out").innerHTML = empty("Cleared."); }, 0);
  view.querySelector("#qx-filters-btn").onclick = (e) => {
    const box = view.querySelector("#qx-filters");
    box.hidden = !box.hidden;
    e.target.classList.toggle("on", !box.hidden);
  };
  view.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => {
    FIELDS.forEach((f) => (form[f].value = f === "mode" ? "hybrid" : "")); // not form.reset(): its handler clears the results
    for (const [k, v] of Object.entries(PRESETS[Number(b.dataset.preset)][1])) form[k].value = v;
    view.querySelectorAll("[data-preset]").forEach((c) => c.classList.toggle("on", c === b));
    run();
  });
  // deep link from other views and demo mode: #/search?preset=0
  if (params.has("preset")) view.querySelector(`[data-preset="${Number(params.get("preset"))}"]`)?.click();
}

// Structured filters in effect, as removable pills under the search bar.
function showPills(view, form, values, run) {
  const el = view.querySelector("#qx-active");
  const label = (k, v) => (k === "zone" ? zoneName(v) : v);
  el.innerHTML = Object.entries(values).filter(([k]) => PILLS[k]).map(([k, v]) =>
    `<span class="chip">${PILLS[k]}: <b>${esc(label(k, v))}</b><span class="x" data-clear="${k}" role="button" aria-label="Remove ${PILLS[k]} filter">&#10005;</span></span>`).join("");
  el.onclick = (e) => {
    const x = e.target.closest("[data-clear]");
    if (x) { form[x.dataset.clear].value = ""; run(); }
  };
}

async function query(view, values) {
  const out = view.querySelector("#qx-out");
  out.innerHTML = loading("Querying the index…");
  let res;
  try {
    res = await api(`/api/index/query?${new URLSearchParams(values)}`);
  } catch (err) {
    out.innerHTML = failure(err, "Query failed");
    return;
  }
  const hours = Array(24).fill(0);
  res.rows.forEach((r) => hours[Number(r.ts.slice(11, 13))]++);
  const peak = Math.max(1, ...hours);
  const detail = res.rows.some((r) => r.object);
  const scored = res.rows.some((r) => r.score != null);
  out.innerHTML = `
    <div class="grid mb" style="grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); align-items:start">
      <div class="card"><div class="card-head"><h3>When the hits happened</h3><span class="muted small"><b style="color:var(--text)">${res.count}</b> frames · ${res.elapsed_ms} ms</span></div>
        <div class="hist">${hours.map((n, h) => `<div class="${n ? "" : "zero"}" style="height:${n ? Math.max(8, (n / peak) * 100) : 4}%;animation-delay:${h * 15}ms" title="${String(h).padStart(2, "0")}:00 · ${n} frame${n === 1 ? "" : "s"}"></div>`).join("")}</div>
        <div class="hist-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div></div>
      <div class="card"><div class="card-head"><h3>What ran</h3><span class="muted small">${res.sql.length} SQL statement${res.sql.length === 1 ? "" : "s"}</span></div>
        <ul class="small" style="margin:0;padding-left:18px">${res.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
        <details class="more" style="margin-top:8px"><summary>Show the SQL SQLite executed</summary><pre class="sql">${res.sql.map(esc).join(";\n\n")}</pre></details></div>
    </div>
    ${res.rows.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>Time</th><th>Frame</th><th>Zone</th>${detail ? "<th>Object</th><th>Entity</th><th>Confidence</th>" : ""}${scored ? "<th>Score</th>" : ""}<th>Caption</th></tr></thead><tbody>
      ${res.rows.map((r) => `<tr class="clickable" data-frame="${r.frame_id}"><td class="mono">${hhmm(r.ts)}</td><td>${flink(r.frame_id)}</td><td>${esc(zoneName(r.zone))}</td>
        ${detail ? `<td>${esc(r.object || "")}</td><td>${esc(r.entity_id || "")}</td><td>${meter(r.confidence)}</td>` : ""}
        ${scored ? `<td class="num">${r.score == null ? "" : fixed(r.score, 4)}</td>` : ""}
        <td>${esc(r.description)}</td></tr>`).join("")}</tbody></table></div>`
      : empty("No frames match. Try fewer filters, a wider time window, or another wording.")}`;
}
