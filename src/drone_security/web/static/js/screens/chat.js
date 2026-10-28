// Agent Chat: follow-up Q&A with the analyst agent. Shows tool calls and checks every [fN] citation.
import { esc, linkCitations, pageHead, readSSE } from "../lib.js";
import { app } from "../app.js";

const HELP = `Ask the analyst agent about the footage. It answers through five tools over the index (<code>query_frames</code>,
  <code>entity_history</code>, <code>list_alerts</code>, <code>summarize_window</code>, <code>drone_status</code>), cites frames as [fN]
  (click them), and says <b>"Not enough evidence"</b> instead of guessing. After each answer the citations are checked against the frames
  the tools actually returned. Follow-ups ("When did it leave?") keep context.<br><br>
  Covers: <b>bonus Q&A agent</b> and grounded answers. Offline it uses the deterministic analyst; with GEMINI_API_KEY the LangChain + Gemini agent.`;

export const SUGGESTIONS = [
  "What objects were in the video?",
  "Did the same truck enter twice?",
  "When did it leave?",
  "Was anyone loitering at night?",
  "What happened between 02:00 and 03:00?",
  "Was the blue Chevrolet Silverado the same vehicle as the blue Ford F150?",
  "What was the drone's lowest battery level?",
  "What was the licence plate of the black SUV?",
];

const history = []; // survives navigation: [{role, text, tools, grounding, mode, seconds, error}]
let busy = false;

export async function render(view) {
  const mode = app.status ? (app.status.gemini.mode === "gemini" ? `Gemini agent (${app.status.gemini.model})` : "offline analyst") : "";
  view.innerHTML = pageHead("Agent Chat", HELP, `<span class="chip">${esc(mode)}</span><button class="btn btn-sm" id="reset">New conversation</button>`) + `
    <div class="chat">
      <div class="messages" id="msgs"></div>
      <div class="stack">
        <div class="row" id="sugg">${SUGGESTIONS.map((s) => `<button class="chip" data-q="${esc(s)}">${esc(s)}</button>`).join("")}</div>
        <form class="composer" id="composer"><input id="q" placeholder="Ask about the footage, e.g. How many times did the blue Ford F150 enter?" autocomplete="off" aria-label="Question">
          <button class="btn btn-accent" type="submit" id="send">Ask</button></form>
      </div>
    </div>`;
  const msgs = view.querySelector("#msgs");
  const draw = () => {
    msgs.innerHTML = history.length ? history.map(message).join("") :
      `<div class="state">Ask a question or pick a suggestion. Answers cite frames like ${linkCitations("[f67]")}; click one to open the frame.</div>`;
    msgs.scrollTop = msgs.scrollHeight;
  };
  draw();
  const ask = (question) => send(question, draw, view);
  view.querySelector("#composer").onsubmit = (e) => { e.preventDefault(); const q = view.querySelector("#q"); if (q.value.trim()) { ask(q.value.trim()); q.value = ""; } };
  view.querySelector("#sugg").onclick = (e) => { const b = e.target.closest("[data-q]"); if (b) ask(b.dataset.q); };
  view.querySelector("#reset").onclick = async () => {
    await fetch("/api/chat/reset", { method: "POST" });
    history.length = 0;
    draw();
  };
  view.querySelector("#q").focus();
  return () => {};
}

export async function send(question, draw, view) {
  if (busy) return;
  busy = true;
  if (view) view.querySelector("#send").disabled = true;
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
      draw();
    });
    if (bot.pending) { bot.pending = false; bot.error = bot.error || "The answer stream ended early."; }
  } catch (err) {
    bot.pending = false;
    bot.error = `Could not reach the agent: ${err.message}`;
  } finally {
    busy = false;
    if (view && view.isConnected) view.querySelector("#send").disabled = false;
    draw();
  }
}

function message(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.text)}</div>`;
  const tools = m.tools == null ? "" : `<div class="tools">${m.tools.length ? m.tools.map((c) =>
    `<span class="tool-call"><b>${esc(c.name)}</b>(${esc(Object.entries(c.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</span>`).join("")
    : '<span class="tool-call">no tool call (answered from the question itself)</span>'}</div>`;
  const g = m.grounding;
  let check = "";
  if (g) {
    if (g.abstained) check = '<span class="warn-text">&#9888; Abstained: not enough evidence in the footage</span>';
    else if (g.cited.length && !g.unsupported.length) check = `<span class="ok-text">&#10003; ${g.cited.length} cited frame${g.cited.length > 1 ? "s" : ""}, all returned by the tools</span>`;
    else if (g.unsupported.length) check = `<span class="bad-text">&#10007; Unsupported citations: ${g.unsupported.map((i) => `f${i}`).join(", ")}</span>`;
    else check = '<span class="warn-text">No frame citations in this answer</span>';
  }
  return `<div class="msg bot">
    ${tools}
    ${m.error ? `<div class="bad-text">${esc(m.error)}</div>` : ""}
    <div class="text">${linkCitations(m.text)}${m.pending ? ' <span class="spinner"></span>' : ""}</div>
    ${g ? `<div class="grounding">${check}<span class="muted">· ${esc(m.mode)} · ${m.seconds}s</span>${m.llmError ? `<span class="warn-text">· Gemini fell back: ${esc(m.llmError)}</span>` : ""}</div>` : ""}
  </div>`;
}
