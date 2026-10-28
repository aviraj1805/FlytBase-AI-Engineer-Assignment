// Ask AI: follow-up Q&A with the analyst agent. Tool calls sit under "How I found this"; every [fN] citation is checked.
import { esc, linkCitations, readSSE } from "../lib.js";
import { app } from "../app.js";

export const HELP = `Ask the analyst agent about the footage. It answers through five tools over the index (<code>query_frames</code>,
  <code>entity_history</code>, <code>list_alerts</code>, <code>summarize_window</code>, <code>drone_status</code>), cites frames as [fN]
  (click them), and says <b>"Not enough evidence"</b> instead of guessing. After each answer the citations are checked against the frames
  the tools actually returned. Follow-ups ("When did it leave?") keep context. Offline it uses the deterministic analyst; with
  GEMINI_API_KEY the LangChain + Gemini agent.
  <div class="req">Covers: the bonus Q&A agent, grounded answers with citations.</div>`;

// Starters on the empty screen, each with what it shows off.
const STARTERS = [
  ["What objects were in the video?", "an inventory of the whole day"],
  ["Did the same truck enter twice?", "re-identification across visits"],
  ["Was anyone loitering at night?", "rules plus time of day"],
  ["What was the licence plate of the black SUV?", "it says when it does not know"],
];
const SUGGESTIONS = [
  "When did it leave?",
  "What happened between 02:00 and 03:00?",
  "Was the blue Chevrolet Silverado the same vehicle as the blue Ford F150?",
  "What was the drone's lowest battery level?",
  ...STARTERS.map(([q]) => q),
];
const SPARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>';

const history = []; // survives navigation: [{role, text, tools, grounding, mode, seconds, error, shown, showTools}]
let busy = false;

export async function render(view) {
  const g = app.status && app.status.gemini;
  const mode = g ? (g.mode === "gemini" ? `Answering with the Gemini agent (${g.model})` : "Answering with the offline analyst (deterministic)") : "";
  view.innerHTML = `
    <div class="chat">
      <div class="messages" id="msgs"></div>
      <div class="stack" style="gap:8px">
        <div class="sugg-row" id="sugg"></div>
        <form class="composer" id="composer"><input id="q" placeholder="Ask about the footage, e.g. How many times did the blue Ford F150 enter?" autocomplete="off" aria-label="Question">
          <button class="btn btn-accent" type="submit" id="send">Ask</button></form>
        <div class="row small muted"><span>${esc(mode)}</span><span class="spacer"></span><button class="btn btn-ghost btn-sm" id="reset">&#8634; New conversation</button></div>
      </div>
    </div>`;
  const msgs = view.querySelector("#msgs");
  const sugg = view.querySelector("#sugg");
  const draw = () => {
    msgs.innerHTML = history.length ? history.map(message).join("") : `
      <div class="chat-empty"><div class="orb">${SPARK}</div><h2>Ask anything about the footage</h2>
        <p>The agent searches the index with five tools, cites the frames it used (click them), and says “not enough evidence” instead of guessing.</p>
        <div class="starters">${STARTERS.map(([q, hint]) => `<button class="starter" data-q="${esc(q)}">${esc(q)}<span>${esc(hint)}</span></button>`).join("")}</div></div>`;
    sugg.innerHTML = history.length ? SUGGESTIONS.map((s) => `<button class="chip" data-q="${esc(s)}">${esc(s)}</button>`).join("") : "";
    msgs.scrollTop = msgs.scrollHeight;
  };
  draw();
  const ask = (question) => send(question, draw, view);
  view.querySelector("#composer").onsubmit = (e) => {
    e.preventDefault();
    const q = view.querySelector("#q");
    if (q.value.trim()) { ask(q.value.trim()); q.value = ""; }
  };
  const pick = (e) => { const b = e.target.closest("[data-q]"); if (b) ask(b.dataset.q); };
  sugg.onclick = pick;
  msgs.onclick = (e) => {
    const s = e.target.closest("summary[data-i]");
    if (s) history[Number(s.dataset.i)].showTools = !s.parentElement.open; // click fires before the toggle
    else pick(e);
  };
  view.querySelector("#reset").onclick = async () => {
    await fetch("/api/chat/reset", { method: "POST" });
    history.length = 0;
    draw();
  };
  view.querySelector("#q").focus();
}

async function send(question, draw, view) {
  if (busy) return;
  busy = true;
  view.querySelector("#send").disabled = true;
  history.push({ role: "user", text: question });
  const bot = { role: "bot", text: "", tools: null, grounding: null, pending: true };
  history.push(bot);
  draw();
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    await readSSE(res, (event, data) => {
      if (event === "tools") { bot.tools = data.calls; bot.mode = data.mode; bot.llmError = data.llm_error; }
      if (event === "token") bot.text += data.text;
      if (event === "done") { bot.text = data.text; bot.grounding = data.grounding; bot.seconds = data.seconds; bot.pending = false; }
      if (event === "error") { bot.error = data.text; bot.pending = false; }
      if (view.isConnected) draw();
    });
    if (bot.pending) { bot.pending = false; bot.error = bot.error || "The answer stream ended early."; }
  } catch (err) {
    bot.pending = false;
    bot.error = `Could not reach the agent: ${err.message}`;
  } finally {
    busy = false;
    if (view.isConnected) { view.querySelector("#send").disabled = false; draw(); }
  }
}

function message(m, i) {
  const enter = m.shown ? "" : "enter"; // animate a message once, not on every streamed token
  m.shown = true;
  if (m.role === "user") return `<div class="msg user ${enter}">${esc(m.text)}</div>`;
  const tools = m.tools == null ? "" : `<details class="more" style="margin-top:10px" ${m.showTools ? "open" : ""}><summary data-i="${i}">How I found this · ${
    m.tools.length ? `${m.tools.length} tool call${m.tools.length > 1 ? "s" : ""}` : "no tool call"}</summary>
    <div class="tools">${m.tools.length ? m.tools.map((c) =>
      `<span class="tool-call"><b>${esc(c.name)}</b>(${esc(Object.entries(c.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</span>`).join("")
      : '<span class="tool-call">answered from the question itself</span>'}</div></details>`;
  const g = m.grounding;
  let verdict = "";
  if (g) {
    if (g.abstained) verdict = '<span class="verdict warn">&#9888; Not enough evidence in the footage, so it did not guess</span>';
    else if (g.cited.length && !g.unsupported.length) verdict = `<span class="verdict ok">&#10003; ${g.cited.length} citation${g.cited.length > 1 ? "s" : ""} checked against the tool results</span>`;
    else if (g.unsupported.length) verdict = `<span class="verdict bad">&#10007; Unsupported citations: ${g.unsupported.map((x) => `f${x}`).join(", ")}</span>`;
    else verdict = '<span class="verdict warn">No frame citations in this answer</span>';
  }
  const waiting = m.pending ? (m.text ? ' <span class="spinner"></span>' : '<span class="typing"><i></i><i></i><i></i></span>') : "";
  return `<div class="msg bot ${enter}"><div class="avatar">${SPARK}</div><div class="bubble">
    ${m.error ? `<div class="bad-text">${esc(m.error)}</div>` : ""}
    <div class="text">${linkCitations(m.text)}${waiting}</div>
    ${g ? `<div class="grounding">${verdict}<span class="muted">${esc(m.mode)} · ${m.seconds}s</span>${m.llmError ? `<span class="warn-text">Gemini fell back: ${esc(m.llmError)}</span>` : ""}</div>` : ""}
    ${tools}</div></div>`;
}
