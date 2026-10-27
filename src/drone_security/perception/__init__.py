"""Perception: frame (caption and/or image) -> structured detections.

Every backend implements `analyze(frame) -> list[Detection]` and has a `name`:
    RuleBasedAnalyzer    deterministic lexicon parser, offline, the default
    GeminiFrameAnalyzer  Gemini LLM over the caption (perception/gemini_analyzer.py)
    GeminiVisionAnalyzer Gemini VLM over a real image (perception/gemini_analyzer.py)
"""
from typing import Protocol

from ..models import Detection, Frame
from .rule_analyzer import RuleBasedAnalyzer


class FrameAnalyzer(Protocol):
    name: str

    def analyze(self, frame: Frame) -> list[Detection]: ...


__all__ = ["FrameAnalyzer", "RuleBasedAnalyzer"]
