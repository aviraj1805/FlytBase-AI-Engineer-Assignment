// Context Manager: what the agent's prompt context contains (hierarchical memory) vs pasting every frame.
import { api, chart, cssVar, esc, failure, fixed, linkCitations, loading, pageHead } from "../lib.js";

const HELP = `How the agent's context is built for a question: property facts, coverage, <b>hour-by-hour summaries</b>, a <b>rolling window</b>
  of the last hour's 10-minute summaries and the latest frames, filled in priority order until the token budget is spent. Anything finer
  is one tool call away. The baseline pastes every frame and alert into the prompt.<br><br>
  Covers: <b>context management for long footage</b>. Token counts are estimates (4 characters per token), as in the report;
  the recorded Gemini experiment below used Gemini's own counts.`;

const SECTION_COLORS = ["--accent", "--cat-vehicle", "--cat-person", "--cat-animal", "--cat-object", "--cat-infrastructure"];

export async function render(view) {
  view.innerHTML = pageHead("Context Manager", HELP, `<label class="row small muted">Token budget <input type="range" id="budget" min="500" max="8000" step="250" value="2500" style="width:160px"> <b id="budget-v" class="num">2500</b></label>`) +
    `<div id="cx">${loading()}</div>`;
  const slider = view.querySelector("#budget");
  let timer;
  slider.oninput = () => { view.querySelector("#budget-v").textContent = slider.value; clearTimeout(timer); timer = setTimeout(() => load(view, slider.value), 200); };
  await load(view, slider.value);
}

async function load(view, budget) {
  const el = view.querySelector("#cx");
  let d;
  try {
    d = await api(`/api/context?budget=${budget}`);
  } catch (err) {
    el.innerHTML = failure(err, "Could not build the context");
    return;
  }
  const h = d.hierarchical, ratio = d.naive.tokens / h.tokens;
  const colors = SECTION_COLORS.map(cssVar);
  el.innerHTML = `
    <div class="grid cols-4" style="margin-bottom:14px">
      <div class="card kpi"><span class="v">${h.tokens.toLocaleString()}</span><span class="l">tokens: hierarchical context (budget ${d.budget})</span></div>
      <div class="card kpi"><span class="v">${d.naive.tokens.toLocaleString()}</span><span class="l">tokens: every frame + alert pasted (baseline)</span></div>
      <div class="card kpi"><span class="v">${fixed(ratio, 1)}×</span><span class="l">smaller prompt for this ${d.frames}-frame day</span></div>
      <div class="card kpi"><span class="v">${h.sections.length}</span><span class="l">sections, filled in priority order</span></div>
    </div>
    <div class="card" style="margin-bottom:14px"><h3>Composition of the hierarchical context</h3>
      <div class="ctx-bar" style="margin:8px 0">${h.sections.map((s, i) => `<div style="width:${(s.tokens / h.tokens) * 100}%;background:${colors[i % colors.length]}" title="${esc(s.title)}: ${s.tokens} tokens">${s.tokens > h.tokens * 0.08 ? esc(s.title) : ""}</div>`).join("")}</div>
      <div class="row small">${h.sections.map((s, i) => `<span><span class="cat" style="background:${colors[i % colors.length]}"></span>${esc(s.title)} <b class="num">${s.tokens}</b></span>`).join("")}</div>
      <h3 style="margin-top:14px">Prompt size: hierarchical vs naive</h3>
      <div class="chart-box" style="height:140px"><canvas id="cx-chart"></canvas></div>
    </div>
    <div class="grid cols-2" style="align-items:start">
      <div class="card"><div class="card-head"><h3>What the agent sees</h3><span class="muted small">${h.text.length.toLocaleString()} characters</span></div>
        <div style="max-height:460px;overflow:auto">${h.sections.map((s) => `<div style="margin-bottom:10px"><b>${esc(s.title)}</b> <span class="chip">${s.tokens} tokens</span><pre class="small" style="margin-top:4px">${linkCitations(s.text)}</pre></div>`).join("")}</div></div>
      <div class="stack">
        <div class="card"><div class="card-head"><h3>Baseline: everything in the prompt</h3><span class="muted small">${d.naive.chars.toLocaleString()} characters, first 3,000 shown</span></div>
          <pre class="small muted" style="max-height:200px;overflow:auto">${esc(d.naive.preview)}…</pre></div>
        ${saved(d.saved)}
      </div>
    </div>`;
  chart(el.querySelector("#cx-chart"), {
    type: "bar",
    data: { labels: ["hierarchical + tools", "naive (every frame)"], datasets: [{ data: [h.tokens, d.naive.tokens], backgroundColor: [cssVar("--ok"), cssVar("--bad")] }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, title: { display: true, text: "estimated tokens" } } } },
  });
}

function saved(s) {
  const runs = Object.entries(s || {});
  if (!runs.length) return '<div class="card"><h3>Recorded experiments</h3><p class="muted">No saved Q&A results in docs/results.</p></div>';
  return `<div class="card"><h3>Recorded experiments (docs/results)</h3>${runs.map(([name, r]) => `
    <div style="margin-top:8px"><b>${esc(name)}</b> <span class="muted small">${r.questions} questions · model ${esc(r.model || "none (offline)")} · ${esc(r.generated_at)}</span>
    <table class="tbl" style="margin-top:4px"><thead><tr><th>Condition</th><th>Accuracy</th><th>Mean prompt tokens</th><th>Made-up citations</th></tr></thead><tbody>
    ${Object.entries(r.results).map(([c, v]) => `<tr><td>${esc(c)}</td><td class="num">${fixed(v.accuracy, 3)}</td><td class="num">${v.mean_prompt_tokens == null ? "–" : Number(v.mean_prompt_tokens).toLocaleString()}</td><td class="num">${v.answers_with_unsupported_citations ?? "–"}</td></tr>`).join("")}
    </tbody></table></div>`).join("")}</div>`;
}
