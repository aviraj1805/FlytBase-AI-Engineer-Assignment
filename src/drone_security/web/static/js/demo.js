// Demo mode: a numbered walkthrough that drives the real views.
import { esc } from "./lib.js";
import { replay, loadTimeline, pause, play, seek, setSpeed } from "./replay.js";

const $ = (sel) => document.querySelector(sel);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(sel, ms = 8000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { const el = $(sel); if (el) return el; await sleep(80); }
  return null;
}

async function go(route, ready) {
  if (location.hash !== route) { location.hash = route; await sleep(60); } // let the router replace the old view first
  return waitFor(ready);
}

async function ask(question) {
  const input = await waitFor("#q");
  const send = $("#send");
  if (!input || !send) return;
  while (send.disabled) await sleep(100); // previous answer still streaming
  input.value = question;
  $("#composer").requestSubmit();
  await sleep(300);
  while ($("#send") && $("#send").disabled) await sleep(100);
}

const firstMarker = (severity) => replay.markers.find((m) => m.severity_name === severity);

// [title, explanation, action, element to highlight]
const STEPS = [
  ["The live console", "One simulated patrol day at a logistics yard: drone telemetry plus a caption for every video frame. Everything on screen comes from the real pipeline running on the server.",
    async () => { await go("#/live", "#tele"); }, "#map-card"],
  ["Replaying the night shift", "Press play: each frame is analysed, tracked, checked against the alert rules and indexed. At 00:01 a person loiters at the main gate, and the rule engine raises a HIGH alert because it is night.",
    async () => { await go("#/live", "#play"); await loadTimeline(); setSpeed(900); seek(0); await sleep(600); play(); }, "#player"],
  ["A critical intrusion at 02:14", "Jump to the first critical alert: a masked person climbing the back fence. The alert says exactly which rule conditions held and links its evidence frame.",
    async () => { await go("#/live", "#frame-panel"); pause(); await loadTimeline(); const m = firstMarker("CRITICAL"); if (m) seek(m.position); }, "#frame-panel"],
  ["Alerts, with the reason", "Every alert has a colour-coded severity, the rule that fired, the zone, a plain-English reason, the recommended action and the evidence frames. Filter by severity and acknowledge what you have handled.",
    async () => { const first = await go("#/live/alerts", "#al-list .alert-item"); if (first && !first.classList.contains("open")) first.click(); }, "#al-list"],
  ["The event log", "Each notable change is logged in the brief's format, for example 'Blue Ford F150 spotted at garage', with the perception confidence and how sure the tracker is that it is the same vehicle.",
    async () => { const q = await go("#/search/events", "#ev-q"); if (q) { q.value = "F150"; q.dispatchEvent(new Event("input")); } }, "#ev-table"],
  ["Show all truck events", "A structured query on the SQLite frame index. The histogram shows when the hits happened, and 'Show the SQL' reveals the statements SQLite actually executed.",
    async () => { await go("#/search?preset=0", "#qx-out .sql"); const d = $("#qx-out details"); if (d) d.open = true; }, "#qx-out"],
  ["Main gate, midnight to 1 am", "Zone plus time window, the second example from the brief. The filters show as pills under the search bar; remove one and the query runs again.",
    async () => { await go("#/search?preset=1", "#qx-out .sql"); }, "#qx-out"],
  ["Ask the agent", "Questions go to the analyst agent. It shows how it found the answer, cites frames you can click, and every citation is checked against what its tools returned. The follow-up 'When did it leave?' keeps context.",
    async () => { await go("#/ask", "#q"); await ask("Did the same truck enter twice?"); await ask("When did it leave?"); }, "#msgs"],
  ["It says when it does not know", "The footage never records licence plates, so the agent answers 'not enough evidence' instead of guessing.",
    async () => { await go("#/ask", "#q"); await ask("What was the licence plate of the black SUV?"); }, "#msgs"],
  ["Identities", "The tracker re-identifies each vehicle and person across the day: visits on a 24-hour bar, the match confidence, and why it matched: colour, make and model agreed.",
    async () => { await go("#/insights/identities", "#detail .trait"); }, "#detail"],
  ["The daily briefing", "A one-sentence summary and the daily briefing, written from the index with frame citations. The hour grid drills down from the day to hours, ten-minute windows and raw frames.",
    async () => { await go("#/insights", ".hero-quote"); }, "#sm"],
  ["Memory for long footage", "What the agent's prompt holds: hour summaries plus a rolling window, inside a token budget, against pasting every frame. Drag the budget to watch it fill in priority order.",
    async () => { await go("#/insights/memory", ".ctx-bar"); }, "#cx"],
  ["The test suite", "The real pytest suite, run from the UI and grouped by area, including the brief's own examples: a truck logged correctly and an alert triggered at midnight.",
    async () => { const run = await go("#/proof/tests", "#run"); if (run && !run.disabled) run.click(); }, "#qa"],
  ["Measured results", "Scored against a ground truth the system never reads, plus robustness and scale tests up to 200,000 frames. Every chart comes from a saved run in docs/results.",
    async () => { await go("#/proof", "#c-prf"); }, "#ev"],
  ["Architecture", "How it fits together: ingest, perception, tracker, rules, index, memory and the agent, with this UI as a thin layer on top.",
    async () => { await go("#/proof/architecture", ".arch img"); }, "#ar"],
];

let step = -1;
const box = document.getElementById("demo");

export function startDemo() {
  step = -1;
  box.hidden = false;
  next(1);
}

function stop() {
  box.hidden = true;
  document.querySelectorAll(".demo-highlight").forEach((e) => e.classList.remove("demo-highlight"));
}

async function next(delta) {
  step = Math.max(0, Math.min(STEPS.length - 1, step + delta));
  const [title, say, action, highlight] = STEPS[step];
  box.innerHTML = `<div class="step">Step ${step + 1} of ${STEPS.length}<div class="progress"><i style="width:${((step + 1) / STEPS.length) * 100}%"></i></div></div>
    <h2>${esc(title)}</h2><p class="say">${esc(say)}</p>
    <div class="row"><button class="btn btn-sm" id="d-prev" ${step === 0 ? "disabled" : ""}>&larr; Back</button>
    <button class="btn btn-sm btn-accent" id="d-next">${step === STEPS.length - 1 ? "Finish" : "Next &rarr;"}</button>
    <span class="muted small" style="margin-left:auto">&larr; &rarr; keys · Esc exits</span><button class="btn btn-sm btn-ghost" id="d-stop">Exit</button></div>`;
  box.querySelector("#d-prev").onclick = () => next(-1);
  box.querySelector("#d-next").onclick = () => (step === STEPS.length - 1 ? stop() : next(1));
  box.querySelector("#d-stop").onclick = stop;
  document.querySelectorAll(".demo-highlight").forEach((e) => e.classList.remove("demo-highlight"));
  const mine = step;
  try { await action(); } catch (err) { console.warn("demo step", err); }
  if (mine !== step) return;
  const el = await waitFor(highlight, 4000);
  if (el && mine === step) { el.classList.add("demo-highlight"); el.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
}

// Registered before the app's and the views' key handlers (this module loads first), so while the tour is open
// the arrow keys move the tour instead of stepping the replay.
document.addEventListener("keydown", (e) => {
  if (box.hidden || e.target.closest("input, textarea, select")) return;
  const moves = { ArrowRight: 1, PageDown: 1, ArrowLeft: -1, PageUp: -1 };
  if (e.key in moves) { e.preventDefault(); e.stopImmediatePropagation(); next(moves[e.key]); }
  if (e.key === "Escape") stop();
});
