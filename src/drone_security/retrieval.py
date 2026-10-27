"""Hybrid frame retrieval: keyword (FTS5/BM25) + semantic vectors, fused by reciprocal rank.

Keyword search is precise when the query uses the caption's words ("F150", "gate open")
but misses paraphrases ("someone hanging around the entrance" vs "person loitering at the
main gate"). Vector search covers paraphrases but can drift on exact identifiers. Hybrid
retrieval runs both and merges the two rankings with reciprocal-rank fusion (RRF):

    score(frame) = sum over rankings of 1 / (60 + rank)

RRF needs no score calibration between BM25 and cosine similarity, which is why it is
the usual default for hybrid search.

Vectors are computed per *unique caption*, not per frame: patrol footage repeats the
same few captions thousands of times, so a day of 650 frames has ~200 distinct captions
and months of footage stay cheap to embed. Two embedders share one interface:

* `GeminiEmbedder`: Gemini embedding model (semantic), vectors cached in SQLite so a
  caption is embedded once.
* `TfidfEmbedder`: offline fallback. Word + character n-gram TF-IDF. It is lexical, not
  semantic, but the character n-grams still match spelling variants ("F-150"/"F150",
  "pick-up"/"pickup") and partial overlaps that strict BM25 matching misses.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from typing import Optional, Protocol

import numpy as np

from .indexing import FrameIndex
from .indexing.frame_index import STOPWORDS, zone_id
from .timeutil import TimeLike

RRF_K = 60
_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str

    def fit(self, docs: list[str]) -> None: ...

    def score(self, query: str) -> np.ndarray: ...  # similarity of the query to each fitted doc


class TfidfEmbedder:
    """Offline vectors: TF-IDF over words and character 3-5-grams, cosine similarity."""

    name = "tfidf"

    def __init__(self):
        self.idf: dict[str, float] = {}
        self.postings: dict[str, list[tuple[int, float]]] = {}
        self.n_docs = 0

    def fit(self, docs: list[str]) -> None:
        counts = [self._terms(d) for d in docs]
        df = Counter(term for c in counts for term in c)
        self.n_docs = len(docs)
        self.idf = {t: math.log((1 + self.n_docs) / (1 + n)) + 1 for t, n in df.items()}
        postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for i, c in enumerate(counts):
            vector = self._weigh(c)
            for term, weight in vector.items():
                postings[term].append((i, weight))
        self.postings = dict(postings)

    def score(self, query: str) -> np.ndarray:
        scores = np.zeros(self.n_docs, dtype=np.float32)
        for term, q_weight in self._weigh(self._terms(query)).items():
            for doc, d_weight in self.postings.get(term, ()):
                scores[doc] += q_weight * d_weight
        return scores

    def _weigh(self, counts: Counter) -> dict[str, float]:
        vector = {t: (1 + math.log(n)) * self.idf.get(t, 0.0) for t, n in counts.items()}
        norm = math.sqrt(sum(w * w for w in vector.values())) or 1.0
        return {t: w / norm for t, w in vector.items() if w}

    @staticmethod
    def _terms(text: str) -> Counter:
        words = [w for w in _WORD.findall(text.lower().replace("-", "")) if w not in STOPWORDS]
        terms = Counter(f"w:{w}" for w in words)
        for w in words:
            padded = f" {w} "
            for n in (3, 4, 5):
                terms.update(f"c:{padded[i:i + n]}" for i in range(len(padded) - n + 1))
        return terms


class GeminiEmbedder:
    """Gemini embeddings; caption vectors cached in the index database (embedded once, ever)."""

    name = "gemini"

    def __init__(self, client, index: FrameIndex, dimensions: int = 768):
        self.client = client
        self.index = index
        self.dimensions = dimensions
        self.matrix = np.zeros((0, dimensions), dtype=np.float32)
        with index.lock:
            index.conn.execute("CREATE TABLE IF NOT EXISTS caption_vectors (text_hash TEXT PRIMARY KEY, model TEXT, "
                               "vector BLOB)")

    def fit(self, docs: list[str]) -> None:
        hashes = [_hash(d) for d in docs]
        cached = self._load(hashes)
        missing = [(h, d) for h, d in zip(hashes, docs) if h not in cached]
        if missing:
            vectors = self.client.embed([d for _, d in missing], task_type="RETRIEVAL_DOCUMENT", dimensions=self.dimensions)
            rows = [(h, self.client.embed_model, np.asarray(v, dtype=np.float32).tobytes()) for (h, _), v in zip(missing, vectors)]
            with self.index.lock:
                self.index.conn.executemany("INSERT OR REPLACE INTO caption_vectors VALUES (?,?,?)", rows)
                self.index.conn.commit()
            cached.update({h: np.frombuffer(blob, dtype=np.float32) for h, _, blob in rows})
        matrix = np.stack([cached[h] for h in hashes]) if hashes else np.zeros((0, self.dimensions), dtype=np.float32)
        self.matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)

    def score(self, query: str) -> np.ndarray:
        q = np.asarray(self.client.embed([query], task_type="RETRIEVAL_QUERY", dimensions=self.dimensions)[0], dtype=np.float32)
        return self.matrix @ (q / max(float(np.linalg.norm(q)), 1e-9))

    def _load(self, hashes: list[str]) -> dict[str, np.ndarray]:
        found: dict[str, np.ndarray] = {}
        for i in range(0, len(hashes), 500):
            chunk = hashes[i:i + 500]
            rows = self.index._all(f"SELECT text_hash, vector FROM caption_vectors WHERE model = ? AND text_hash IN "
                                   f"({','.join('?' * len(chunk))})", [self.client.embed_model, *chunk])
            found.update({r["text_hash"]: np.frombuffer(r["vector"], dtype=np.float32) for r in rows})
        return found


class HybridRetriever:
    """mode = keyword | semantic | hybrid (default)."""

    def __init__(self, index: FrameIndex, embedder: Optional[Embedder] = None, mode: str = "hybrid"):
        self.index = index
        self.embedder = embedder or TfidfEmbedder()
        self.mode = mode if mode == "keyword" else f"{mode} ({self.embedder.name})"
        self._kind = mode
        self._captions: list[str] = []
        self._frames_by_caption: dict[str, list[dict]] = {}
        self._indexed_version = -1

    def search(self, text: str, *, start: TimeLike = None, end: TimeLike = None, zone: Optional[str] = None,
               limit: int = 10) -> list[dict]:
        pool = max(limit * 5, 50)
        keyword = self._keyword(text, start, end, zone, pool) if self._kind in ("keyword", "hybrid") else []
        if self._kind == "keyword":
            return keyword[:limit]
        semantic = self._semantic(text, start, end, zone, pool)
        if self._kind == "semantic":
            return semantic[:limit]
        return rrf([keyword, semantic])[:limit]

    def _keyword(self, text, start, end, zone, limit) -> list[dict]:
        rows = self.index.search(text, start=start, end=end, limit=limit * 3 if zone else limit)
        return [r for r in rows if not zone or r["zone"] == zone_id(zone)][:limit]

    def _semantic(self, text, start, end, zone, limit) -> list[dict]:
        self._refresh()
        if not self._captions:
            return []
        scores = self.embedder.score(text)
        start_ts, end_ts = self.index.time_range(start, end)
        out: list[dict] = []
        for i in np.argsort(-scores):
            if scores[i] <= 0 or len(out) >= limit:
                break
            # a caption repeated on many days maps to many frames: take the newest matching ones only
            taken = 0
            for frame in reversed(self._frames_by_caption[self._captions[i]]):
                if taken >= limit:
                    break
                if (start_ts and frame["ts"] < start_ts) or (end_ts and frame["ts"] > end_ts):
                    continue
                if zone and frame["zone"] != zone_id(zone):
                    continue
                out.append({**frame, "score": float(scores[i])})
                taken += 1
        return out[:limit]

    def _refresh(self) -> None:
        """(Re)fit on the unique captions whenever new frames were indexed."""
        if self.index.version == self._indexed_version:
            return
        by_caption: dict[str, list[dict]] = defaultdict(list)
        for row in self.index._all("SELECT frame_id, ts, zone, description FROM frames ORDER BY ts, frame_id"):
            by_caption[row["description"]].append(row)
        self._frames_by_caption = dict(by_caption)
        self._captions = list(by_caption)
        self.embedder.fit(self._captions)
        self._indexed_version = self.index.version


def rrf(rankings: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """Reciprocal-rank fusion of several ranked frame lists (best first)."""
    scores: dict[int, float] = defaultdict(float)
    rows: dict[int, dict] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            scores[row["frame_id"]] += 1.0 / (k + rank)
            rows.setdefault(row["frame_id"], row)
    ordered = sorted(scores, key=lambda fid: (-scores[fid], rows[fid]["ts"]))
    return [{**rows[fid], "score": round(scores[fid], 5)} for fid in ordered]


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


__all__ = ["HybridRetriever", "TfidfEmbedder", "GeminiEmbedder", "rrf"]
