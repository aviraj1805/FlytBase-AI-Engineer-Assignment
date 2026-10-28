// Frame Index Explorer: real queries against the SQLite index, with the SQL that actually ran.
import { api, chart, cssVar, empty, esc, failure, fixed, flink, hhmm, loading, pageHead, zoneName } from "../lib.js";

const HELP = `Queries the <b>SQLite frame index</b> directly: structured filters use B-tree indexes on (label, ts), (zone, ts), (entity_id, ts);
  free text uses <b>FTS5/BM25</b> plus TF-IDF vectors merged by reciprocal-rank fusion. The SQL panel shows the statements SQLite executed
  for this query (captured with <code>set_trace_callback</code>), so nothing here is canned.<br><br>
  Covers: <b>frame-by-frame indexing</b> and queries like "show all truck events".`;

export const PRESETS = [
  ["Show all truck events", { object: "truck" }],
  ["Near the main gate, 00:00–01:00", { zone: "main_gate", start: "00:00", end: "01:00" }],
  ["Blue Ford F150 sightings", { object: "blue Ford F150" }],
  ["Person climbing the fence (free text)", { text: "person climbing the fence" }],
  ["People in the warehouse at night", { object: "person", zone: "warehouse", start: "00:00", end: "06:00" }],
  ["Masked + fence, 00:00–03:00", { text: "masked fence", start: "00:00", end: "03:00" }],
];
const FIELDS = ["text", "object", "zone", "entity", "start", "end", "mode"];

export async function render(view, { params }) {
  view.innerHTML = pageHead("Frame Index Explorer", HELP) + `
    <div class="card" style="margin-bottom:12px">
      <div class="row" style="margin-bottom:10px"><span class="muted small">Presets</span>
        ${PRESETS.map(([label], i) => `<button class="chip" data-preset="${i}">${esc(label)}</button>`).join("")}</div>
      <form id="qx" class="qform">
        <label class="field wide">Free text (FTS + vectors)<input name="text" placeholder="someone hanging around the entrance"></label>
        <label class="field">Object (parsed like a caption)<input name="object" placeholder="truck, blue Ford F150, person"></label>
        <label class="field">Zone<select name="zone"><option value="">any</option></select></label>
        <label class="field">Entity<select name="entity"><option value="">any</option></select></label>
        <label class="field narrow">From<input name="start" placeholder="00:00" size="5"></label>
        <label class="field narrow">To<input name="end" placeholder="23:59" size="5"></label>
        <label class="field">Text mode<select name="mode"><option value="hybrid">hybrid</option><option value="keyword">keyword (BM25)</option><option value="semantic">semantic (TF-IDF)</option></select></label>
        <div class="row"><button class="btn btn-accent" type="submit">Run query</button><button class="btn btn-ghost" type="reset">Clear</button></div>
      </form>
    </div>
    <div id="qx-out">${empty("Pick a preset or fill in any combination of filters. Leave everything empty to list all frames.")}</div>`;

  const form = view.querySelector("#qx");
  try {
    const opts = await api("/api/index/options");
    form.zone.innerHTML += opts.zones.map((z) => `<option value="${esc(z.id)}">${esc(z.name)}</option>`).join("");
    form.entity.innerHTML += opts.entities.map((e) => `<option value="${esc(e.entity_id)}">${esc(e.entity_id)} · ${esc(e.display_name)}</option>`).join("");
  } catch (err) {
    view.querySelector("#qx-out").innerHTML = failure(err, "Could not load the index options");
  }
  const run = () => query(view, Object.fromEntries(FIELDS.map((f) => [f, form[f].value.trim()]).filter(([, v]) => v)));
  form.onsubmit = (e) => { e.preventDefault(); run(); };
  form.onreset = () => setTimeout(() => (view.querySelector("#qx-out").innerHTML = empty("Cleared.")), 0);
  view.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => {
    form.reset();
    const values = PRESETS[Number(b.dataset.preset)][1];
    for (const [k, v] of Object.entries(values)) form[k].value = v;
    run();
  });
  // deep link from other screens / demo mode: #/explorer?preset=0
  if (params.has("preset")) view.querySelector(`[data-preset="${Number(params.get("preset"))}"]`)?.click();
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
  const detail = res.rows.some((r) => r.object);
  out.innerHTML = `
    <div class="grid cols-2" style="margin-bottom:12px">
      <div class="card"><div class="card-head"><h3>Hits per hour</h3><span class="muted small">${res.count} frames · ${res.elapsed_ms} ms</span></div>
        <div class="chart-box" style="height:200px"><canvas id="qx-hist"></canvas></div></div>
      <div class="card"><div class="card-head"><h3>What ran</h3><span class="muted small">${res.sql.length} SQL statements</span></div>
        <ul class="small" style="margin:0 0 8px;padding-left:18px">${res.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
        <pre class="sql" style="max-height:150px;overflow:auto;background:var(--panel-2);padding:8px;border-radius:8px">${res.sql.map(esc).join(";\n\n")}</pre></div>
    </div>
    ${res.rows.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>Time</th><th>Frame</th><th>Zone</th>${detail ? "<th>Object</th><th>Entity</th><th>Conf.</th>" : ""}${res.rows.some((r) => r.score != null) ? "<th>Score</th>" : ""}<th>Caption</th></tr></thead><tbody>
      ${res.rows.map((r) => `<tr class="clickable" data-frame="${r.frame_id}"><td class="mono">${hhmm(r.ts)}</td><td>${flink(r.frame_id)}</td><td>${esc(zoneName(r.zone))}</td>
        ${detail ? `<td>${esc(r.object || "")}</td><td>${esc(r.entity_id || "")}</td><td class="num">${fixed(r.confidence, 2)}</td>` : ""}
        ${r.score != null ? `<td class="num">${fixed(r.score, 4)}</td>` : res.rows.some((x) => x.score != null) ? "<td></td>" : ""}
        <td>${esc(r.description)}</td></tr>`).join("")}</tbody></table></div>`
      : empty("No frames match. Try fewer filters, a wider time window, or another wording.")}`;
  chart(out.querySelector("#qx-hist"), {
    type: "bar",
    data: { labels: hours.map((_, h) => `${String(h).padStart(2, "0")}h`), datasets: [{ data: hours, backgroundColor: cssVar("--accent") }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });
}
