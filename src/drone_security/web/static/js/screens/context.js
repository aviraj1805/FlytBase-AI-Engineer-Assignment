// Insights › Memory: what the agent's prompt context holds (hierarchical memory) against pasting every frame.
import { api, countUp, cssVar, esc, failure, fixed, lead, linkCitations, loading, num } from "../lib.js";

export const HELP = `How the agent's context is built for a question: property facts, coverage, <b>hour-by-hour summaries</b>, a <b>rolling window</b>
  of the last hour's 10-minute summaries and the latest frames, filled in priority order until the token budget is spent. Anything finer
  is one tool call away. The baseline pastes every frame and alert into the prompt. Token counts are estimates (4 characters per token),
  as in the report; the recorded Gemini experiment used Gemini's own counts.
  <div class="req">Covers: context management for long footage.</div>`;

const SECTION_COLORS = ["--accent", "--cat-vehicle", "--cat-person", "--cat-animal", "--cat-object", "--cat-infrastructure"];
let budget = 2500;

export async function render(view) {
  view.innerHTML = lead("How the agent fits a whole day of footage into a small prompt: summaries first, every detail one tool call away.",
    `<label class="row small muted">Token budget <input type="range" id="budget" min="500" max="8000" step="250" value="${budget}" style="width:160px">
      <b id="budget-v" class="num" style="color:var(--text)">${budget}</b></label>`) + `<div id="cx">${loading()}</div>`;
  const slider = view.querySelector("#budget");
  let timer;
  slider.oninput = () => {
    budget = Number(slider.value);
    view.querySelector("#budget-v").textContent = slider.value;
    clearTimeout(timer);
    timer = setTimeout(() => load(view), 200);
  };
  await load(view);
}

async function load(view) {
  const el = view.querySelector("#cx");
  let d;
  try {
    d = await api(`/api/context?budget=${budget}`);
  } catch (err) {
    el.innerHTML = failure(err, "Could not build the context");
    return;
  }
  const h = d.hierarchical;
  const colors = SECTION_COLORS.map(cssVar);
  const bar = (label, tokens, color) => `<div class="bar-row"><span>${label}</span><div class="track"><div class="fill" style="width:${(tokens / d.naive.tokens) * 100}%;background:${color}"></div></div><b class="num">${num(tokens)}</b></div>`;
  el.innerHTML = `
    <div class="grid mb" style="grid-template-columns: minmax(0, 2fr) minmax(220px, 1fr)">
      <div class="card"><h3>Prompt size for this ${d.frames}-frame day</h3>
        <div class="compare" style="margin-top:14px">${bar("Hierarchical memory", h.tokens, "var(--ok)")}${bar("Paste every frame", d.naive.tokens, "var(--bad)")}</div>
        <p class="small muted" style="margin:12px 0 0">Estimated tokens (4 characters per token) at a budget of ${d.budget}. Drag the budget to watch the sections fill in.</p></div>
      <div class="card kpi" style="justify-content:center"><span class="big-ratio">${num(d.naive.tokens / h.tokens, 1, "×")}</span><span class="l">smaller than pasting every frame and alert</span></div>
    </div>
    <div class="card mb"><div class="card-head"><h3>What fills the budget, in priority order</h3><span class="muted small">${h.sections.length} sections</span></div>
      <div class="ctx-bar">${h.sections.map((s, i) => `<div style="width:${(s.tokens / h.tokens) * 100}%;background:${colors[i % colors.length]}" title="${esc(s.title)}: ${s.tokens} tokens">${s.tokens > h.tokens * 0.1 ? esc(s.title) : ""}</div>`).join("")}</div>
      <div class="row small" style="margin-top:10px;gap:14px">${h.sections.map((s, i) => `<span><span class="cat" style="background:${colors[i % colors.length]}"></span>${esc(s.title)} <b class="num">${s.tokens}</b></span>`).join("")}</div>
      <details class="more" style="margin-top:14px"><summary>Read exactly what the agent sees (${h.text.length.toLocaleString()} characters)</summary>
        <div style="max-height:420px;overflow:auto;margin-top:8px">${h.sections.map((s) => `<div style="margin-bottom:12px"><b>${esc(s.title)}</b> <span class="chip">${s.tokens} tokens</span><pre class="small" style="margin-top:4px">${linkCitations(s.text)}</pre></div>`).join("")}</div></details>
      <details class="more" style="margin-top:10px"><summary>Compare with the baseline that pastes everything (${d.naive.chars.toLocaleString()} characters, first 3,000 shown)</summary>
        <pre class="small muted" style="max-height:220px;overflow:auto;margin-top:8px">${esc(d.naive.preview)}…</pre></details>
    </div>
    ${saved(d.saved)}`;
  countUp(el);
}

function saved(s) {
  const runs = Object.entries(s || {});
  if (!runs.length) return '<div class="card"><h3>Does the smaller prompt still answer correctly?</h3><p class="muted">No saved Q&A results in docs/results.</p></div>';
  return `<div class="card"><h3>Does the smaller prompt still answer correctly? (recorded runs, docs/results)</h3>${runs.map(([name, r]) => `
    <div style="margin-top:12px"><b>${esc(name)}</b> <span class="muted small">${r.questions} questions · model ${esc(r.model || "none (offline)")} · ${esc(r.generated_at)}</span>
    <table class="tbl" style="margin-top:6px"><thead><tr><th>Condition</th><th>Accuracy</th><th>Mean prompt tokens</th><th>Made-up citations</th></tr></thead><tbody>
    ${Object.entries(r.results).map(([c, v]) => `<tr><td>${esc(c)}</td><td class="num">${fixed(v.accuracy, 3)}</td><td class="num">${v.mean_prompt_tokens == null ? "–" : Number(v.mean_prompt_tokens).toLocaleString()}</td><td class="num">${v.answers_with_unsupported_citations ?? "–"}</td></tr>`).join("")}
    </tbody></table></div>`).join("")}</div>`;
}
