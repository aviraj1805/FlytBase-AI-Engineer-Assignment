// Replay store: one live connection to /api/replay/stream shared by every screen.
// The server runs a fresh pipeline per connection; this file only keeps what it sent.
import { api } from "./lib.js";

const listeners = new Set();
export const replay = {
  frames: [],        // timeline: [{position, frame_id, ts}]
  markers: [],       // alert markers for the scrubber (from the processed day)
  position: 0,
  playing: false,
  speed: 600,        // footage seconds per real second
  current: null,     // last frame payload: {frame, detections, logs, alerts}
  trail: [],         // recent drone positions for the map
  alerts: [],        // alerts raised so far in this replay
  logs: [],          // event logs so far
  rawFirings: 0,
  started: false,
  error: null,
  ended: false,
};

let source = null;
let seq = 0; // ignore events from a stream we already replaced

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function emit(kind, data) { for (const fn of listeners) fn(kind, data); }

export async function loadTimeline() {
  if (replay.frames.length) return;
  const t = await api("/api/replay/timeline");
  replay.frames = t.frames;
  replay.markers = t.markers;
  emit("timeline");
}

function open(position, { once = false } = {}) {
  close();
  const my = ++seq;
  replay.error = null;
  replay.ended = false;
  source = new EventSource(`/api/replay/stream?start=${position}&speed=${replay.speed}`);
  source.addEventListener("snapshot", (e) => {
    if (my !== seq) return;
    const d = JSON.parse(e.data);
    replay.alerts = d.alerts;
    replay.logs = d.logs;
    replay.rawFirings = d.raw_rule_firings;
    replay.trail = [];
    if (d.last) setCurrent(d.last);
    emit("snapshot", d);
  });
  source.addEventListener("frame", (e) => {
    if (my !== seq) return;
    const d = JSON.parse(e.data);
    setCurrent(d);
    replay.alerts.push(...d.alerts);
    replay.logs.push(...d.logs);
    replay.rawFirings = d.raw_rule_firings;
    emit("frame", d);
    if (d.alerts.length) emit("alerts", d.alerts);
    if (once) { close(); replay.playing = false; emit("state"); }
  });
  source.addEventListener("end", () => {
    if (my !== seq) return;
    close();
    replay.playing = false;
    replay.ended = true;
    emit("state");
  });
  source.onerror = () => {
    if (my !== seq || !source) return;
    // EventSource would silently reconnect and restart from the old position; stop and say so instead
    close();
    replay.playing = false;
    replay.error = "Lost the connection to the replay stream. Is the server still running? Press play to resume.";
    emit("state");
  };
}

function close() {
  if (source) { source.close(); source = null; }
}

function setCurrent(d) {
  replay.current = d;
  replay.position = d.position;
  const t = d.frame.telemetry;
  if (t) {
    replay.trail.push([t.lat, t.lon]);
    if (replay.trail.length > 25) replay.trail.shift();
  }
}

export function play() {
  if (!replay.frames.length) return;
  let start = replay.current ? replay.position + 1 : 0;
  if (start >= replay.frames.length) start = 0; // at the end: start over
  replay.playing = true;
  replay.started = true;
  open(start);
  emit("state");
}

export function pause() {
  close();
  seq++;
  replay.playing = false;
  emit("state");
}

export function toggle() { replay.playing ? pause() : play(); }

// Jump to a position: re-runs the pipeline up to there on the server and shows that frame.
export function seek(position) {
  position = Math.max(0, Math.min(position, replay.frames.length - 1));
  replay.started = true;
  replay.position = position;
  open(position, { once: !replay.playing });
  emit("state");
}

export function setSpeed(speed) {
  replay.speed = speed;
  if (replay.playing) open(replay.position + 1);
  emit("state");
}

export function positionOfFrame(frameId) {
  return replay.frames.findIndex((f) => f.frame_id === frameId);
}
