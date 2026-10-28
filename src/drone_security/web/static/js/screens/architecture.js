// Proof › Architecture: the pipeline as clickable components, the diagrams from docs/diagrams one at a time, and how this UI is wired.
import { api, esc, failure, lead, loading } from "../lib.js";

export const HELP = `The system's architecture and data flow (the same diagrams as in the report), each component with the code that implements
  it, plus how this UI talks to the backend.
  <div class="req">Covers: the design / architecture deliverable.</div>`;

const COMPONENTS = [
  ["Ingest", "ingest.py", "Validates records, drops duplicates, re-orders late frames in a small buffer, tolerates lost telemetry."],
  ["Perception", "perception/", "Caption (or image via the Gemini VLM) → structured detections: category, label, colour, make, model, clothing, action, zone, confidence."],
  ["Tracker", "tracking.py", "Re-identifies entities from appearance attributes with a confidence; splits sightings into visits; measures dwell time."],
  ["Rule engine", "rules/engine.py + config/rules.yaml", "Declarative rules; every alert explains the exact conditions that held; cooldown and correlation fold repeated firings into one alert per incident step."],
  ["Frame index", "indexing/frame_index.py", "SQLite + FTS5: frames, detections, entities, events, alerts, summaries, with indexes on time, object, zone and entity."],
  ["Hybrid retrieval", "retrieval.py", "BM25 keyword search + TF-IDF (or Gemini) vectors, merged by reciprocal-rank fusion."],
  ["Hierarchical memory", "memory.py", "10-minute → hour → day summaries with frame citations; the agent's bounded prompt context."],
  ["Analyst agent", "agent/", "Five tools over the index; offline router or LangChain + Gemini; citations checked after every answer."],
  ["Web UI", "web/ + run_ui.py", "FastAPI routes call the modules above; the replay streams a live pipeline over Server-Sent Events; plain HTML/JS, no build step."],
];
const TITLES = { "architecture.svg": "Architecture", "data_flow.svg": "Data flow: one frame through the pipeline", "agent_sequence.svg": "Agent Q&A sequence" };
let comp = 0;       // selected component
let diagram = null; // selected diagram file

export async function render(view) {
  view.innerHTML = lead("How the pieces fit: a pipeline of small modules that each do one job, with this UI as a thin layer on top.") + `<div id="ar">${loading()}</div>`;
  let diagrams;
  try { diagrams = (await api("/api/diagrams")).diagrams; } catch (err) { view.querySelector("#ar").innerHTML = failure(err, "Could not list the diagrams"); return; }
  const order = Object.keys(TITLES).filter((d) => diagrams.includes(d)).concat(diagrams.filter((d) => !TITLES[d]));
  if (!order.includes(diagram)) diagram = order[0];
  const el = view.querySelector("#ar");
  el.innerHTML = `
    <div class="card mb"><div class="card-head"><h3>From frame to answer</h3><span class="muted small">click a component</span></div>
      <div class="flow" id="flow">${COMPONENTS.map(([n], i) => `${i ? '<span class="arrow">&#8594;</span>' : ""}<button data-i="${i}">${esc(n)}</button>`).join("")}</div>
      <div class="comp-detail" id="comp"></div></div>
    ${order.length ? `<div class="card mb arch"><div class="seg" id="dg">${order.map((d) => `<button data-d="${esc(d)}">${esc(TITLES[d] || d)}</button>`).join("")}</div>
      <div id="dg-img" style="margin-top:14px"></div></div>` : ""}
    <div class="card"><details class="more"><summary>How this UI is wired to the backend</summary>
      <pre class="small" style="margin-top:10px">browser (static/index.html + ES modules, Chart.js vendored)
   │  fetch JSON  /api/status /api/alerts /api/events /api/frames/{id} /api/index/query /api/entities /api/summaries /api/context /api/results
   │  SSE         /api/replay/stream  → a fresh SecurityPipeline per connection, one frame at a time
   │  SSE (POST)  /api/chat           → SecurityAnalyst.ask (offline router, or LangChain + Gemini)
   ▼
FastAPI (src/drone_security/web/api.py) ──► existing modules (pipeline, index, tracker, rules, memory, agent, analysis, evaluation)
   │
   ▼
data/runs/ui.db  (the scenario day, rebuilt at start-up)        docs/results/*.json  (saved experiment outputs)</pre></details></div>`;

  const showComp = (i) => {
    comp = i;
    el.querySelectorAll("#flow button").forEach((b) => b.classList.toggle("on", Number(b.dataset.i) === i));
    const [name, code, what] = COMPONENTS[i];
    el.querySelector("#comp").innerHTML = `<b>${esc(name)}</b> <code class="muted">${esc(code)}</code><div style="margin-top:4px">${esc(what)}</div>`;
  };
  el.querySelector("#flow").onclick = (e) => { const b = e.target.closest("[data-i]"); if (b) showComp(Number(b.dataset.i)); };
  showComp(comp);

  const dg = el.querySelector("#dg");
  if (!dg) return;
  const showDiagram = (d) => {
    diagram = d;
    dg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.d === d));
    el.querySelector("#dg-img").innerHTML = `<img src="/diagrams/${encodeURIComponent(d)}" alt="${esc(TITLES[d] || d)}">`;
  };
  dg.onclick = (e) => { const b = e.target.closest("[data-d]"); if (b) showDiagram(b.dataset.d); };
  showDiagram(diagram);
}
