// Proof › Results: charts drawn from the saved experiment outputs in docs/results, one at a time, plus a small live benchmark.
import { api, chart, charts, countUp, cssVar, esc, failure, fixed, lead, loading, num } from "../lib.js";

export const HELP = `Every chart is drawn from the <b>saved real outputs</b> in <code>docs/results/*.json</code>, with the command and time that
  produced it: alerts, detections and re-ID scored against a ground truth the system never reads, generalisation to reworded captions,
  re-ID robustness, noise control, retrieval quality, the Gemini VLM on real photos, and the 10k–200k frame benchmark.
  "Run benchmark" runs the same benchmark live at a small size on this machine.
  <div class="req">Covers: evaluation / testing and scalability.</div>`;

const pctAxis = { y: { min: 0, max: 1 } };

// [id, tab label, title, what it shows, saved result it needs, draw(box, R, colours)]
const VIEWS = [
  ["accuracy", "Accuracy", "Precision, recall and F1 against the hidden ground truth", "Alerts, detections and re-identification scored against ground_truth.json, which the pipeline never reads. These captions were used to build the parser, so the next tabs test generalisation.", "eval_scenario_rules",
    (box, R, C) => {
      const s = R.eval_scenario_rules.summary;
      box.innerHTML = '<div class="chart-box"><canvas id="c-prf"></canvas></div>';
      chart(box.querySelector("canvas"), { type: "bar", data: { labels: ["alerts", "detections", "re-ID"], datasets: [
        { label: "precision", data: [s.alert_precision.mean, s.detection_precision.mean, s.reid_precision.mean], backgroundColor: C.a, borderRadius: 6 },
        { label: "recall", data: [s.alert_recall.mean, s.detection_recall.mean, s.reid_recall.mean], backgroundColor: C.v, borderRadius: 6 },
        { label: "F1", data: [s.alert_f1.mean, null, s.reid_f1.mean], backgroundColor: C.ok, borderRadius: 6 }] }, options: { scales: pctAxis } });
    }],
  ["reworded", "Reworded captions", "Alert F1 on reworded captions (held-out wording)", "The same day with every caption reworded. The rule-based parser drops on wording it never saw; the Gemini analyzer does not.", "paraphrase_eval",
    (box, R, C) => {
      const pe = R.paraphrase_eval.results;
      box.innerHTML = '<div class="chart-box"><canvas id="c-para"></canvas></div>';
      chart(box.querySelector("canvas"), { type: "bar", data: { labels: Object.keys(pe), datasets: [
        { label: "alert F1", data: Object.values(pe).map((r) => r.alert_f1), backgroundColor: Object.keys(pe).map((k) => (k.startsWith("gemini") ? C.ok : C.a)), borderRadius: 6 }] },
        options: { scales: pctAxis, plugins: { legend: { display: false } } } });
    }],
  ["reid", "Re-ID robustness", "Re-identification F1 when identity attributes are dropped", "Attributes such as colour or make are removed at random from captions; the similarity tracker degrades far more slowly than an exact signature match.", "ablation",
    (box, R, C) => {
      const ab = R.ablation.reid;
      const trackers = [...new Set(ab.rows.map((r) => r.tracker))];
      box.innerHTML = '<div class="chart-box"><canvas id="c-reid"></canvas></div>';
      chart(box.querySelector("canvas"), { type: "line", data: { labels: ab.levels.map((l) => `${l * 100}% dropped`), datasets: trackers.map((t) => {
        const color = t.startsWith("similarity") ? C.ok : C.bad;
        return { label: t, data: ab.levels.map((l) => (ab.rows.find((r) => r.tracker === t && r.dropout === l) || {}).reid_f1), borderColor: color, backgroundColor: color, tension: 0.3 };
      }) }, options: { scales: pctAxis } });
    }],
  ["noise", "Noise control", "Alerts per day under each noise-control setting", "Cooldown stops one rule repeating for the same thing; correlation folds related rules into one alert per incident step.", "ablation",
    (box, R, C) => {
      const rows = R.ablation.noise.rows;
      box.innerHTML = '<div class="chart-box"><canvas id="c-noise"></canvas></div>';
      chart(box.querySelector("canvas"), { type: "bar", data: { labels: rows.map((r) => r.setting), datasets: [
        { label: "alerts per day", data: rows.map((r) => r.alerts_per_day), backgroundColor: [C.ok, C.w, C.bad], borderRadius: 6 }] }, options: { plugins: { legend: { display: false } } } });
    }],
  ["retrieval", "Retrieval", "Retrieval quality on labelled queries", "nDCG@10 and hit@10 for keyword (FTS5/BM25), semantic and hybrid search over the frame index.", "retrieval_eval",
    (box, R, C) => {
      const rt = R.retrieval_eval;
      box.innerHTML = `<div class="chart-box"><canvas id="c-ret"></canvas></div><p class="small muted">${rt.queries} labelled queries.</p>`;
      chart(box.querySelector("canvas"), { type: "bar", data: { labels: Object.keys(rt.results), datasets: [
        { label: "nDCG@10", data: Object.values(rt.results).map((r) => r.overall.ndcg), backgroundColor: C.a, borderRadius: 6 },
        { label: "hit@10", data: Object.values(rt.results).map((r) => r.overall.hit_at_k), backgroundColor: C.v, borderRadius: 6 }] }, options: { scales: pctAxis } });
    }],
  ["vlm", "Real photos", "Gemini VLM on real photos", "The vision path run on real images with hand-written labels.", "vlm_check",
    (box, R) => {
      const v = R.vlm_check;
      box.innerHTML = `<table class="tbl"><tbody>
        <tr><td>Model</td><td>${esc(v.summary.model)}</td></tr><tr><td>Images</td><td class="num">${v.images}</td></tr>
        <tr><td>Object recall</td><td class="num">${fixed(v.summary.vlm.object_recall, 3)}</td></tr>
        <tr><td>Attribute accuracy</td><td class="num">${fixed(v.summary.vlm.attribute_accuracy, 3)}</td></tr>
        <tr><td>Images with false alarms</td><td class="num">${v.summary.vlm.images_with_false_alarms}</td></tr>
        <tr><td>Alert agreement with labels</td><td class="num">${fixed(v.summary.alert_agreement, 3)}</td></tr>
        <tr><td>Mean latency</td><td class="num">${fixed(v.summary.mean_latency_s, 1)} s</td></tr></tbody></table>`;
    }],
  ["scale", "Scale", "Scalability benchmark, 10k to 200k frames", "Generate, ingest, index and query at each size. Queries run against the full index; prompt size compares hierarchical memory with pasting every frame.", "benchmark",
    (box, R, C) => {
      const b = R.benchmark, rows = b.rows, labels = rows.map((r) => `${r.frames / 1000}k`);
      box.innerHTML = `<div class="grid cols-3"><div class="chart-box sm"><canvas id="c-fps"></canvas></div><div class="chart-box sm"><canvas id="c-lat"></canvas></div><div class="chart-box sm"><canvas id="c-ctx"></canvas></div></div>
        <p class="small muted">Recorded on ${esc(b.provenance.platform)}, Python ${esc(b.provenance.python)}.</p>`;
      chart(box.querySelector("#c-fps"), { type: "line", data: { labels, datasets: [{ label: "ingest frames/s", data: rows.map((r) => r.ingest_fps), borderColor: C.a, backgroundColor: C.a, tension: 0.3 }] },
        options: { scales: { y: { beginAtZero: true } } } });
      const queries = Object.keys(rows[0].latency);
      const palette = [C.a, C.v, C.ok, C.w, C.bad, C.p, cssVar("--cat-animal"), cssVar("--cat-infrastructure")];
      chart(box.querySelector("#c-lat"), { type: "bar", data: { labels, datasets: queries.map((q, i) => ({ label: q, data: rows.map((r) => r.latency[q].p50_ms), backgroundColor: palette[i % palette.length] })) },
        options: { plugins: { legend: { display: false }, title: { display: true, text: "query latency p50 (ms), hover for the query" } }, scales: { y: { beginAtZero: true } } } });
      chart(box.querySelector("#c-ctx"), { type: "bar", data: { labels, datasets: [
        { label: "hierarchical", data: rows.map((r) => r.context_tokens_hierarchical), backgroundColor: C.ok },
        { label: "naive", data: rows.map((r) => r.context_tokens_naive), backgroundColor: C.bad }] },
        options: { plugins: { title: { display: true, text: "prompt tokens (log scale)" } }, scales: { y: { type: "logarithmic" } } } });
    }],
];
let shown = "accuracy";

