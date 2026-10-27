"""Claude-based frame analyzer: caption -> detections via structured outputs.

Handles captions the lexicon cannot (unusual phrasing, unseen makes, implied actions,
VLM output). Production concerns built in:

* **Schema-validated output** (Pydantic + structured outputs) -> no JSON parsing errors.
* **Caption cache**: patrol footage repeats ("Backyard clear, no movement"), so identical
  captions are answered once.
* **Fallback**: any API error, refusal, or invalid zone falls back to the rule parser for
  that frame; the feed never stalls on the LLM.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..config import PropertyConfig
from ..llm import ClaudeClient
from ..models import Detection, Frame
from .rule_analyzer import RuleBasedAnalyzer

log = logging.getLogger(__name__)


class DetectedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["person", "vehicle", "animal", "object", "infrastructure"]
    label: str = Field(description="lower-case noun: truck, car, van, suv, motorcycle, person, dog, package, gate, door ...")
    color: Optional[str] = Field(description="main colour (clothing colour for people), lower-case, or null")
    make: Optional[str] = Field(description="vehicle make, e.g. Ford, or null")
    model: Optional[str] = Field(description="vehicle model without spaces/dashes, e.g. F150, or null")
    attributes: list[str] = Field(description="clothing and carried items, 'masked', 'open' for open gates/doors")
    action: Optional[str] = Field(description="one of: entering, exiting, parked, loitering, standing, walking, running, "
                                              "climbing, tampering, peering, delivering, loading, unloading, or null")
    zone: Optional[str] = Field(description="zone id from the list, or null if not stated")
    count: int = Field(description="how many of this object (people in a group)")


class FrameAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects: list[DetectedObject]


SYSTEM = """You convert security-drone frame captions into structured detections.
Property zones (use these ids): {zones}.
Rules: one entry per distinct object; people's colour is their clothing colour; treat the patrol drone itself
and negated mentions ("no vehicles") as absent; items a person carries go in that person's attributes;
an open gate/door is category infrastructure with attribute "open". Return an empty list for empty scenes."""


class ClaudeFrameAnalyzer:
    name = "claude"

    def __init__(self, config: PropertyConfig, *, client: Optional[ClaudeClient] = None, cache_size: int = 4096):
        self.config = config
        self.client = client or ClaudeClient(effort="low")
        self.fallback = RuleBasedAnalyzer()
        self.system = SYSTEM.format(zones=", ".join(f"{z.id} ({z.name})" for z in config.zones))
        self._cache: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
        self.cache_size = cache_size
        self.stats = {"llm_calls": 0, "cache_hits": 0, "fallbacks": 0}

    def analyze(self, frame: Frame) -> list[Detection]:
        key = (frame.description.strip().lower(), frame.telemetry.zone)
        if key in self._cache:
            self._cache.move_to_end(key)
            self.stats["cache_hits"] += 1
            return [Detection(**d) for d in self._cache[key]]
        try:
            self.stats["llm_calls"] += 1
            analysis = self.client.parse(
                system=self.system,
                content=f"Drone position zone: {frame.telemetry.zone}\nCaption: {frame.description}",
                output_format=FrameAnalysis,
                max_tokens=4000,
            )
            detections = [self._to_detection(o, frame.telemetry.zone) for o in analysis.objects]
        except Exception as exc:  # API error, refusal, validation: fall back for this frame only
            log.warning("LLM analyzer failed on frame %s (%s); using rule parser", frame.frame_id, exc)
            self.stats["fallbacks"] += 1
            return self.fallback.analyze(frame)

        self._cache[key] = [d.to_dict() for d in detections]
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return detections

    def _to_detection(self, o: DetectedObject, default_zone: str) -> Detection:
        zone = o.zone if o.zone and self.config.zone(o.zone) else default_zone
        return Detection(
            category=o.category,
            label=o.label.strip().lower(),
            zone=zone,
            color=(o.color or "").strip().lower() or None,
            make=o.make.strip().title() if o.make else None,
            model=o.model.replace("-", "").replace(" ", "").upper() if o.model else None,
            attributes=[a.strip().lower() for a in o.attributes],
            action=(o.action or "").strip().lower() or None,
            count=max(o.count, 1),
            confidence=0.85,
        )
