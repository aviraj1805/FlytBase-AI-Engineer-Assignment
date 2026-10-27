"""Gemini perception backends, behind the same `analyze(frame) -> list[Detection]` interface.

* `GeminiFrameAnalyzer` (LLM): caption -> detections with schema-validated JSON output.
  Handles phrasing the lexicon parser has never seen. Captions are analysed in batches
  (`prefetch`) and cached, because patrol footage repeats the same captions all day.
* `GeminiVisionAnalyzer` (VLM): image -> caption + detections in one call, so a real
  camera frame enters the pipeline exactly like a simulated one.

Both normalise the model's free-form words onto the parser's vocabulary (so "pickup",
"navy", "F-150" become truck / blue / F150), and both fall back to the rule parser for a
frame whenever the API fails: the feed never stalls on the network.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import BaseModel

from ..config import PropertyConfig
from ..models import Detection, Frame
from . import lexicon as lx
from .rule_analyzer import RuleBasedAnalyzer

log = logging.getLogger(__name__)


class DetectedObject(BaseModel):
    category: Literal["person", "vehicle", "animal", "object", "infrastructure"]
    label: str
    color: Optional[str]
    make: Optional[str]
    model: Optional[str]
    attributes: list[str]
    action: Optional[str]
    zone: Optional[str]
    count: int


class CaptionResult(BaseModel):
    index: int
    objects: list[DetectedObject]


class CaptionBatch(BaseModel):
    results: list[CaptionResult]


class ImageAnalysis(BaseModel):
    caption: str
    objects: list[DetectedObject]


RULES = """Output one entry per distinct real object in the scene, using these conventions:
- category: person | vehicle | animal | object | infrastructure.
- label (lower case): vehicles truck|car|van|suv|motorcycle|bicycle|bus|forklift (a pickup is a truck);
  people person; objects package|bag|suitcase|backpack|ladder; infrastructure gate|door; animals by species.
- color: main colour (for people, their clothing colour), one lower-case word, else null.
- make / model: only for vehicles when stated or clearly visible (model without spaces or dashes, e.g. F150), else null.
- attributes: clothing items (hoodie, jacket, uniform, cap, hi-vis vest, sweater, t-shirt, shirt), items a person
  carries, "masked" if the face is covered; "open" for an open gate or door.
- action, one of: entering, exiting, parked, loitering, standing, walking, running, climbing, tampering,
  delivering, loading, unloading, or null. Trying door handles or forcing a door is tampering.
