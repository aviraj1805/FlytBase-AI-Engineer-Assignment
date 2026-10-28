// Evaluation & scalability: charts drawn from the saved experiment outputs in docs/results, plus a small live benchmark.
import { api, chart, cssVar, esc, failure, fixed, loading, pageHead } from "../lib.js";

const HELP = `Charts drawn from the <b>saved real outputs</b> in <code>docs/results/*.json</code> (each shows when and where it was generated):
  alerts, detections and re-ID scored against a ground truth the system never reads, retrieval quality, re-ID robustness, noise control,
  and the 10k–200k frame benchmark. "Run benchmark" runs the same benchmark live at a small size on this machine.<br><br>
  Covers: <b>evaluation / testing</b> and <b>scalability</b>.`;

export async function render(view) {
  view.innerHTML = pageHead("Evaluation & Scalability", HELP) + `<div id="ev">${loading()}</div>`;
  const el = view.querySelector("#ev");
  let R;
  try { R = await api("/api/results"); } catch (err) { el.innerHTML = failure(err, "Could not load docs/results"); return; }
  const s = R.eval_scenario_rules, b = R.benchmark, ab = R.ablation, rt = R.retrieval_eval, pe = R.paraphrase_eval, vlm = R.vlm_check;
  const prov = (x) => x ? `<span class="muted small">${esc(x.provenance.command)} · ${esc(x.provenance.generated_at)}</span>` : "";
  const kpi = (v, l) => `<div class="card kpi"><span class="v">${v}</span><span class="l">${l}</span></div>`;
  el.innerHTML = `
    ${s ? `<div class="grid cols-4" style="margin-bottom:14px">
      ${kpi(fixed(s.summary.alert_f1.mean, 3), `alert F1 vs ground truth (mean of ${s.seeds.length} seeds)`)}
      ${kpi(fixed(s.summary.detection_precision.mean, 3), "detection precision")}
      ${kpi(fixed(s.summary.reid_f1.mean, 3), "re-identification F1")}
      ${kpi(b ? `${b.rows[b.rows.length - 1].ingest_fps.toLocaleString()}/s` : "–", b ? `ingest at ${b.rows[b.rows.length - 1].frames.toLocaleString()} frames` : "")}</div>` : ""}
    <div class="grid cols-2" style="margin-bottom:14px">
      <div class="card"><div class="card-head"><h3>Precision / recall / F1 vs ground truth</h3>${prov(s)}</div><div class="chart-box"><canvas id="c-prf"></canvas></div>
        <p class="small muted">Development captions (the parser was built against them): the generalisation numbers are the reworded-caption and ablation charts.</p></div>
      <div class="card"><div class="card-head"><h3>Alert F1 on reworded captions (held-out wording)</h3>${prov(pe)}</div><div class="chart-box"><canvas id="c-para"></canvas></div></div>
      <div class="card"><div class="card-head"><h3>Re-ID F1 when identity attributes are dropped</h3>${prov(ab)}</div><div class="chart-box"><canvas id="c-reid"></canvas></div></div>
      <div class="card"><div class="card-head"><h3>Noise control: alerts per day</h3>${prov(ab)}</div><div class="chart-box"><canvas id="c-noise"></canvas></div></div>
      <div class="card"><div class="card-head"><h3>Retrieval quality${rt ? ` (${rt.queries} labelled queries)` : ""}</h3>${prov(rt)}</div><div class="chart-box"><canvas id="c-ret"></canvas></div></div>
      <div class="card"><div class="card-head"><h3>Gemini VLM on real photos</h3>${prov(vlm)}</div>${vlm ? `<table class="tbl"><tbody>
        <tr><td>Model</td><td>${esc(vlm.summary.model)}</td></tr><tr><td>Images</td><td>${vlm.images}</td></tr>
        <tr><td>Object recall (VLM)</td><td class="num">${fixed(vlm.summary.vlm.object_recall, 3)}</td></tr>
        <tr><td>Attribute accuracy (VLM)</td><td class="num">${fixed(vlm.summary.vlm.attribute_accuracy, 3)}</td></tr>
        <tr><td>Images with false alarms</td><td class="num">${vlm.summary.vlm.images_with_false_alarms}</td></tr>
        <tr><td>Alert agreement with labels</td><td class="num">${fixed(vlm.summary.alert_agreement, 3)}</td></tr>
        <tr><td>Mean latency</td><td class="num">${fixed(vlm.summary.mean_latency_s, 1)} s</td></tr></tbody></table>` : '<p class="muted">Not recorded.</p>'}</div>
    </div>
    <div class="card" style="margin-bottom:14px"><div class="card-head"><h3>Scalability benchmark (saved run)</h3>${prov(b)}</div>
      <div class="grid cols-3"><div class="chart-box"><canvas id="c-fps"></canvas></div><div class="chart-box"><canvas id="c-lat"></canvas></div><div class="chart-box"><canvas id="c-ctx"></canvas></div></div>
      ${b ? `<p class="small muted">Recorded on ${esc(b.provenance.platform)}, Python ${esc(b.provenance.python)}. Queries run against the full index at each size.</p>` : ""}</div>
    <div class="card"><div class="card-head"><h3>Run the benchmark here</h3>
      <div class="row"><select id="bn"><option value="2000">2,000 frames (~3 s)</option><option value="5000">5,000 frames</option><option value="10000">10,000 frames</option></select>
      <button class="btn btn-accent" id="bench">Run benchmark</button></div></div>
      <div id="bench-out" class="muted small">Runs generate → ingest → index → queries on this machine; results are shown here and not written over docs/results.</div></div>`;

  const C = { a: cssVar("--accent"), ok: cssVar("--ok"), w: cssVar("--warn"), bad: cssVar("--bad"), v: cssVar("--cat-vehicle"), p: cssVar("--cat-person") };
  const pctAxis = { y: { min: 0, max: 1 } };
  if (s) chart(el.querySelector("#c-prf"), { type: "bar", data: { labels: ["alerts", "detections", "re-ID"], datasets: [
    { label: "precision", data: [s.summary.alert_precision.mean, s.summary.detection_precision.mean, s.summary.reid_precision.mean], backgroundColor: C.a },
    { label: "recall", data: [s.summary.alert_recall.mean, s.summary.detection_recall.mean, s.summary.reid_recall.mean], backgroundColor: C.v },
    { label: "F1", data: [s.summary.alert_f1.mean, null, s.summary.reid_f1.mean], backgroundColor: C.ok }] }, options: { scales: pctAxis } });
  if (pe) chart(el.querySelector("#c-para"), { type: "bar", data: { labels: Object.keys(pe.results), datasets: [
    { label: "alert F1", data: Object.values(pe.results).map((r) => r.alert_f1), backgroundColor: Object.keys(pe.results).map((k) => k.startsWith("gemini") ? C.ok : C.a) }] },
    options: { scales: pctAxis, plugins: { legend: { display: false } } } });
  if (ab) {
    const trackers = [...new Set(ab.reid.rows.map((r) => r.tracker))];
    chart(el.querySelector("#c-reid"), { type: "line", data: { labels: ab.reid.levels.map((l) => `${l * 100}% dropped`), datasets: trackers.map((t, i) => ({
      label: t, data: ab.reid.levels.map((l) => (ab.reid.rows.find((r) => r.tracker === t && r.dropout === l) || {}).reid_f1), borderColor: i ? C.bad : C.ok, backgroundColor: i ? C.bad : C.ok })) },
      options: { scales: pctAxis } });
    chart(el.querySelector("#c-noise"), { type: "bar", data: { labels: ab.noise.rows.map((r) => r.setting), datasets: [
      { label: "alerts per day", data: ab.noise.rows.map((r) => r.alerts_per_day), backgroundColor: [C.ok, C.w, C.bad] }] }, options: { plugins: { legend: { display: false } } } });
  }
  if (rt) chart(el.querySelector("#c-ret"), { type: "bar", data: { labels: Object.keys(rt.results), datasets: [
    { label: "nDCG@10", data: Object.values(rt.results).map((r) => r.overall.ndcg), backgroundColor: C.a },
    { label: "hit@10", data: Object.values(rt.results).map((r) => r.overall.hit_at_k), backgroundColor: C.v }] }, options: { scales: pctAxis } });
  if (b) drawBench(el, b.rows, C, "saved");

  el.querySelector("#bench").onclick = async (e) => {
    const out = el.querySelector("#bench-out");
    e.target.disabled = true;
    out.innerHTML = loading("Running the benchmark…");
    try {
      const r = await api(`/api/benchmark?frames=${el.querySelector("#bn").value}`, { method: "POST" });
      const row = r.rows[0];
      out.innerHTML = `<table class="tbl"><tbody>
        <tr><td>Frames / days</td><td class="num">${row.frames.toLocaleString()} / ${row.days}</td></tr>
        <tr><td>Ingest</td><td class="num">${row.ingest_s} s = ${row.ingest_fps.toLocaleString()} frames/s</td></tr>
        <tr><td>Per-frame latency p50 / p99</td><td class="num">${row.frame_latency_p50_ms ?? "–"} / ${row.frame_latency_p99_ms ?? "–"} ms</td></tr>
        <tr><td>Alerts / raw rule firings</td><td class="num">${row.alerts} / ${row.raw_rule_firings}</td></tr>
        <tr><td>DB size</td><td class="num">${row.db_mb} MB</td></tr>
        <tr><td>Context tokens: hierarchical vs naive</td><td class="num">${row.context_tokens_hierarchical.toLocaleString()} vs ${row.context_tokens_naive.toLocaleString()}</td></tr>
        ${Object.entries(row.latency).map(([q, v]) => `<tr><td>${esc(q)}</td><td class="num">p50 ${v.p50_ms} ms · p95 ${v.p95_ms} ms</td></tr>`).join("")}</tbody></table>`;
    } catch (err) {
      out.innerHTML = failure(err, "Benchmark failed");
    }
    e.target.disabled = false;
  };
}

