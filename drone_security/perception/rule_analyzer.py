"""Deterministic caption parser: free text -> list[Detection].

The parser finds entity mentions (vehicles, people, animals, objects, open access
points), then reads the words around each mention for modifiers (colour, make,
count, clothing) and the words after it, up to the next mention, for the action
and location. It runs offline in microseconds, so it is the default analyzer
and the fallback whenever the LLM analyzer fails.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..models import Detection, Frame
from . import lexicon as lx


def _alternation(phrases) -> str:
    return "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))


_PLURAL = r"(?:e?s)?"
_RE_PERSON = re.compile(rf"\b({_alternation(lx.PERSON_WORDS)}){_PLURAL}\b")
_RE_VTYPE = re.compile(rf"\b({_alternation(lx.VEHICLE_TYPES)}){_PLURAL}\b")
_RE_ANIMAL = re.compile(rf"\b({_alternation(lx.ANIMALS)}){_PLURAL}\b")
_RE_OBJECT = re.compile(rf"\b({_alternation(lx.OBJECTS)}){_PLURAL}\b")
_RE_MAKE = re.compile(rf"\b({_alternation(lx.MAKES)})\b")
_RE_MODELS = [
    (re.compile(rf"\b(?:({_alternation(lx.MAKES)})\s+)?({pattern})\b"), make, model, vtype)
    for pattern, make, model, vtype in lx.VEHICLE_MODELS
]
_RE_TYPE_AFTER = re.compile(rf"^\s+({_alternation(lx.VEHICLE_TYPES)})\b")
_RE_OPEN_ACCESS = re.compile(
    rf"\b(?:({_alternation(lx.INFRASTRUCTURE)})\s+(?:is\s+|was\s+|has been\s+|been\s+)?(?:left\s+)?"
    rf"({_alternation(lx.OPEN_STATES)})|({_alternation(lx.OPEN_STATES)})\s+({_alternation(lx.INFRASTRUCTURE)}))\b"
)
_RE_ZONE = re.compile(rf"\b({_alternation(p for p, _ in lx.ZONE_ALIASES)})\b")
_ZONE_LOOKUP = dict(lx.ZONE_ALIASES)
_RE_COLOR = re.compile(rf"\b({_alternation(lx.COLORS)})\b")
_RE_CLOTHING = re.compile(rf"\b({_alternation(lx.CLOTHING)})\b")
_RE_CARRIED = re.compile(rf"\b(?:carrying|holding|with|carries|holds)\s+(?:a\s+|an\s+|the\s+|some\s+)?({_alternation(lx.CARRIED)})\b")
_RE_BACKPACK = re.compile(r"\bbackpack\b")
_RE_COUNT = re.compile(rf"(?<![:\d])\b(\d{{1,2}}|{_alternation(lx.NUMBER_WORDS)})\s+(?:\w+\s+){{0,2}}$")
_RE_NEGATED = re.compile(rf"\b({'|'.join(lx.NEGATIONS)})\s+(?:\w+\s+){{0,2}}$")
_RE_COORDINATION = re.compile(r"^[\s,]*(?:or|and|nor)?[\s,]*(?:any\s+)?$")
_RE_ATTACH = re.compile(r"\b(?:carrying|holding|with|carries|holds)\s+(?:a\s+|an\s+|the\s+|some\s+)?$")
_RE_FRAME_PREFIX = re.compile(r"^\s*frame\s*#?\s*\d+\s*[:\-|]\s*")
_SENTENCE_END = re.compile(r"[.;!?]")
_INFRA_ZONE = {"gate": "main_gate", "garage door": "garage", "door": "front_door"}
_ACTION_PATTERNS = [(re.compile(rf"\b{re.escape(phrase)}\b"), action) for phrase, action in lx.ACTIONS]


@dataclass
class _Mention:
    start: int
    end: int
    category: str
    label: str
    make: Optional[str] = None
    model: Optional[str] = None
    count: int = 1


class RuleBasedAnalyzer:
    """Lexicon + pattern based frame analyzer (no ML, no network)."""

    name = "rules"

    def analyze(self, frame: Frame) -> list[Detection]:
        return self.parse(frame.description, default_zone=frame.telemetry.zone)

    # ------------------------------------------------------------------ parsing
    def parse(self, description: str, default_zone: str = "open_area") -> list[Detection]:
        text = _RE_FRAME_PREFIX.sub("", " ".join(description.lower().split()))
        explicit_zone = _zone_in(text)
        frame_zone = explicit_zone or default_zone
        mentions = self._mentions(text)

        detections: list[Detection] = []
        prev_negated = False
        for i, mention in enumerate(mentions):
            prev_end = mentions[i - 1].end if i else 0
            next_start = mentions[i + 1].start if i + 1 < len(mentions) else len(text)
            before = text[max(prev_end, mention.start - 40):mention.start]
            after = text[mention.end:next_start]
            sentence_break = _SENTENCE_END.search(after)
            if sentence_break:
                after = after[:sentence_break.start()]

            # "no vehicles or people": negation carries across a coordinated list
            if _RE_NEGATED.search(before) or (prev_negated and _RE_COORDINATION.match(before)):
                prev_negated = True
                continue
            prev_negated = False

            # "person carrying a box walking ...": the box belongs to the person
            if mention.category == "object" and detections and detections[-1].category == "person" and _RE_ATTACH.search(before):
                carrier = detections[-1]
                carrier.attributes.append(mention.label)
                carrier.action = carrier.action or _action_in(after)
                carrier.zone = _zone_in(after) or carrier.zone
                continue

            detections.append(self._build(mention, text, before, after, frame_zone, explicit_zone, is_first=(i == 0)))
        return detections

    def _mentions(self, text: str) -> list[_Mention]:
        candidates: list[_Mention] = []

        for match in _RE_OPEN_ACCESS.finditer(text):
            label = match.group(1) or match.group(4)
            candidates.append(_Mention(match.start(), match.end(), "infrastructure", label))

        for regex, make, model, vtype in _RE_MODELS:
            for match in regex.finditer(text):
                end = match.end()
                follow = _RE_TYPE_AFTER.match(text[end:])
                if follow:
                    end += follow.end()
                candidates.append(_Mention(match.start(), end, "vehicle", vtype, make=make, model=model))

        for match in _RE_MAKE.finditer(text):
            end, vtype = match.end(), "car"
            follow = _RE_TYPE_AFTER.match(text[end:])
            if follow:
                end += follow.end()
                vtype = lx.VEHICLE_TYPES[follow.group(1)]
            candidates.append(_Mention(match.start(), end, "vehicle", vtype, make=lx.MAKES[match.group(1)]))

        for regex, category, table in (
            (_RE_VTYPE, "vehicle", lx.VEHICLE_TYPES),
            (_RE_PERSON, "person", None),
            (_RE_ANIMAL, "animal", lx.ANIMALS),
            (_RE_OBJECT, "object", lx.OBJECTS),
        ):
            for match in regex.finditer(text):
                word = match.group(1)
                if category == "person":
                    candidates.append(_Mention(match.start(), match.end(), "person", "person", count=lx.PERSON_WORDS[word]))
                else:
                    candidates.append(_Mention(match.start(), match.end(), category, table[word]))

        # keep the longest mention at each position, drop anything overlapping an earlier pick
        candidates.sort(key=lambda m: (m.start, -(m.end - m.start)))
        selected: list[_Mention] = []
        for cand in candidates:
            if selected and cand.start < selected[-1].end:
                continue
            selected.append(cand)
        return selected

    def _build(self, m: _Mention, text: str, before: str, after: str, frame_zone: str,
               explicit_zone: Optional[str], is_first: bool) -> Detection:
        context = before + text[m.start:m.end] + after
        zone = _zone_in(after)
        if m.category == "infrastructure":
            # "warehouse side door ajar" -> warehouse; a bare "door left open" -> front door
            zone = zone or explicit_zone or _INFRA_ZONE.get(m.label)
        zone = zone or frame_zone

        count = m.count
        count_match = _RE_COUNT.search(before)
        if count_match:
            word = count_match.group(1)
            count = int(word) if word.isdigit() else lx.NUMBER_WORDS.get(word, count)

        attributes: list[str] = []
        color = None
        if m.category == "person":
            for cm in _RE_CLOTHING.finditer(context):
                attributes.append(lx.CLOTHING[cm.group(1)])
                color = color or _color_before(context, cm.start())
            attributes += [lx.CARRIED[c.group(1)] for c in _RE_CARRIED.finditer(after)]
            if _RE_BACKPACK.search(after) and "backpack" not in attributes:
                attributes.append("backpack")
            if any(word in context for word in lx.MASK_WORDS):
                attributes.append("masked")
        else:
            color = _color_before(before, len(before))
        if m.category == "infrastructure":
            attributes.append("open")
        if m.category == "vehicle" and "headlights off" in after:
            attributes.append("headlights off")

        return Detection(
            category=m.category,
            label=m.label,
            zone=zone,
            color=color,
            make=m.make,
            model=m.model,
            attributes=list(dict.fromkeys(attributes)),
            # "loitering person at gate": an action before the noun only counts at the start of the caption
            action=_action_in(after) or (_action_in(before) if is_first else None),
            count=max(count, 1),
            confidence=0.9 if m.model else 0.8,
        )


def _zone_in(text: str) -> Optional[str]:
    match = _RE_ZONE.search(text)
    return _ZONE_LOOKUP[match.group(1)] if match else None


zone_in = _zone_in  # public: the offline analyst parses zones out of questions the same way


def _action_in(text: str) -> Optional[str]:
    hits = [(lx_index, action) for lx_index, (regex, action) in enumerate(_ACTION_PATTERNS) if regex.search(text)]
    return hits[0][1] if hits else None


def _color_before(text: str, pos: int) -> Optional[str]:
    """Colour word among the two words immediately before position `pos` ("dark blue sedan")."""
    window = " ".join(text[:pos].split()[-2:])
    found = _RE_COLOR.findall(window)
    return lx.COLORS[found[-1]] if found else None
