// Architecture: the diagrams from docs/diagrams with a short explanation of each component.
import { api, esc, failure, loading, pageHead } from "../lib.js";

const HELP = `The system's architecture and data flow (the same diagrams as in the report), plus how this UI talks to it.<br><br>
  Covers: <b>design / architecture</b> deliverable.`;

const COMPONENTS = [
  ["Ingest", "ingest.py", "Validates records, drops duplicates, re-orders late frames in a small buffer, tolerates lost telemetry."],
  ["Perception", "perception/", "Caption (or image via the Gemini VLM) → structured detections: category, label, colour, make, model, clothing, action, zone, confidence."],
  ["Tracker", "tracking.py", "Re-identifies entities from appearance attributes with a confidence; splits sightings into visits; measures dwell time."],
  ["Rule engine", "rules/engine.py + config/rules.yaml", "Declarative rules; every alert explains the exact conditions that held; cooldown and correlation fold repeated firings into one alert per incident step."],
  ["Frame index", "indexing/frame_index.py", "SQLite + FTS5: frames, detections, entities, events, alerts, summaries, with indexes on time, object, zone and entity."],
  ["Hybrid retrieval", "retrieval.py", "BM25 keyword search + TF-IDF (or Gemini) vectors, merged by reciprocal-rank fusion."],
  ["Hierarchical memory", "memory.py", "10-minute → hour → day summaries with frame citations; the agent's bounded prompt context."],
  ["Analyst agent", "agent/", "Five tools over the index; offline router or LangChain + Gemini; citations checked after every answer."],
  ["Web UI (this app)", "web/ + run_ui.py", "FastAPI routes call the modules above; the replay streams a live pipeline over Server-Sent Events; plain HTML/JS, no build step."],
];

export async function render(view) {
  view.innerHTML = pageHead("Architecture", HELP) + `<div id="ar">${loading()}</div>`;
  let diagrams;
  try { diagrams = (await api("/api/diagrams")).diagrams; } catch (err) { view.querySelector("#ar").innerHTML = failure(err, "Could not list the diagrams"); return; }
  const titles = { "architecture.svg": "Architecture", "data_flow.svg": "Data flow: one frame through the pipeline", "agent_sequence.svg": "Agent Q&A sequence" };
  const order = ["architecture.svg", "data_flow.svg", "agent_sequence.svg"].filter((d) => diagrams.includes(d)).concat(diagrams.filter((d) => !titles[d]));
  view.querySelector("#ar").innerHTML = `
    <div class="card" style="margin-bottom:14px"><h3>Components</h3>
      <table class="tbl"><thead><tr><th>Component</th><th>Code</th><th>What it does</th></tr></thead><tbody>
      ${COMPONENTS.map(([n, c, d]) => `<tr><td><b>${esc(n)}</b></td><td class="mono small">${esc(c)}</td><td>${esc(d)}</td></tr>`).join("")}</tbody></table></div>
    <div class="card" style="margin-bottom:14px"><h3>How the UI is wired</h3>
      <pre class="small">browser (static/index.html + ES modules, Chart.js vendored)
   │  fetch JSON  /api/status /api/alerts /api/events /api/frames/{id} /api/index/query /api/entities /api/summaries /api/context /api/results
   │  SSE         /api/replay/stream  → a fresh SecurityPipeline per connection, one frame at a time
   │  SSE (POST)  /api/chat           → SecurityAnalyst.ask (offline router, or LangChain + Gemini)
   ▼
FastAPI (src/drone_security/web/api.py) ──► existing modules (pipeline, index, tracker, rules, memory, agent, analysis, evaluation)
   │
   ▼
data/runs/ui.db  (the scenario day, rebuilt at start-up)        docs/results/*.json  (saved experiment outputs)</pre></div>
    ${order.map((d) => `<div class="card arch" style="margin-bottom:14px"><h3>${esc(titles[d] || d)}</h3><img src="/diagrams/${encodeURIComponent(d)}" alt="${esc(titles[d] || d)}" loading="lazy"></div>`).join("")}`;
}