function drawBench(el, rows, C) {
  const labels = rows.map((r) => `${r.frames / 1000}k`);
  chart(el.querySelector("#c-fps"), { type: "line", data: { labels, datasets: [{ label: "ingest frames/s", data: rows.map((r) => r.ingest_fps), borderColor: C.a, backgroundColor: C.a }] },
    options: { scales: { y: { beginAtZero: true } } } });
  const queries = Object.keys(rows[0].latency);
  const palette = [C.a, C.v, C.ok, C.w, C.bad, C.p, "#a3e635", "#c084fc"];
  chart(el.querySelector("#c-lat"), { type: "bar", data: { labels, datasets: queries.map((q, i) => ({ label: q, data: rows.map((r) => r.latency[q].p50_ms), backgroundColor: palette[i % palette.length] })) },
    options: { plugins: { legend: { display: false }, title: { display: true, text: "query latency p50 (ms), hover for the query" } }, scales: { y: { beginAtZero: true } } } });
  chart(el.querySelector("#c-ctx"), { type: "bar", data: { labels, datasets: [
    { label: "hierarchical", data: rows.map((r) => r.context_tokens_hierarchical), backgroundColor: C.ok },
    { label: "naive", data: rows.map((r) => r.context_tokens_naive), backgroundColor: C.bad }] },
    options: { plugins: { title: { display: true, text: "prompt tokens (log scale)" } }, scales: { y: { type: "logarithmic" } } } });
}