- count: number of people in a group entry (1 otherwise).
- zone: one of the zone ids if the text or image places the object there, else null.
- Leave out anything negated or absent ("no people", "person no longer visible"), closed gates/doors,
  the drone itself, and items a person is carrying (they go in that person's attributes).
- An empty scene has an empty list."""


class GeminiFrameAnalyzer:
    """LLM backend: frame caption -> structured detections."""

    name = "gemini"

    def __init__(self, config: PropertyConfig, client=None, *, batch_size: int = 40):
        if client is None:
            from ..gemini import GeminiClient

            client = GeminiClient()
        self.client = client
        self.config = config
        self.batch_size = batch_size
        self.fallback = RuleBasedAnalyzer()
        self.zones = ", ".join(f"{z.id} ({z.name})" for z in config.zones)
        self.cache: dict[tuple[str, str], list[dict]] = {}
        self.stats = {"llm_requests": 0, "captions_sent": 0, "cache_hits": 0, "fallbacks": 0}

    def prefetch(self, frames: list[Frame]) -> None:
        """Analyse every distinct (caption, zone) in a few batched requests before streaming."""
        todo = list(dict.fromkeys((f.description, f.zone) for f in frames if (f.description, f.zone) not in self.cache))
        for i in range(0, len(todo), self.batch_size):
            self._run_batch(todo[i:i + self.batch_size])

    def analyze(self, frame: Frame) -> list[Detection]:
        key = (frame.description, frame.zone)
        if key in self.cache:
            self.stats["cache_hits"] += 1
        else:
            self._run_batch([key])
        if key not in self.cache:  # the request failed: parser for this frame only
            self.stats["fallbacks"] += 1
            return self.fallback.analyze(frame)
        return [Detection(**d) for d in self.cache[key]]

    def _run_batch(self, items: list[tuple[str, str]]) -> None:
        listing = "\n".join(f"{i}. (drone over {zone}) {caption}" for i, (caption, zone) in enumerate(items))
        prompt = (f"Property zones: {self.zones}.\n{RULES}\n\nAnalyse each numbered security-drone caption and return "
                  f"one result per caption with its index.\n\n{listing}")
        try:
            self.stats["llm_requests"] += 1
            self.stats["captions_sent"] += len(items)
            batch = self.client.json(prompt, CaptionBatch, purpose="frame_analysis")
        except Exception as exc:  # quota, network, invalid output: those frames use the parser
            log.warning("Gemini frame analysis failed for %d captions: %s", len(items), exc)
            return
        for result in batch.results:
            if 0 <= result.index < len(items):
                caption, zone = items[result.index]
                self.cache[(caption, zone)] = [to_detection(o, zone, self.config).to_dict() for o in result.objects]


class GeminiVisionAnalyzer:
    """VLM backend: one image -> caption + structured detections (one request per image)."""

    name = "vlm:gemini"

    def __init__(self, config: PropertyConfig, client=None):
        if client is None:
            from ..gemini import GeminiClient

            client = GeminiClient()
        self.client = client
        self.config = config
        self.zones = ", ".join(f"{z.id} ({z.name})" for z in config.zones)

    def describe(self, image: bytes, mime_type: str, zone_hint: str) -> tuple[str, list[Detection]]:
        prompt = (f"This image is a frame from a security drone patrolling a property; the camera is over the "
                  f"{zone_hint.replace('_', ' ')}. Property zones: {self.zones}.\n"
                  "First write `caption`: ONE plain sentence for a security log naming every person (clothing colour, "
                  "what they carry and do), vehicle (colour, make/model if readable, entering/leaving/parked), animal, "
                  "and any open gate or door. No speculation. Then list the objects.\n" + RULES)
        result = self.client.json(prompt, ImageAnalysis, images=[(image, mime_type)], purpose="vlm")
        return result.caption.strip(), [to_detection(o, zone_hint, self.config) for o in result.objects]


def to_detection(o: DetectedObject, default_zone: str, config: PropertyConfig) -> Detection:
    """Map the model's words onto the parser's vocabulary so both backends are comparable."""
    category = o.category
    label = o.label.strip().lower()
    if category == "vehicle":
        label = lx.VEHICLE_TYPES.get(label, label)
    elif category == "object":
        label = lx.OBJECTS.get(label, label)
    elif category == "animal":
        label = lx.ANIMALS.get(label, label)
    elif category == "person":
        label = "person"
    color = (o.color or "").strip().lower() or None
    attributes = [lx.CLOTHING.get(a.strip().lower(), a.strip().lower()) for a in o.attributes]
    action = _canonical_action(o.action)
    zone = o.zone if o.zone and config.zone(o.zone) else default_zone
    return Detection(
        category=category,
        label=label,
        zone=zone,
        color=lx.COLORS.get(color, color) if color else None,
        make=o.make.strip().title() if o.make else None,
        model=_model_name(o.model),
        attributes=list(dict.fromkeys(a for a in attributes if a)),
        action=action,
        count=max(o.count, 1),
        confidence=0.85,
    )


def _canonical_action(action: Optional[str]) -> Optional[str]:
    if not action:
        return None
    text = action.strip().lower()
    canonical = {canon for _, canon in lx.ACTIONS}
    if text in canonical:
        return text
    return next((canon for phrase, canon in lx.ACTIONS if phrase in text), text)


def _model_name(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    compact = model.replace("-", "").replace(" ", "")
    return compact.upper() if any(ch.isdigit() for ch in compact) else compact.capitalize()


__all__ = ["GeminiFrameAnalyzer", "GeminiVisionAnalyzer", "to_detection"]