export async function render(view) {
  view.innerHTML = lead("Measured, not claimed: scores against a ground truth the system never reads, robustness checks and scale tests. Every chart comes from a saved run in docs/results.") +
    `<div id="ev">${loading()}</div>`;
  const el = view.querySelector("#ev");
  let R;
  try { R = await api("/api/results"); } catch (err) { el.innerHTML = failure(err, "Could not load docs/results"); return; }
  const s = R.eval_scenario_rules, b = R.benchmark;
  const last = b && b.rows[b.rows.length - 1];
  const kpi = (v, l) => `<div class="card kpi lift"><span class="v">${v}</span><span class="l">${l}</span></div>`;
  const views = VIEWS.filter((v) => R[v[4]]);
  if (!views.some((v) => v[0] === shown)) shown = views.length ? views[0][0] : "";
  el.innerHTML = `
    ${s ? `<div class="grid cols-4 mb">
      ${kpi(num(s.summary.alert_f1.mean, 3), `alert F1 against the ground truth (mean of ${s.seeds.length} seeds)`)}
      ${kpi(num(s.summary.detection_precision.mean, 3), "detection precision")}
      ${kpi(num(s.summary.reid_f1.mean, 3), "re-identification F1")}
      ${kpi(last ? num(last.ingest_fps, 0, "/s") : "–", last ? `frames ingested per second at ${last.frames.toLocaleString()} frames` : "no saved benchmark")}</div>` : ""}
    <div class="card mb">
      <div class="seg" id="ev-tabs">${views.map(([id, label]) => `<button data-v="${id}">${esc(label)}</button>`).join("")}</div>
      <div id="ev-view" style="margin-top:16px">${views.length ? "" : '<p class="muted">No saved results in docs/results.</p>'}</div>
    </div>
    <div class="card"><div class="card-head"><h3>Run the benchmark here</h3>
      <div class="row"><select id="bn" aria-label="Benchmark size"><option value="2000">2,000 frames (~3 s)</option><option value="5000">5,000 frames</option><option value="10000">10,000 frames</option></select>
      <button class="btn btn-accent" id="bench">&#9654; Run benchmark</button></div></div>
      <div id="bench-out" class="muted small">Runs generate → ingest → index → queries on this machine; the result is shown here and not written over docs/results.</div></div>`;
  countUp(el);

  const C = { a: cssVar("--accent"), ok: cssVar("--ok"), w: cssVar("--warn"), bad: cssVar("--bad"), v: cssVar("--cat-vehicle"), p: cssVar("--cat-person") };
  const show = (id) => {
    shown = id;
    const v = views.find((x) => x[0] === id);
    el.querySelectorAll("#ev-tabs button").forEach((b2) => b2.classList.toggle("on", b2.dataset.v === id));
    while (charts.length) charts.pop().destroy();
    const box = el.querySelector("#ev-view");
    const prov = R[v[4]].provenance;
    box.innerHTML = `<div class="card-head" style="margin-bottom:4px"><h2>${esc(v[2])}</h2><span class="muted small">${esc(prov.command)} · ${esc(prov.generated_at)}</span></div>
      <p class="muted" style="margin:0 0 12px">${esc(v[3])}</p><div id="ev-chart"></div>`;
    v[5](box.querySelector("#ev-chart"), R, C);
  };
  el.querySelector("#ev-tabs").onclick = (e) => { const b2 = e.target.closest("[data-v]"); if (b2) show(b2.dataset.v); };
  if (shown) show(shown);

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
