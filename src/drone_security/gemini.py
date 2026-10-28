"""Gemini access (google-genai SDK): key handling, model selection, retries, token accounting.

Everything Gemini-powered is optional. `gemini_available()` gates it and every caller has
a deterministic offline fallback, so the system runs fully offline without a key.

* The key is read from GEMINI_API_KEY (or GOOGLE_API_KEY) or from the gitignored .env
  file, and is never printed or logged.
* Models are not hard-coded: `select_models()` lists what the key can access and picks
  the newest stable "flash" model (fast, multimodal: text + images) and the newest
  embedding model. GEMINI_MODEL / GEMINI_EMBED_MODEL override the choice.
* Rate limits (429) and transient server errors are retried with exponential backoff.
* Every call's token usage is accumulated in `usage`, so experiments report real counts.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TypeVar

from .config import PROJECT_ROOT

T = TypeVar("T")
ENV_FILE = PROJECT_ROOT / ".env"
_EXCLUDE = ("tts", "image", "live", "audio", "embedding", "native", "exp", "computer", "robotics", "latest", "learnlm",
            "gemma", "aqa", "veo", "imagen", "lyria", "thinking", "lite")
_RETRYABLE = {429, 500, 502, 503, 504}
REQUEST_TIMEOUT_MS = 120_000
MAX_RETRY_WAIT_S = 90


def load_api_key() -> Optional[str]:
    """GEMINI_API_KEY / GOOGLE_API_KEY from the environment, else from the project's .env file."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.getenv(name, "").strip():
            return os.environ[name].strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and value.strip():
                return value.strip().strip('"').strip("'")
    return None


def gemini_available() -> bool:
    """True when a key is configured, the SDK is installed and offline mode is not forced (DSA_OFFLINE=1)."""
    if os.getenv("DSA_OFFLINE") == "1" or not load_api_key():
        return False
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class Usage:
    requests: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    latency_s: float = 0.0
    by_purpose: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, purpose: str, prompt: int, output: int, seconds: float) -> None:
        self.requests += 1
        self.prompt_tokens += prompt
        self.output_tokens += output
        self.latency_s += seconds
        row = self.by_purpose.setdefault(purpose, {"requests": 0, "prompt_tokens": 0, "output_tokens": 0, "latency_s": 0.0})
        row["requests"] += 1
        row["prompt_tokens"] += prompt
        row["output_tokens"] += output
        row["latency_s"] = round(row["latency_s"] + seconds, 3)


class GeminiClient:
    def __init__(self, *, model: Optional[str] = None, embed_model: Optional[str] = None, client=None,
                 max_retries: int = 6):
        if client is None:
            from google import genai
            from google.genai import types

            key = load_api_key()
            if not key:
                raise RuntimeError("No Gemini API key: set GEMINI_API_KEY or add it to .env")
            # the SDK waits forever by default; a stalled connection must fail (and be retried) instead
            client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
        self.client = client
        self.max_retries = max_retries
        self.usage = Usage()
        if model and embed_model:
            self.model, self.embed_model = model, embed_model
        else:
            chosen = select_models(self.client)
            self.model = model or chosen["generation_model"]
            self.embed_model = embed_model or chosen["embedding_model"]

    # ------------------------------------------------------------------ generation
    def text(self, prompt: str, *, system: Optional[str] = None, images: Optional[list[tuple[bytes, str]]] = None,
             purpose: str = "text") -> str:
        response = self._generate(prompt, system=system, images=images, schema=None, purpose=purpose, thinking=None)
        return (response.text or "").strip()

    def json(self, prompt: str, schema: type[T], *, system: Optional[str] = None,
             images: Optional[list[tuple[bytes, str]]] = None, purpose: str = "json",
             thinking: Optional[str] = None) -> T:
        """Structured output validated against a Pydantic model.

        thinking="low" suits bulk extraction (caption -> objects): faster, and stays inside the server deadline.
        """
        response = self._generate(prompt, system=system, images=images, schema=schema, purpose=purpose,
                                  thinking=thinking)
        if response.parsed is not None:
            return response.parsed
        return schema.model_validate(json.loads(response.text))  # some SDK versions only fill .text

    def _generate(self, prompt: str, *, system, images, schema, purpose: str, thinking: Optional[str]):
        from google.genai import types

        parts: list[Any] = [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in (images or [])]
        parts.append(prompt)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,  # deterministic-as-possible answers for evaluation
            response_mime_type="application/json" if schema else None,
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=thinking) if thinking else None,
        )
        started = time.perf_counter()
        response = self._retry(lambda: self.client.models.generate_content(model=self.model, contents=parts, config=config))
        meta = response.usage_metadata
        self.usage.add(purpose, getattr(meta, "prompt_token_count", 0) or 0,
                       (getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0),
                       time.perf_counter() - started)
        return response

    # ------------------------------------------------------------------ embeddings and tokens
    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT", batch_size: int = 100,
              dimensions: int = 768) -> list[list[float]]:
        from google.genai import types

        vectors: list[list[float]] = []
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=dimensions)
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            # one Content per text: multimodal embedding models merge a plain list of strings into ONE vector
            contents = [types.Content(parts=[types.Part(text=t)]) for t in batch]
            started = time.perf_counter()
            response = self._retry(lambda: self.client.models.embed_content(model=self.embed_model, contents=contents, config=config))
            self.usage.add("embed", 0, 0, time.perf_counter() - started)
            if len(response.embeddings) != len(batch):
                raise RuntimeError(f"{self.embed_model} returned {len(response.embeddings)} vectors for {len(batch)} texts")
            vectors += [e.values for e in response.embeddings]
        return vectors

    def count_tokens(self, text: str) -> int:
        response = self._retry(lambda: self.client.models.count_tokens(model=self.model, contents=text))
        return response.total_tokens

    def _retry(self, call):
        delay = 2.0
        for attempt in range(self.max_retries):
            try:
                return call()
            except Exception as exc:  # google.genai.errors.APIError carries an HTTP status in .code
                timed_out = "timeout" in type(exc).__name__.lower()
                if not (timed_out or getattr(exc, "code", None) in _RETRYABLE) or attempt == self.max_retries - 1:
                    raise
                wait = _retry_after(exc)
                if wait and wait > MAX_RETRY_WAIT_S:  # a daily quota, not a burst limit: retrying cannot help
                    raise
                self.usage.retries += 1
                time.sleep(wait or delay)
                delay = min(delay * 2, 60)
        raise RuntimeError("unreachable")


