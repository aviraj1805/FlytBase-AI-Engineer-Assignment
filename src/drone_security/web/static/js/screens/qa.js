// Proof › Tests: runs the real pytest suite in a subprocess and shows pass/fail per test group.
import { api, countUp, esc, failure, fixed, lead, loading, num } from "../lib.js";

export const HELP = `Runs <code>pytest</code> on the server (the same suite as <code>python make.py test</code>) and groups the JUnit results by
  test file: scenario and ground truth, ingest, perception, tracking, rules, index, search and memory, agent, Gemini (mocked), evaluation and
  this web API. Names come straight from the test functions (e.g. "loitering at night is high").
  <div class="req">Covers: testing, including the cases the assignment asks for (truck logged correctly, alert triggered at midnight).</div>`;

export async function render(view) {
  view.innerHTML = lead("Run the project's real test suite on the server and see every test, grouped by what it checks.",
    '<button class="btn btn-accent" id="run">&#9654; Run the test suite</button>') + `<div id="qa">${loading()}</div>`;
  const el = view.querySelector("#qa");
  const btn = view.querySelector("#run");
  const run = async () => {
    btn.disabled = true;
    el.innerHTML = loading("Running pytest on the server (about 10 to 20 seconds)…");
    try { show(await api("/api/tests/run", { method: "POST" })); } catch (err) { el.innerHTML = failure(err, "Test run failed to start"); }
    btn.disabled = false;
  };
  const show = (r) => {
    if (!r || !r.ran_at) {
      el.innerHTML = `<div class="state">No test run yet in this session.<div style="margin-top:12px"><button class="btn btn-accent" id="run-empty">&#9654; Run the test suite</button></div></div>`;
      el.querySelector("#run-empty").onclick = run;
      return;
    }
    if (r.error) { el.innerHTML = failure(r.error, "pytest did not produce a report"); return; }
    const t = r.total, all = t.passed + t.failed + t.skipped;
    el.innerHTML = `
      <div class="card mb test-summary">
        <div class="ring ${t.failed ? "fail" : ""}" style="--p:${all ? (t.passed / all) * 100 : 0}"><div><b class="${r.ok ? "ok-text" : "bad-text"}">${r.ok ? "PASS" : "FAIL"}</b><span>${t.passed} / ${all}</span></div></div>
        <div class="grid cols-3" style="flex:1;min-width:260px">
          <div class="kpi plain"><span class="v ok-text">${num(t.passed)}</span><span class="l">passed</span></div>
          <div class="kpi plain"><span class="v ${t.failed ? "bad-text" : ""}">${num(t.failed)}</span><span class="l">failed${t.skipped ? ` · ${t.skipped} skipped` : ""}</span></div>
          <div class="kpi plain"><span class="v">${num(r.seconds, 1, " s")}</span><span class="l">ran at ${esc(r.ran_at)}</span></div>
        </div>
        <div class="muted small" style="flex-basis:100%">${esc(r.summary)} · click a group to see its tests</div>
      </div>
      <div class="grid cols-2" style="align-items:start">${r.groups.map((g) => `
        <details class="card test-group" ${g.failed ? "open" : ""}><summary>
          <div class="row" style="flex-wrap:nowrap"><span class="${g.failed ? "fail-dot" : "pass-dot"}"></span><b>${esc(g.name)}</b><span class="muted small mono">${esc(g.file)}</span>
            <span style="margin-left:auto" class="num small">${g.passed}/${g.tests.length}</span></div>
          <div class="progress" style="margin-top:8px"><i style="width:${(g.passed / g.tests.length) * 100}%"></i></div></summary>
          <div style="margin-top:8px">${g.tests.map((x) => `<div class="test-row"><span class="${x.outcome === "passed" ? "pass-dot" : x.outcome === "failed" ? "fail-dot" : "skip-dot"}"></span>
            <span>${esc(x.title)}</span><span class="muted small" style="margin-left:auto">${fixed(x.seconds, 2)}s</span></div>
            ${x.message ? `<pre class="small bad-text" style="margin-left:17px">${esc(x.message)}</pre>` : ""}`).join("")}</div>
        </details>`).join("")}</div>`;
    countUp(el);
  };
  try { show(await api("/api/tests/last")); } catch (err) { el.innerHTML = failure(err, "Could not load the last test run"); }
  btn.onclick = run;
}
