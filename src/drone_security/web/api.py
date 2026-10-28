"""FastAPI layer over the existing modules. Routes only translate HTTP to existing calls.

Run with `python run_ui.py` (or `make ui`). Every number the UI shows comes from these
endpoints: the processed day in data/runs/ui.db, a live pipeline (replay), or the saved
experiment outputs in docs/results.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import PROJECT_ROOT
from ..memory import estimate_tokens, render_from_index, render_naive
from ..tracking import _PERSON_WEIGHTS, _VEHICLE_WEIGHTS, ACCEPT_SCORE
from .replay import replay_stream, sse
from .state import AppState

STATIC = Path(__file__).resolve().parent / "static"
RESULTS = PROJECT_ROOT / "docs" / "results"
DIAGRAMS = PROJECT_ROOT / "docs" / "diagrams"
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


class Question(BaseModel):
    question: str


def create_app(state: Optional[AppState] = None) -> FastAPI:
    app = FastAPI(title="Drone Security Analyst UI", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.ds = state  # built lazily so importing this module stays cheap

    def ds() -> AppState:
        if app.state.ds is None:
            app.state.ds = AppState()
        return app.state.ds

    # ------------------------------------------------------------------ overview
    @app.get("/api/status")
    def status():
        s = ds()
        stats = s.index.stats()
        run = s.run_stats
        return {
            "property": s.config.property_name, "drone_id": s.config.drone_id, "feed": s.feed.name,
            "gemini": s.gemini_status(), "index": stats,
            "run": {"frames": run.frames, "detections": run.detections, "logs": run.logs, "alerts": run.alerts,
                    "raw_rule_firings": run.raw_rule_firings, "suppressed_alerts": run.suppressed_alerts,
                    "fps": round(run.fps), "latency_p50_ms": round(run.latency_ms(50), 3),
                    "latency_p99_ms": round(run.latency_ms(99), 3),
                    "duplicates": run.ingest.duplicates if run.ingest else 0,
                    "missing_telemetry": run.ingest.missing_telemetry if run.ingest else 0},
        }

    @app.get("/api/property")
    def property_profile():
        c = ds().config
        return {"name": c.property_name, "home": {"lat": c.home_lat, "lon": c.home_lon},
                "geofence_radius_m": c.geofence_radius_m,
                "night": [c.night.start.strftime("%H:%M"), c.night.end.strftime("%H:%M")],
                "business": [c.business.start.strftime("%H:%M"), c.business.end.strftime("%H:%M")],
                "zones": [z.__dict__ for z in c.zones], "known_vehicles": c.known_vehicles}

    # ------------------------------------------------------------------ replay
    @app.get("/api/replay/timeline")
    def replay_timeline():
        s = ds()
        markers = [{"position": s.position_of[a["frame_id"]], "frame_id": a["frame_id"], "ts": a["ts"],
                    "severity_name": a["severity_name"], "rule_id": a["rule_id"], "message": a["message"]}
                   for a in s.index.alerts(limit=10_000) if a["frame_id"] in s.position_of]
        return {"frames": [{"position": i, "frame_id": f.frame_id, "ts": f.timestamp.strftime("%Y-%m-%d %H:%M:%S")}
                           for i, f in enumerate(s.frames)],
                "markers": markers}

    @app.get("/api/replay/stream")
    async def replay(request: Request, start: int = 0, speed: float = 600):
        s = ds()
        return StreamingResponse(replay_stream(s, start, speed, request.is_disconnected),
                                 media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------------ alerts / events / frames
    @app.get("/api/alerts")
    def alerts(min_severity: Optional[str] = None, rule_id: Optional[str] = None, zone: Optional[str] = None):
        return {"alerts": ds().index.alerts(min_severity=min_severity or None, rule_id=rule_id or None,
                                            zone=zone or None, limit=10_000)}

    @app.get("/api/events")
    def events():
        s = ds()
        # event logs carry no confidence; take it from the detection of the same entity in the same frame
        rows = s.index._all(
            "SELECT e.id, e.ts, e.frame_id, e.entity_id, e.zone, e.kind, e.message, d.display_name, d.category, "
            "d.label, MAX(d.confidence) AS confidence, MAX(d.track_confidence) AS track_confidence "
            "FROM events e LEFT JOIN detections d ON d.frame_id = e.frame_id AND d.entity_id = e.entity_id "
            "GROUP BY e.id ORDER BY e.ts, e.id")
        return {"events": rows}

    @app.get("/api/frames/{frame_id}")
    def frame(frame_id: int):
        s = ds()
        rows = s.index.query_frames(frame_ids=[frame_id], limit=1)
        if not rows:
            raise HTTPException(404, f"frame {frame_id} is not in the index")
        dets = s.index._all("SELECT * FROM detections WHERE frame_id = ? ORDER BY id", (frame_id,))
        for d in dets:
            d["attributes"] = json.loads(d["attributes"] or "[]")
        all_alerts = s.index.alerts(limit=10_000)
        position = s.position_of.get(frame_id)
        return {
            "frame": rows[0], "position": position, "detections": dets,
            "alerts_raised": [a for a in all_alerts if a["frame_id"] == frame_id],
            "evidence_for": [a for a in all_alerts if frame_id in a["evidence_frame_ids"] and a["frame_id"] != frame_id],
            "events": s.index._all("SELECT * FROM events WHERE frame_id = ? ORDER BY id", (frame_id,)),
            "prev": s.frames[position - 1].frame_id if position else None,
            "next": s.frames[position + 1].frame_id if position is not None and position + 1 < len(s.frames) else None,
        }

    # ------------------------------------------------------------------ frame index explorer
    @app.get("/api/index/options")
    def index_options():
        s = ds()
        return {"zones": [{"id": z.id, "name": z.name} for z in s.config.zones],
                "labels": s.index._all("SELECT label, category, COUNT(*) AS n FROM detections GROUP BY label ORDER BY n DESC"),
                "entities": s.index.entities(with_visits=False),
                "first_ts": s.index.stats()["first_ts"], "last_ts": s.index.stats()["last_ts"]}

    @app.get("/api/index/query")
    def index_query(text: Optional[str] = None, object: Optional[str] = None, zone: Optional[str] = None,
                    entity: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                    mode: str = "hybrid", limit: int = Query(500, le=5000)):
        from ..agent import AnalystToolkit
        from ..retrieval import HybridRetriever

        s = ds()
        text, object, zone, entity = (v.strip() if v and v.strip() else None for v in (text, object, zone, entity))
        start, end = (v.strip() if v and v.strip() else None for v in (start, end))
        if mode not in ("keyword", "semantic", "hybrid"):
            raise HTTPException(400, "mode must be keyword, semantic or hybrid")
        steps, rows = [], None
        started = time.perf_counter()
        try:
            with s.capture_sql() as sql:
                if object or entity:
                    # the same parser the agent uses turns "blue Ford F150" into column filters
                    filters = AnalystToolkit(s.index, s.config)._object_filters(object) if object else {}
                    dets = s.index.query_detections(zone=zone, entity_id=entity, start=start, end=end, limit=limit, **filters)
                    rows = [{"frame_id": r["frame_id"], "ts": r["ts"], "zone": r["zone"], "description": r["description"],
                             "object": r["display_name"], "entity_id": r["entity_id"], "confidence": r["confidence"],
                             "track_confidence": r["track_confidence"]} for r in dets]
                    steps.append(f"structured detection lookup, filters {filters | ({'entity_id': entity} if entity else {})}")
                if text:
                    retriever = HybridRetriever(s.index, mode=mode)
                    hits = retriever.search(text, start=start, end=end, zone=zone, limit=limit)
                    steps.append(f"{retriever.mode} text search" +
                                 (" (BM25 over FTS5 + TF-IDF vectors, merged by reciprocal-rank fusion)" if mode == "hybrid" else ""))
                    if rows is not None:  # both given: keep frames that match the filters AND the text
                        keep = {r["frame_id"] for r in rows}
                        hits = [h for h in hits if h["frame_id"] in keep]
                        steps.append("intersected with the structured hits")
                    rows = [{"frame_id": h["frame_id"], "ts": h["ts"], "zone": h["zone"], "description": h["description"],
                             "score": h.get("score")} for h in hits]
                if rows is None:
                    rows = [{"frame_id": r["frame_id"], "ts": r["ts"], "zone": r["zone"], "description": r["description"]}
                            for r in s.index.query_frames(start=start, end=end, zone=zone, limit=limit)]
                    steps.append("frames in the time window / zone")
        except ValueError as exc:  # unparseable time, e.g. "25:99"
            raise HTTPException(400, f"Invalid query: {exc}")
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {"rows": rows, "count": len(rows), "steps": steps, "elapsed_ms": round(elapsed_ms, 2),
                "sql": _clean_sql(sql)}

    # ------------------------------------------------------------------ agent chat
    @app.post("/api/chat")
    async def chat(q: Question):
        from ..agent import check_citations

        s = ds()
        question = q.question.strip()
        if not question:
            raise HTTPException(400, "empty question")

        def ask():
            analyst = s.analyst()
            analyst.toolkit.retrieved_ids = set()  # citation check per answer, not per conversation
            started = time.perf_counter()
            answer = analyst.ask(question)
            if answer.mode == "gemini" and getattr(analyst.llm_agent, "last_grounding", None):
                grounding = analyst.llm_agent.last_grounding
            else:
                grounding = check_citations(answer.text, analyst.toolkit.retrieved_ids)
            return answer, grounding, time.perf_counter() - started, analyst.llm_error

        async def stream():
            yield sse("status", {"text": "thinking"})
            try:
                answer, grounding, seconds, llm_error = await asyncio.to_thread(ask)
            except Exception as exc:  # show the failure in the chat instead of a silent spinner
                yield sse("error", {"text": f"{type(exc).__name__}: {exc}"})
                return
            yield sse("tools", {"mode": answer.mode, "calls": [{"name": n, "args": a} for n, a in answer.tool_calls],
                                "llm_error": llm_error})
            # the answer is already computed; it is sent in small chunks so the UI can render progressively
            for chunk in re.findall(r"\S+\s*", answer.text):
                yield sse("token", {"text": chunk})
                await asyncio.sleep(0.008)
            yield sse("done", {"text": answer.text, "grounding": grounding, "seconds": round(seconds, 3)})

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/chat/reset")
    def chat_reset():
        ds().reset_analyst()
        return {"ok": True}

    # ------------------------------------------------------------------ entities
    @app.get("/api/entities")
    def entities():
        s = ds()
        alert_counts = defaultdict(int)
        for a in s.index.alerts(limit=10_000):
            if a["entity_id"]:
                alert_counts[a["entity_id"]] += 1
        out = []
        for e in s.index.entities():
            e["traits"] = _traits(e["signature"])
            e["alerts"] = alert_counts.get(e["entity_id"], 0)
            out.append(e)
        return {"entities": out}

    @app.get("/api/entities/{entity_id}")
    def entity(entity_id: str):
        from ..agent import AnalystToolkit

        s = ds()
        rows = s.index.entities(entity_id=entity_id)
        if not rows:
            raise HTTPException(404, f"no entity {entity_id}")
        e = rows[0]
        e["traits"] = _traits(e["signature"])
        sightings = s.index.query_detections(entity_id=entity_id, limit=5000)
        weights = dict(_VEHICLE_WEIGHTS if e["category"] == "vehicle" else _PERSON_WEIGHTS)
        matched_on = [{"trait": k, "value": e["traits"].get(k), "weight": w} for k, w in weights.items()
                      if e["traits"].get(k) not in (None, "?")] if e["category"] in ("vehicle", "person") else []
        confs = [r["track_confidence"] for r in sightings if r["track_confidence"] is not None]
        return {
            "entity": e,
            "known_vehicle": AnalystToolkit(s.index, s.config)._known_owner(e),
            "sightings": [{"frame_id": r["frame_id"], "ts": r["ts"], "zone": r["zone"], "action": r["action"],
                           "description": r["description"], "display_name": r["display_name"],
                           "confidence": r["confidence"], "track_confidence": r["track_confidence"]} for r in sightings],
            "alerts": s.index.alerts(entity_id=entity_id, limit=1000),
            "match": {"category": e["category"], "matched_on": matched_on, "accept_score": ACCEPT_SCORE,
                      "min_confidence": min(confs) if confs else None, "mean_confidence": e["mean_confidence"],
                      "low_confidence_sightings": sum(c < 0.7 for c in confs)},
        }

    # ------------------------------------------------------------------ summaries / context
    @app.get("/api/summaries")
    def summaries(llm: bool = False):
        from ..analysis import daily_briefing, recommend, summarize_video

        s = ds()
        client = None
        if llm and s.gemini_status()["mode"] == "gemini":
            from ..gemini import GeminiClient

            client = GeminiClient(model=s.gemini_status()["model"], embed_model="unused")
        video = summarize_video(s.index)
        return {
            "one_sentence": video.one_sentence, "highlights": video.highlights,
            "briefing": daily_briefing(s.index, s.config, llm=client), "briefing_by": "gemini" if client else "offline template",
            "recommendations": [r.__dict__ for r in recommend(s.index, s.config)],
            "levels": {level: s.index.summaries(level) for level in ("day", "hour", "minute")},
        }

    @app.get("/api/context")
    def context(budget: int = Query(2500, ge=200, le=20000)):
        s = ds()
        text = render_from_index(s.index, s.config, budget)
        naive = render_naive(s.index, s.config)
        sections = []
        for block in text.split("\n\n"):
            title, _, body = block.partition("\n")
            sections.append({"title": title.lstrip("# "), "text": body, "tokens": estimate_tokens(block)})
        saved = {}
        for name in ("qa_context_eval", "qa_heldout_eval", "qa_gemini_subset_eval"):
            path = RESULTS / f"{name}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                saved[name] = {"model": data.get("model"), "questions": data.get("questions"),
                               "context_sizes": data.get("context_sizes"), "generated_at": data["provenance"]["generated_at"],
                               "results": {c: {k: r.get(k) for k in ("accuracy", "abstention_accuracy", "mean_prompt_tokens",
                                                                    "mean_latency_s", "answers_with_unsupported_citations")}
                                           for c, r in data.get("results", {}).items()}}
        return {"budget": budget, "hierarchical": {"text": text, "tokens": estimate_tokens(text), "sections": sections},
                "naive": {"tokens": estimate_tokens(naive), "chars": len(naive), "preview": naive[:3000]},
                "frames": s.index.stats()["frames"], "saved": saved}

    # ------------------------------------------------------------------ tests
    @app.post("/api/tests/run")
    async def run_tests():
        s = ds()
        if not s.tests_lock.acquire(blocking=False):
            raise HTTPException(409, "a test run is already in progress")
        try:
            s.last_test_run = await asyncio.to_thread(_run_pytest)
            return s.last_test_run
        finally:
            s.tests_lock.release()

    @app.get("/api/tests/last")
    def last_tests():
        return ds().last_test_run or {"groups": [], "ran_at": None}

    # ------------------------------------------------------------------ evaluation results / benchmark
    @app.get("/api/results")
    def results():
        out = {}
        for path in sorted(RESULTS.glob("*.json")):
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        return out

    @app.post("/api/benchmark")
    async def benchmark(frames: int = Query(2000, ge=500, le=20000)):
        from ..evaluation.benchmark import run_benchmark

        s = ds()
        # small, synchronous run in a worker thread; results are returned, not written over docs/results
        result = await asyncio.to_thread(run_benchmark, s.config, [frames], 5)
        return result

    # ------------------------------------------------------------------ static files
    @app.get("/api/diagrams")
    def diagrams():
        return {"diagrams": sorted(p.name for p in DIAGRAMS.glob("*.svg"))}

    app.mount("/diagrams", StaticFiles(directory=DIAGRAMS), name="diagrams")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index_html():
        return FileResponse(STATIC / "index.html")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):  # errors as JSON the UI can show
        return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return app


# ---------------------------------------------------------------------- helpers
def _traits(signature: str) -> dict:
    keys = ("category", "label", "color", "make", "model", "clothing", "masked")
    return {k: (None if v == "?" else v) for k, v in zip(keys, signature.split("|"))}


def _clean_sql(statements: list[str]) -> list[str]:
    """Drop transaction noise; collapse whitespace so the statements read well in the UI."""
    out = []
    for stmt in statements:
        text = " ".join(stmt.split())
        if text.startswith("--"):  # SQLite's own reads inside the FTS5 virtual table
            continue
        if text.upper() in ("BEGIN", "COMMIT", "ROLLBACK") or text.upper().startswith(("PRAGMA", "SAVEPOINT", "RELEASE")):
            continue
        out.append(text)
    return out


GROUP_NAMES = {
    "test_scenario": "Scenario & ground truth", "test_ingest_edge_cases": "Telemetry & ingest",
    "test_perception": "Perception (frame -> objects)", "test_tracking": "Tracking & re-identification",
    "test_rules": "Alert rules", "test_index": "Frame index (SQLite)", "test_retrieval_memory": "Search & memory",
    "test_agent": "Analyst agent", "test_gemini": "Gemini backends (mocked)", "test_evaluation": "Evaluation code",
    "test_web_api": "Web API (this UI)", "test_brief_examples": "Assignment brief examples",
}


def _run_pytest() -> dict:
    """Run the real suite in a subprocess and group the JUnit results by test file."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT / "src"), PYTHONIOENCODING="utf-8")
        started = time.perf_counter()
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}"],
                              cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=600)
        seconds = time.perf_counter() - started
        if not report.exists():
            return {"ok": False, "error": (proc.stdout + proc.stderr)[-3000:], "groups": [], "seconds": round(seconds, 1),
                    "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tree = ET.parse(report)
    groups: dict[str, dict] = {}
    for case in tree.iter("testcase"):
        module = case.get("classname", "").split(".")
        file_key = next((m for m in module if m.startswith("test_")), module[-1] if module else "?")
        group = groups.setdefault(file_key, {"file": f"tests/{file_key}.py", "name": GROUP_NAMES.get(file_key, file_key),
                                             "tests": [], "passed": 0, "failed": 0, "skipped": 0})
        failure = case.find("failure") if case.find("failure") is not None else case.find("error")
        skipped = case.find("skipped") is not None
        outcome = "failed" if failure is not None else "skipped" if skipped else "passed"
        group[outcome] += 1
        group["tests"].append({"name": case.get("name"), "title": _humanize(case.get("name", "")), "outcome": outcome,
                               "seconds": float(case.get("time", 0)),
                               "message": (failure.get("message") or "")[:500] if failure is not None else None})
    total = {k: sum(g[k] for g in groups.values()) for k in ("passed", "failed", "skipped")}
    summary = ", ".join(f"{n} {k}" for k, n in total.items() if n or k == "passed") + f" in {seconds:.1f}s"
    return {"ok": proc.returncode == 0, "summary": summary, "total": total, "seconds": round(seconds, 1),
            "groups": list(groups.values()), "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def _humanize(name: str) -> str:
    """test_truck_logged_correctly[seed-1] -> 'truck logged correctly [seed-1]'"""
    base, _, param = name.partition("[")
    text = base.removeprefix("test_").replace("_", " ")
    return f"{text} [{param}" if param else text


__all__ = ["create_app"]
