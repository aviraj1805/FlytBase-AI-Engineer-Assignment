// QA / Tests: runs the real pytest suite in a subprocess and shows pass/fail per test group.
import { api, empty, esc, failure, fixed, loading, pageHead } from "../lib.js";

const HELP = `Runs <code>pytest</code> on the server (the same suite as <code>python make.py test</code>) and groups the JUnit results by
  test file: scenario & ground truth, ingest, perception, tracking, rules, index, search & memory, agent, Gemini (mocked), evaluation and this
  web API. Names come straight from the test functions (e.g. "loitering at night is high").<br><br>
  Covers: <b>testing</b>, with the test cases the assignment asks for (truck logged, alert at midnight, …).`;

export async function render(view) {
  view.innerHTML = pageHead("QA / Tests", HELP, `<button class="btn btn-accent" id="run">&#9654; Run the test suite</button>`) + `<div id="qa">${loading()}</div>`;
  const el = view.querySelector("#qa");
  const btn = view.querySelector("#run");
  const show = (r) => {
    if (!r || !r.ran_at) { el.innerHTML = empty("No test run yet in this session. Press “Run the test suite” (takes about 10–20 seconds)."); return; }
    if (r.error) { el.innerHTML = failure(r.error, "pytest did not produce a report"); return; }
    const t = r.total, all = t.passed + t.failed + t.skipped;
    el.innerHTML = `
      <div class="grid cols-4" style="margin-bottom:14px">
        <div class="card kpi"><span class="v ${r.ok ? "ok-text" : "bad-text"}">${r.ok ? "PASS" : "FAIL"}</span><span class="l">${esc(r.summary)}</span></div>
        <div class="card kpi"><span class="v ok-text">${t.passed}</span><span class="l">passed of ${all}</span></div>
        <div class="card kpi"><span class="v ${t.failed ? "bad-text" : ""}">${t.failed}</span><span class="l">failed</span></div>
        <div class="card kpi"><span class="v">${r.seconds}s</span><span class="l">ran at ${esc(r.ran_at)}</span></div>
      </div>
      <div class="grid cols-2" style="align-items:start">${r.groups.map((g) => `
        <details class="card test-group" ${g.failed ? "open" : ""}><summary>
          <span class="${g.failed ? "fail-dot" : "pass-dot"}"></span><b>${esc(g.name)}</b><span class="muted small mono">${esc(g.file)}</span>
          <span style="margin-left:auto" class="num small">${g.passed}/${g.tests.length}</span></summary>
          <div class="progress" style="margin:8px 0"><i style="width:${(g.passed / g.tests.length) * 100}%"></i></div>
          ${g.tests.map((x) => `<div class="test-row"><span class="${x.outcome === "passed" ? "pass-dot" : x.outcome === "failed" ? "fail-dot" : "skip-dot"}"></span>
            <span>${esc(x.title)}</span><span class="muted small" style="margin-left:auto">${fixed(x.seconds, 2)}s</span></div>
            ${x.message ? `<pre class="small bad-text" style="margin-left:17px">${esc(x.message)}</pre>` : ""}`).join("")}
        </details>`).join("")}</div>`;
  };
  try { show(await api("/api/tests/last")); } catch (err) { el.innerHTML = failure(err, "Could not load the last test run"); }
  btn.onclick = async () => {
    btn.disabled = true;
    el.innerHTML = loading("Running pytest on the server…");
    try { show(await api("/api/tests/run", { method: "POST" })); } catch (err) { el.innerHTML = failure(err, "Test run failed to start"); }
    btn.disabled = false;
  };
}