# ----------------------------------------------------------------------------------- model choice
def list_models(client) -> list[dict]:
    rows = []
    for m in client.models.list():
        rows.append({
            "name": m.name.removeprefix("models/"),
            "display_name": getattr(m, "display_name", None),
            "actions": list(getattr(m, "supported_actions", None) or []),
            "input_token_limit": getattr(m, "input_token_limit", None),
        })
    return rows


def select_models(client, models: Optional[list[dict]] = None) -> dict:
    """Newest stable Gemini 'flash' model for generation and newest embedding model.

    Flash models are Google's fast tier and accept images, which is what both the
    per-frame VLM path and an interactive agent need. Stable releases are preferred
    over previews (previews have tighter quotas and can change underneath us).
    """
    models = models if models is not None else list_models(client)
    generation = [m for m in models if "generateContent" in m["actions"] and m["name"].startswith("gemini-")
                  and "flash" in m["name"] and not any(x in m["name"] for x in _EXCLUDE)]
    embedding = [m for m in models if "embedContent" in m["actions"] and "embedding" in m["name"]]
    if not generation:
        raise RuntimeError("no Gemini flash model is available to this API key")
    chosen = {
        "generation_model": os.getenv("GEMINI_MODEL") or max(generation, key=_rank)["name"],
        # the Gemini embedding family replaced the older text-embedding-00x models
        "embedding_model": os.getenv("GEMINI_EMBED_MODEL") or (
            max(embedding, key=lambda m: (m["name"].startswith("gemini-embedding"), _rank(m)))["name"] if embedding else None),
    }
    return chosen


def _rank(model: dict) -> tuple:
    name = model["name"]
    version = re.search(r"(\d+(?:\.\d+)?)", name)
    stable = "preview" not in name
    return (stable, float(version.group(1)) if version else 0.0, len(name) * -1)


def record_model_choice(client, path: Path) -> dict:
    """List the models, choose, and save both (evidence that the choice came from the live list)."""
    models = list_models(client)
    chosen = select_models(client, models)
    record = {"listed_at": datetime.now().isoformat(timespec="seconds"), **chosen,
              "available": [m for m in models if m["name"].startswith(("gemini", "text-embedding", "embedding"))]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def _retry_after(exc) -> Optional[float]:
    """Use the server's suggested retry delay when the 429 body includes one ("retryDelay": "17s")."""
    match = re.search(r"retryDelay['\"]?:\s*['\"](\d+(?:\.\d+)?)s", str(exc))
    return float(match.group(1)) + 1 if match else None


__all__ = ["GeminiClient", "gemini_available", "load_api_key", "list_models", "select_models", "record_model_choice", "Usage"]
