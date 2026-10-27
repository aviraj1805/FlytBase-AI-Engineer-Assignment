"""Frame-by-frame index on SQLite.

Every processed frame is stored with its telemetry, the structured detections the
analyzer produced, and an FTS5 full-text entry. That gives two complementary ways
to find footage:

* **Structured queries** on indexed columns - "all truck detections between 09:00
  and 13:00 at the garage" is a B-tree range scan on (label, ts).
* **Full-text search** (BM25-ranked) over the caption *plus* the enrichment tags, so
  "truck" also finds a frame captioned "Blue Ford F150 at garage" because the
  analyzer tagged it `vehicle truck pickup ford f150`.

SQLite was chosen deliberately: it is embedded (runs on the dock's edge computer),
needs no server, ships with Python, and handles millions of rows with the right
indexes. The schema maps 1:1 to Postgres + pgvector if this ever moves to the cloud.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

from ..models import Alert, EventLog, FrameResult, Severity
from ..timeutil import TimeLike, from_ts, resolve, to_ts
from ..tracking import Entity

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS frames (
    frame_id             INTEGER PRIMARY KEY,
    ts                   TEXT NOT NULL,
    description          TEXT NOT NULL,
    source               TEXT,
    image_path           TEXT,
    zone                 TEXT,
    lat                  REAL,
    lon                  REAL,
    altitude_m           REAL,
    heading_deg          REAL,
    speed_mps            REAL,
    battery_pct          REAL,
    wind_mps             REAL,
    mode                 TEXT,
    distance_from_home_m REAL
);
CREATE INDEX IF NOT EXISTS idx_frames_ts   ON frames(ts);
CREATE INDEX IF NOT EXISTS idx_frames_zone ON frames(zone, ts);

CREATE TABLE IF NOT EXISTS detections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id     INTEGER NOT NULL REFERENCES frames(frame_id) ON DELETE CASCADE,
    ts           TEXT NOT NULL,              -- denormalised so range scans skip the join
    category     TEXT NOT NULL,
    label        TEXT NOT NULL,
    display_name TEXT,
    color        TEXT,
    make         TEXT,
    model        TEXT,
    action       TEXT,
    zone         TEXT,
    attributes   TEXT,                       -- JSON list
    count        INTEGER DEFAULT 1,
    confidence   REAL,
    entity_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_det_label    ON detections(label, ts);
CREATE INDEX IF NOT EXISTS idx_det_category ON detections(category, ts);
CREATE INDEX IF NOT EXISTS idx_det_entity   ON detections(entity_id, ts);
CREATE INDEX IF NOT EXISTS idx_det_zone     ON detections(zone, ts);
CREATE INDEX IF NOT EXISTS idx_det_frame    ON detections(frame_id);

CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(
    description, tags, frame_id UNINDEXED, tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id    TEXT PRIMARY KEY,
    signature    TEXT NOT NULL,
    category     TEXT NOT NULL,
    label        TEXT NOT NULL,
    display_name TEXT,
    first_seen   TEXT,
    last_seen    TEXT,
    last_zone    TEXT,
    sightings    INTEGER,
    visit_count  INTEGER,
    visits       TEXT                        -- JSON [{start, end, zones}]
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    frame_id  INTEGER,
    entity_id TEXT,
    zone      TEXT,
    kind      TEXT,
    message   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    frame_id           INTEGER,
    rule_id            TEXT NOT NULL,
    severity           INTEGER NOT NULL,
    severity_name      TEXT NOT NULL,
    message            TEXT NOT NULL,
    zone               TEXT,
    entity_id          TEXT,
    recommended_action TEXT,
    related_rules      TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts  ON alerts(ts);
CREATE INDEX IF NOT EXISTS idx_alerts_sev ON alerts(severity, ts);

CREATE TABLE IF NOT EXISTS summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    level        TEXT NOT NULL,              -- chunk | day
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    text         TEXT NOT NULL
);
"""

# extra words indexed with each label so natural queries hit ("pickup" -> truck)
_LABEL_SYNONYMS = {
    "truck": "pickup lorry vehicle", "car": "sedan automobile vehicle", "van": "vehicle delivery",
    "suv": "vehicle", "motorcycle": "motorbike bike vehicle", "bicycle": "bike cyclist",
    "person": "people human pedestrian individual", "package": "parcel box delivery",
    "gate": "access entrance", "door": "access entrance",
}
_STOPWORDS = {
    "show", "me", "all", "the", "a", "an", "any", "find", "list", "events", "event", "frames", "frame",
    "of", "in", "at", "on", "with", "were", "was", "is", "are", "there", "what", "which", "when",
    "did", "do", "to", "and", "or", "for", "from", "today", "video", "footage", "seen", "spotted",
}


