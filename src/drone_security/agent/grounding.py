"""Grounding checks for agent answers.

The agent must cite the frames it relies on as "[f123]". After every answer we compare
the cited ids with the ids the tools actually returned (plus those in the prompt
context). A citation that no tool returned is unsupported: the model made it up. This
turns "grounded answers" from a prompt instruction into a measured property.
"""
from __future__ import annotations

import re

CITATION = re.compile(r"\[f(\d+)\]")
NOT_ENOUGH_EVIDENCE = "Not enough evidence in the footage to answer that."


def cited_frame_ids(text) -> set[int]:
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    return {int(n) for n in CITATION.findall(str(text or ""))}


def check_citations(answer: str, allowed: set[int]) -> dict:
    cited = cited_frame_ids(answer)
    unsupported = sorted(cited - allowed)
    return {
        "cited": sorted(cited),
        "unsupported": unsupported,
        "abstained": NOT_ENOUGH_EVIDENCE.lower()[:20] in answer.lower(),
        "grounded": bool(cited) and not unsupported,
    }


__all__ = ["CITATION", "NOT_ENOUGH_EVIDENCE", "cited_frame_ids", "check_citations"]
