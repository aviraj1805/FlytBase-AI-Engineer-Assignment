// Small shared helpers: API calls, HTML escaping, formatting and the UI states every screen uses.

export async function api(path, options = {}) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) throw new Error((body && body.detail) ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : `${res.status} ${res.statusText}`);
  return body;
}

// Read a text/event-stream response from fetch (EventSource cannot POST): calls onEvent(name, data).
export async function readSSE(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const name = (block.match(/^event: (.*)$/m) || [])[1] || "message";
      const data = (block.match(/^data: (.*)$/m) || [])[1];
      if (data) onEvent(name, JSON.parse(data));
    }
  }
}

export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const hhmm = (ts) => (ts ? ts.slice(11, 16) : "");
export const fixed = (v, d = 1) => (v == null ? "–" : Number(v).toFixed(d));
export const zoneName = (id) => (id ? id.replace(/_/g, " ") : "–");
export const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

export const sev = (name) => `<span class="sev sev-${esc(name)}">${esc(name)}</span>`;
export const cat = (name) => `<span class="cat cat-${esc(name)}" title="${esc(name)}"></span>`;
export const flink = (id) => `<button class="flink" data-frame="${Number(id)}" title="Open frame ${Number(id)}">f${Number(id)}</button>`;

// Turn "[f123]" citations in text into clickable frame links (text is escaped first).
export const linkCitations = (text) => esc(text).replace(/\[f(\d+)\]/g, (_, id) => flink(id));

export const loading = (label = "Loading…") => `<div class="state"><span class="spinner"></span> ${esc(label)}</div>`;
export const empty = (label) => `<div class="state">${esc(label)}</div>`;
export const failure = (err, what = "Request failed") => `<div class="state error"><b>${esc(what)}.</b> ${esc(err && err.message ? err.message : err)}</div>`;

// First line of a view: what it shows in plain words, with optional actions on the right.
export const lead = (text, extra = "") => `<div class="lead"><p>${text}</p>${extra ? `<div class="lead-actions">${extra}</div>` : ""}</div>`;

// A number that counts up when it appears; it is rendered with its final value, so it is right without JS animation.
export function num(value, dec = 0, suffix = "") {
  if (value == null) return "–";
  return `<span data-count="${Number(value)}" data-dec="${dec}" data-suffix="${esc(suffix)}">${formatNum(value, dec)}${esc(suffix)}</span>`;
}
const formatNum = (v, dec) => Number(v).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });

export function countUp(root) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  root.querySelectorAll("[data-count]").forEach((el) => {
    const target = Number(el.dataset.count), dec = Number(el.dataset.dec), suffix = el.dataset.suffix;
    const t0 = performance.now();
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / 700);
      el.textContent = formatNum(target * (1 - (1 - k) ** 3), dec) + suffix;
      if (k < 1 && el.isConnected) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

// Small confidence meter: bar plus the value; amber below 0.7.
export const meter = (v) => (v == null ? "–" : `<span class="meter ${v < 0.7 ? "low" : ""}" style="--v:${Math.round(v * 100)}%"><i></i>${fixed(v, 2)}</span>`);

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const SEV_COLOR = () => ({
  CRITICAL: cssVar("--sev-critical"), HIGH: cssVar("--sev-high"), MEDIUM: cssVar("--sev-medium"),
  LOW: cssVar("--sev-low"), INFO: cssVar("--sev-info"),
});

// Chart.js defaults that follow the theme; charts are destroyed on navigation (see app.js).
export const charts = [];
export function chart(canvas, config) {
  const Chart = window.Chart;
  if (!Chart) { canvas.replaceWith(Object.assign(document.createElement("div"), { className: "state error", textContent: "Chart.js failed to load." })); return null; }
  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--border");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.datasets.bar.maxBarThickness = 72;
  const c = new Chart(canvas, { ...config, options: { responsive: true, maintainAspectRatio: false, animation: { duration: 500 }, ...(config.options || {}) } });
  charts.push(c);
  return c;
}
