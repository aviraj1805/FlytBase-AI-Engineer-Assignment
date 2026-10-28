// Summaries: one-sentence summary, daily briefing, recommendations, and the frame → 10-min → hour → day pyramid.
import { api, esc, failure, flink, hhmm, linkCitations, loading, pageHead, zoneName } from "../lib.js";
import { app } from "../app.js";

const HELP = `Everything here is built from the frame index, so a summary cannot mention an event that is not stored.
  The <b>hierarchical summaries</b> are written while frames stream in: 10-minute windows → hours → day; expand a level to drill down,
  down to the raw frames of a window. Every line keeps its [fN] citations.<br><br>
  Covers: <b>bonus: video summarisation</b> (one sentence + daily briefing) and long-footage memory.`;

export async function render(view) {
  const gemini = app.status && app.status.gemini.mode === "gemini";
  view.innerHTML = pageHead("Summaries", HELP, gemini ? `<button class="btn btn-sm" id="llm">Rewrite briefing with Gemini</button>` : "") + `<div id="sm">${loading()}</div>`;
  const load = async (llm = false) => {
    const el = view.querySelector("#sm");
    el.innerHTML = loading(llm ? "Gemini is writing the briefing…" : "Loading…");
    let d;
    try {
      d = await api(`/api/summaries${llm ? "?llm=true" : ""}`);
    } catch (err) {
      el.innerHTML = failure(err, "Could not load summaries");
      return;
    }
    const hours = d.levels.hour, minutes = d.levels.minute;
    el.innerHTML = `
      <div class="card" style="margin-bottom:14px"><h3>One-sentence summary</h3><p style="font-size:16px;margin:4px 0 0;line-height:1.5">${linkCitations(d.one_sentence)}</p></div>
      <div class="grid cols-2" style="margin-bottom:14px;align-items:start">
        <div class="card"><div class="card-head"><h3>Daily security briefing</h3><span class="chip">${esc(d.briefing_by)}</span></div><pre style="font-family:inherit;font-size:13px">${linkCitations(d.briefing)}</pre></div>
        <div class="card"><h3>Recommendations</h3>${d.recommendations.map((r) => `<div class="det"><span class="chip">${esc(r.priority)}</span><div>${linkCitations(r.text)}</div></div>`).join("") || '<p class="muted">None.</p>'}</div>
      </div>
      <div class="card"><div class="card-head"><h3>Hierarchical summaries</h3><span class="muted small">${d.levels.day.length} day · ${hours.length} hours · ${minutes.length} ten-minute windows</span></div>
        ${d.levels.day.map((day) => `<details class="node" open><summary><b>Day ${esc(day.period_start.slice(0, 10))}</b><span class="muted small">${day.frame_ids.length} cited frames</span></summary>
          <div class="body">${linkCitations(day.text.split("\n")[0])}</div>
          ${hours.filter((h) => h.period_start.slice(0, 10) === day.period_start.slice(0, 10)).map((h) => {
            const mins = minutes.filter((m) => m.period_start >= h.period_start && m.period_start < h.period_end);
            const quiet = !h.frame_ids.length;
            return `<details class="node"><summary><b class="mono">${hhmm(h.period_start)}–${hhmm(h.period_end) === "00:00" ? "24:00" : hhmm(h.period_end)}</b>
              <span class="${quiet ? "muted" : ""} small">${quiet ? "quiet hour" : esc(h.text.replace(/^\d\d:\d\d-\d\d:\d\d\s*/, "").slice(0, 120)) + "…"}</span></summary>
              <div class="body">${linkCitations(h.text)}</div>
              ${mins.map((m) => `<details class="node" data-start="${esc(m.period_start)}" data-end="${esc(m.period_end)}"><summary><span class="mono small">${hhmm(m.period_start)}–${hhmm(m.period_end)}</span>
                <span class="small ${m.frame_ids.length ? "" : "muted"}">${esc(m.text.replace(/^\d\d:\d\d-\d\d:\d\d\s*/, "").slice(0, 110))}</span></summary>
                <div class="body">${linkCitations(m.text)}</div><div class="frames small muted">Expand to load the raw frames…</div></details>`).join("")}
            </details>`;
          }).join("")}
        </details>`).join("") || '<p class="muted">No summaries stored.</p>'}
      </div>`;
    // lazily load the raw frames of a 10-minute window: the bottom of the pyramid
    el.querySelectorAll("details[data-start]").forEach((node) => node.addEventListener("toggle", async () => {
      const box = node.querySelector(".frames");
      if (!node.open || box.dataset.done) return;
      box.dataset.done = "1";
      box.innerHTML = '<span class="spinner"></span>';
      try {
        const r = await api(`/api/index/query?${new URLSearchParams({ start: node.dataset.start, end: node.dataset.end })}`);
        const rows = r.rows.filter((f) => f.ts < node.dataset.end);
        box.innerHTML = rows.map((f) => `<div>${flink(f.frame_id)} <span class="mono">${hhmm(f.ts)}</span> ${esc(zoneName(f.zone))}: ${esc(f.description)}</div>`).join("") || "No frames.";
      } catch (err) {
        box.innerHTML = `<span class="bad-text">${esc(err.message)}</span>`;
      }
    }));
  };
  await load();
  const btn = view.querySelector("#llm");
  if (btn) btn.onclick = () => load(true);
}
