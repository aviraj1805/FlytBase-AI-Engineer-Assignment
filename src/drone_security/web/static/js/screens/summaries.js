// Insights › Briefing: one-sentence summary, daily briefing, recommendations, and the day → hour → 10-minute → frame pyramid.
import { api, esc, failure, flink, hhmm, lead, linkCitations, loading, zoneName } from "../lib.js";
import { app } from "../app.js";

export const HELP = `Everything here is built from the frame index, so a summary cannot mention an event that is not stored.
  The <b>hierarchical summaries</b> are written while frames stream in: 10-minute windows → hours → day. Pick an hour to read it, open a
  10-minute window to load its raw frames. Every line keeps its [fN] citations (click them).
  <div class="req">Covers: bonus video summarisation (one sentence + daily briefing) and memory for long footage.</div>`;

let selected = null; // index of the hour being shown, kept across visits

export async function render(view) {
  const gemini = app.status && app.status.gemini.mode === "gemini";
  view.innerHTML = lead("What happened today, written from the frame index. Every statement cites the frames behind it.",
    gemini ? '<button class="btn btn-sm" id="llm">Rewrite the briefing with Gemini</button>' : "") + `<div id="sm">${loading()}</div>`;
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
    const peak = Math.max(1, ...hours.map((h) => h.frame_ids.length));
    if (selected == null || !hours[selected]) selected = hours.reduce((best, h, i) => (h.frame_ids.length > hours[best].frame_ids.length ? i : best), 0);
    el.innerHTML = `
      <div class="hero-quote mb"><h3>Today in one sentence</h3><p>${linkCitations(d.one_sentence)}</p></div>
      <div class="grid mb" style="grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); align-items:start">
        <div class="card"><div class="card-head"><h3>Daily security briefing</h3><span class="chip">written by: ${esc(d.briefing_by)}</span></div>
          <pre class="briefing">${linkCitations(d.briefing)}</pre></div>
        <div class="card"><h3>Recommendations</h3>${d.recommendations.map((r) => `<div class="reco"><span class="prio">${esc(r.priority)}</span><div>${linkCitations(r.text)}</div></div>`).join("") || '<p class="muted">None.</p>'}</div>
      </div>
      <div class="card">
        <div class="card-head"><h3>Hour by hour</h3><span class="muted small">${d.levels.day.length} day · ${hours.length} hours · ${minutes.length} ten-minute windows · pick an hour</span></div>
        ${hours.length ? `<div class="hours-grid" id="hours">${hours.map((h, i) => {
          const n = h.frame_ids.length;
          return `<button class="hour ${n ? "" : "quiet"} ${i === selected ? "on" : ""}" data-i="${i}" title="${n ? `${n} cited frames` : "quiet hour"}" style="--heat:${n / peak}">
            <span class="lvl" style="width:${(n / peak) * 100}%"></span><span class="h">${hhmm(h.period_start)}</span><span class="n">${n ? `${n} frames` : "quiet"}</span></button>`;
        }).join("")}</div>
        <div id="hour-detail" style="margin-top:16px"></div>` : '<p class="muted">No summaries stored.</p>'}
        ${d.levels.day.map((day) => `<details class="more" style="margin-top:14px"><summary>Whole-day summary, ${esc(day.period_start.slice(0, 10))}</summary>
          <div style="white-space:pre-wrap;font-size:13px;margin-top:8px">${linkCitations(day.text)}</div></details>`).join("")}
      </div>`;
    const grid = el.querySelector("#hours");
    if (!grid) return;
    grid.onclick = (e) => {
      const b = e.target.closest(".hour");
      if (!b) return;
      selected = Number(b.dataset.i);
      grid.querySelectorAll(".hour").forEach((x) => x.classList.toggle("on", x === b));
      showHour(el.querySelector("#hour-detail"), hours[selected], minutes);
    };
    showHour(el.querySelector("#hour-detail"), hours[selected], minutes);
  };
  await load();
  const btn = view.querySelector("#llm");
  if (btn) btn.onclick = () => load(true);
}

function showHour(el, h, minutes) {
  const end = hhmm(h.period_end) === "00:00" ? "24:00" : hhmm(h.period_end);
  const windows = minutes.filter((m) => m.period_start >= h.period_start && m.period_start < h.period_end);
  el.innerHTML = `<div class="row" style="margin-bottom:6px"><b class="mono">${hhmm(h.period_start)}–${end}</b>${h.frame_ids.length ? "" : '<span class="chip">quiet hour</span>'}</div>
    <div style="white-space:pre-wrap;font-size:13.5px">${linkCitations(h.text)}</div>
    <h3 style="margin:14px 0 4px">Ten-minute windows</h3>
    ${windows.map((m) => `<details class="node" data-start="${esc(m.period_start)}" data-end="${esc(m.period_end)}"><summary><span class="mono small">${hhmm(m.period_start)}–${hhmm(m.period_end)}</span>
      <span class="small ${m.frame_ids.length ? "" : "muted"}">${esc(m.text.replace(/^\d\d:\d\d-\d\d:\d\d\s*/, "").slice(0, 110))}</span></summary>
      <div class="body">${linkCitations(m.text)}</div><div class="frames muted">Loading the raw frames…</div></details>`).join("")}`;
  // the bottom of the pyramid: a window's raw frames, loaded when it is opened
  el.querySelectorAll("details[data-start]").forEach((node) => node.addEventListener("toggle", async () => {
    const box = node.querySelector(".frames");
    if (!node.open || box.dataset.done) return;
    box.dataset.done = "1";
    try {
      const r = await api(`/api/index/query?${new URLSearchParams({ start: node.dataset.start, end: node.dataset.end })}`);
      const rows = r.rows.filter((f) => f.ts < node.dataset.end);
      box.classList.remove("muted");
      box.innerHTML = rows.map((f) => `<div>${flink(f.frame_id)} <span class="mono">${hhmm(f.ts)}</span> ${esc(zoneName(f.zone))}: ${esc(f.description)}</div>`).join("") || "No frames.";
    } catch (err) {
      box.innerHTML = `<span class="bad-text">${esc(err.message)}</span>`;
    }
  }));
}