class FrameIndex:
    def __init__(self, path: str | Path = ":memory:", *, batch_size: int = 50):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # LangGraph executes tool calls on worker threads, so the connection must be shareable;
        # the lock serialises access because one sqlite3 connection is not safe for concurrent use.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self.batch_size = batch_size
        self._pending = 0

    # ================================================================== writes
    def add(self, result: FrameResult) -> None:
        frame, t = result.frame, result.frame.telemetry
        ts = to_ts(frame.timestamp)
        cur = self.conn.cursor()
        if cur.execute("SELECT 1 FROM frames WHERE frame_id = ?", (frame.frame_id,)).fetchone():
            # re-indexing a frame (e.g. re-captioned by a better VLM): replace its derived rows too
            cur.execute("DELETE FROM detections WHERE frame_id = ?", (frame.frame_id,))
            cur.execute("DELETE FROM frames_fts WHERE frame_id = ?", (frame.frame_id,))
        cur.execute(
            "INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (frame.frame_id, ts, frame.description, frame.source, frame.image_path, t.zone, t.lat, t.lon,
             t.altitude_m, t.heading_deg, t.speed_mps, t.battery_pct, t.wind_mps, t.mode, t.distance_from_home_m),
        )
        cur.executemany(
            "INSERT INTO detections (frame_id, ts, category, label, display_name, color, make, model, action, zone,"
            " attributes, count, confidence, entity_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (frame.frame_id, ts, d.category, d.label, d.display_name, d.color, d.make, d.model, d.action, d.zone,
                 json.dumps(d.attributes), d.count, d.confidence, d.entity_id)
                for d in result.detections
            ],
        )
        cur.execute(
            "INSERT INTO frames_fts (description, tags, frame_id) VALUES (?,?,?)",
            (frame.description, _tags(result), frame.frame_id),
        )
        self._add_events(cur, result.logs)
        self._add_alerts(cur, result.alerts)
        self._pending += 1
        if self._pending >= self.batch_size:
            self.flush()

    def _add_events(self, cur: sqlite3.Cursor, logs: Iterable[EventLog]) -> None:
        cur.executemany(
            "INSERT INTO events (ts, frame_id, entity_id, zone, kind, message) VALUES (?,?,?,?,?,?)",
            [(to_ts(e.timestamp), e.frame_id, e.entity_id, e.zone, e.kind, e.message) for e in logs],
        )

    def _add_alerts(self, cur: sqlite3.Cursor, alerts: Iterable[Alert]) -> None:
        for a in alerts:
            cur.execute(
                "INSERT INTO alerts (ts, frame_id, rule_id, severity, severity_name, message, zone, entity_id,"
                " recommended_action, related_rules) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (to_ts(a.timestamp), a.frame_id, a.rule_id, int(a.severity), a.severity.name, a.message, a.zone,
                 a.entity_id, a.recommended_action, json.dumps(a.related_rules)),
            )
            a.alert_id = cur.lastrowid

    def upsert_entities(self, entities: Iterable[Entity]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (e.entity_id, e.signature, e.category, e.label, e.display_name, to_ts(e.first_seen), to_ts(e.last_seen),
                 e.last_zone, e.sightings, len(e.visits),
                 json.dumps([{"start": to_ts(v.start), "end": to_ts(v.end), "zones": v.zones, "left": v.closed} for v in e.visits]))
                for e in entities
            ],
        )

    def add_summary(self, level: str, start: str, end: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO summaries (level, period_start, period_end, text) VALUES (?,?,?,?)", (level, start, end, text)
        )

    def flush(self) -> None:
        with self.lock:
            self.conn.commit()
        self._pending = 0

    def close(self) -> None:
        self.flush()
        self.conn.close()

    # ================================================================== reads
    def footage_day(self) -> Optional[date]:
        """Day of the most recent footage; bare clock times in queries refer to it."""
        row = self._one("SELECT MAX(ts) AS ts FROM frames")
        return from_ts(row["ts"]).date() if row and row["ts"] else None

    def _range(self, start: TimeLike, end: TimeLike) -> tuple[Optional[str], Optional[str]]:
        day = self.footage_day()
        return resolve(start, day), resolve(end, day, end_of_minute=True)

    def query_detections(
        self,
        *,
        label: Optional[str] = None,
        category: Optional[str] = None,
        color: Optional[str] = None,
        make: Optional[str] = None,
        model: Optional[str] = None,
        zone: Optional[str] = None,
        action: Optional[str] = None,
        entity_id: Optional[str] = None,
        start: TimeLike = None,
        end: TimeLike = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Structured lookup, e.g. query_detections(label="truck") == "show all truck events"."""
        self.flush()
        start_ts, end_ts = self._range(start, end)
        clauses, params = [], []
        # analyzer output is lower-case, so exact matches here can use the (column, ts) indexes
        for column, value in (("d.label", label), ("d.category", category), ("d.color", color),
                              ("d.zone", zone_id(zone)), ("d.action", action), ("d.entity_id", entity_id)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value if column == "d.entity_id" else value.strip().lower())
        for column, value in (("d.make", make), ("d.model", model)):
            if value:
                clauses.append(f"REPLACE({column}, '-', '') = ? COLLATE NOCASE")
                params.append(value.strip().replace("-", ""))
        if start_ts:
            clauses.append("d.ts >= ?")
            params.append(start_ts)
        if end_ts:
            clauses.append("d.ts <= ?")
            params.append(end_ts)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._all(
            f"SELECT d.*, f.description, f.altitude_m, f.battery_pct FROM detections d "
            f"JOIN frames f ON f.frame_id = d.frame_id {where} ORDER BY d.ts LIMIT ?",
            (*params, limit),
        )
        for row in rows:
            row["attributes"] = json.loads(row["attributes"] or "[]")
        return rows

    def query_frames(self, *, start: TimeLike = None, end: TimeLike = None, zone: Optional[str] = None,
                     limit: int = 500) -> list[dict[str, Any]]:
        self.flush()
        start_ts, end_ts = self._range(start, end)
        clauses, params = [], []
        if start_ts:
            clauses.append("ts >= ?")
            params.append(start_ts)
        if end_ts:
            clauses.append("ts <= ?")
            params.append(end_ts)
        if zone:
            clauses.append("zone = ?")
            params.append(zone_id(zone))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._all(f"SELECT * FROM frames {where} ORDER BY ts LIMIT ?", (*params, limit))

    def search(self, text: str, *, start: TimeLike = None, end: TimeLike = None, limit: int = 20) -> list[dict[str, Any]]:
        """BM25-ranked full-text search over captions and enrichment tags.

        All query terms must match; if that finds nothing, any term may match.
        """
        self.flush()
        terms = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]
        if not terms:
            return []
        start_ts, end_ts = self._range(start, end)
        for joiner in (" AND ", " OR "):
            match = joiner.join(f'"{t}"' for t in terms)
            clauses, params = ["frames_fts MATCH ?"], [match]
            if start_ts:
                clauses.append("f.ts >= ?")
                params.append(start_ts)
            if end_ts:
                clauses.append("f.ts <= ?")
                params.append(end_ts)
            rows = self._all(
                "SELECT f.frame_id, f.ts, f.zone, f.description, frames_fts.tags AS tags, bm25(frames_fts) AS score, "
                "snippet(frames_fts, 0, '[', ']', '...', 12) AS snippet "
                f"FROM frames_fts JOIN frames f ON f.frame_id = frames_fts.frame_id WHERE {' AND '.join(clauses)} "
                "ORDER BY score LIMIT ?",
                (*params, limit),
            )
            if rows or len(terms) == 1:
                return rows
        return []

    def alerts(self, *, min_severity: str | int | Severity | None = None, start: TimeLike = None, end: TimeLike = None,
               rule_id: Optional[str] = None, zone: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        self.flush()
        start_ts, end_ts = self._range(start, end)
        clauses, params = [], []
        if min_severity is not None:
            clauses.append("severity >= ?")
            params.append(int(Severity.parse(min_severity)))
        if start_ts:
            clauses.append("ts >= ?")
            params.append(start_ts)
        if end_ts:
            clauses.append("ts <= ?")
            params.append(end_ts)
        if rule_id:
            clauses.append("rule_id = ?")
            params.append(rule_id)
        if zone:
            clauses.append("zone = ?")
            params.append(zone_id(zone))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._all(f"SELECT * FROM alerts {where} ORDER BY ts LIMIT ?", (*params, limit))
        for row in rows:
            row["related_rules"] = json.loads(row["related_rules"] or "[]")
        return rows

    def events(self, *, start: TimeLike = None, end: TimeLike = None, entity_id: Optional[str] = None,
               limit: int = 500) -> list[dict[str, Any]]:
        self.flush()
        start_ts, end_ts = self._range(start, end)
        clauses, params = [], []
        if start_ts:
            clauses.append("ts >= ?")
            params.append(start_ts)
        if end_ts:
            clauses.append("ts <= ?")
            params.append(end_ts)
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._all(f"SELECT * FROM events {where} ORDER BY ts, id LIMIT ?", (*params, limit))

    def entities(self, *, category: Optional[str] = None, label: Optional[str] = None,
                 text: Optional[str] = None) -> list[dict[str, Any]]:
        """Tracked identities. `text` matches words of the display name ("blue f150")."""
        self.flush()
        clauses, params = [], []
        if category:
            clauses.append("category = ?")
            params.append(category.strip().lower())
        if label:
            clauses.append("label = ?")
            params.append(label.strip().lower())
        for word in re.findall(r"[a-z0-9-]+", (text or "").lower()):
            clauses.append("LOWER(display_name) LIKE ?")
            params.append(f"%{word}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._all(f"SELECT * FROM entities {where} ORDER BY visit_count DESC, sightings DESC", params)
        for row in rows:
            row["visits"] = json.loads(row["visits"] or "[]")
        return rows

    def object_counts(self, *, start: TimeLike = None, end: TimeLike = None) -> list[dict[str, Any]]:
        """Inventory of everything seen: one row per category/label with sighting and identity counts."""
        self.flush()
        start_ts, end_ts = self._range(start, end)
        clauses, params = [], []
        if start_ts:
            clauses.append("ts >= ?")
            params.append(start_ts)
        if end_ts:
            clauses.append("ts <= ?")
            params.append(end_ts)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._all(
            "SELECT category, label, COUNT(*) AS sightings, COUNT(DISTINCT entity_id) AS distinct_entities, "
            "GROUP_CONCAT(DISTINCT display_name) AS examples, MIN(ts) AS first_seen, MAX(ts) AS last_seen "
            f"FROM detections {where} GROUP BY category, label ORDER BY category, sightings DESC",
            params,
        )

    def telemetry_near(self, when: TimeLike) -> Optional[dict[str, Any]]:
        self.flush()
        ts = resolve(when, self.footage_day())
        # two index seeks instead of scoring every row
        before = self._one("SELECT * FROM frames WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (ts,))
        after = self._one("SELECT * FROM frames WHERE ts >= ? ORDER BY ts ASC LIMIT 1", (ts,))
        if not before or not after:
            return before or after
        target = from_ts(ts)
        return before if target - from_ts(before["ts"]) <= from_ts(after["ts"]) - target else after

    def summaries(self, level: Optional[str] = None) -> list[dict[str, Any]]:
        self.flush()
        if level:
            return self._all("SELECT * FROM summaries WHERE level = ? ORDER BY period_start", (level,))
        return self._all("SELECT * FROM summaries ORDER BY period_start")

    def stats(self) -> dict[str, Any]:
        self.flush()
        frames = self._one("SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM frames")
        by_sev = self._all("SELECT severity_name, COUNT(*) AS n FROM alerts GROUP BY severity ORDER BY severity DESC")
        return {
            "frames": frames["n"],
            "first_ts": frames["first"],
            "last_ts": frames["last"],
            "detections": self._one("SELECT COUNT(*) AS n FROM detections")["n"],
            "entities": self._one("SELECT COUNT(*) AS n FROM entities")["n"],
            "events": self._one("SELECT COUNT(*) AS n FROM events")["n"],
            "alerts": {row["severity_name"]: row["n"] for row in by_sev},
        }

    # ------------------------------------------------------------------ helpers
    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, tuple(params))]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None


def zone_id(value: Optional[str]) -> Optional[str]:
    """'Main Gate' -> 'main_gate' (zone ids are stored snake_case)."""
    return "_".join(value.lower().split()) if value else None


def _tags(result: FrameResult) -> str:
    words = [result.frame.telemetry.zone.replace("_", " ")]
    for d in result.detections:
        words += [d.category, d.label, _LABEL_SYNONYMS.get(d.label, ""), d.color or "", d.make or "", d.model or "",
                  d.action or "", d.zone.replace("_", " "), " ".join(d.attributes)]
    for a in result.alerts:
        words += ["alert", a.severity.name.lower(), a.rule_id.lower().replace("-", " ")]
    return " ".join(w for w in words if w)
