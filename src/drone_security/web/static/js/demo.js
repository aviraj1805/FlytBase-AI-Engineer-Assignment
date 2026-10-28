// Demo mode: a numbered walkthrough (matches docs/DEMO_SCRIPT.md, "UI walkthrough") that drives the real screens.
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
  if (location.hash !== route) location.hash = route;
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

// [title, what to say, action, element to highlight]
export const STEPS = [
  ["Mission Control", "One simulated patrol day at a logistics yard: telemetry plus a caption per frame. Everything you see is produced by the real pipeline as frames stream from the server.",
    async () => { await go("#/mission", "#tele"); }, "#tele"],
  ["Live replay of the night shift", "Press play: each frame goes through perception, tracking, the rule engine and the index. At 00:01 a person loiters at the main gate: a HIGH alert, because it is night.",
    async () => { await go("#/mission", "#play"); await loadTimeline(); setSpeed(900); seek(0); await sleep(600); play(); }, "#controls-card"],
  ["A critical intrusion at 02:14", "Jump to the first critical alert: a masked person climbing the back fence. The alert explains exactly which rule conditions held, and cites its evidence frame.",
    async () => { await go("#/mission", "#frame-panel"); pause(); await loadTimeline(); const m = firstMarker("CRITICAL"); if (m) seek(m.position); }, "#frame-panel"],
  ["Live alerts", "The alert feed: colour-coded severity, rule id, zone, the plain-English why, the recommended action and evidence frames. Filter by severity, rule or zone; acknowledge what you have handled.",
    async () => { await go("#/alerts", "#al-list"); }, "#al-list"],
  ["Event log", "Every notable change is logged in the brief's format, 'Blue Ford F150 spotted at garage, 09:16', with the perception and re-identification confidence. Click a row to open the frame.",
    async () => { await go("#/events", "#ev-q"); const q = $("#ev-q"); q.value = "F150"; q.dispatchEvent(new Event("input")); }, "#ev-table"],
  ["Frame index: show all truck events", "A structured query on the SQLite index. The panel on the right shows the SQL SQLite actually executed. The F150 and the Silverado are two different entities.",
    async () => { await go("#/explorer?preset=0", "#qx-out"); await waitFor(".sql"); }, "#qx-out"],
  ["Frame index: main gate, 00:00 to 01:00", "Time plus zone, the second example from the brief. The histogram shows where the hits fall in the day.",
    async () => { await go("#/explorer?preset=1", "#qx"); }, "#qx-out"],
  ["Agent chat", "Questions go to the analyst agent. It shows which tools it called, cites frames you can click, and every citation is checked against what the tools returned. The follow-up 'When did it leave?' keeps context.",
    async () => { await go("#/chat", "#q"); await ask("Did the same truck enter twice?"); await ask("When did it leave?"); }, "#msgs"],
  ["It says when it does not know", "The footage never records licence plates, so the agent answers 'not enough evidence' instead of guessing.",
    async () => { await go("#/chat", "#q"); await ask("What was the licence plate of the black SUV?"); }, "#msgs"],
  ["Entity tracker", "Each re-identified identity with its visits over the day, the match confidence, and why it was matched: colour, make and model agreed.",
    async () => { await go("#/entities", ".entity-card"); }, "#detail"],
  ["Summaries", "The one-sentence summary, the daily briefing, and the hierarchy: day, hours, ten-minute windows, down to the raw frames.",
    async () => { await go("#/summaries", "details.node"); }, "#sm"],
  ["Context management", "What the agent's prompt holds: hourly summaries plus a rolling window, inside a token budget, against pasting every frame. Drag the budget to see it fill in priority order.",
    async () => { await go("#/context", ".ctx-bar"); }, ".ctx-bar"],
  ["Tests", "The real pytest suite, run from the UI and grouped by area, including the brief's own examples: truck logged correctly, alert triggered at midnight.",
    async () => { await go("#/qa", "#run"); $("#run").click(); }, "#qa"],
  ["Evaluation and scale", "Scored against a ground truth the system never reads, plus the saved benchmark up to 200,000 frames. All charts come from docs/results.",
    async () => { await go("#/evaluation", "#c-prf"); }, "#ev"],
  ["Architecture", "How it fits together: ingest, perception, tracker, rules, index, memory and the agent, with this UI as a thin layer on top.",
    async () => { await go("#/architecture", ".arch"); }, "#ar"],
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
  box.innerHTML = `<div class="step">Step ${step + 1} of ${STEPS.length}</div><h2>${esc(title)}</h2><p class="say">${esc(say)}</p>
    <div class="row"><button class="btn btn-sm" id="d-prev" ${step === 0 ? "disabled" : ""}>&larr; Back</button>
    <button class="btn btn-sm btn-accent" id="d-next">${step === STEPS.length - 1 ? "Finish" : "Next &rarr;"}</button>
    <span class="muted small" style="margin-left:auto">&larr;/&rarr; keys · Esc to exit</span><button class="btn btn-sm btn-ghost" id="d-stop">Exit</button></div>`;
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

document.addEventListener("keydown", (e) => {
  if (box.hidden || e.target.closest("input, textarea, select")) return;
  if (e.key === "ArrowRight" && !location.hash.startsWith("#/mission")) next(1);
  if (e.key === "ArrowLeft" && !location.hash.startsWith("#/mission")) next(-1);
  if (e.key === "PageDown") next(1);
  if (e.key === "PageUp") next(-1);
  if (e.key === "Escape") stop();
});
