"""SecurityAnalyst: one entry point for operator Q&A.

Uses the LangChain + Claude agent when an Anthropic credential is configured, and the
deterministic offline analyst otherwise (or if the LLM call fails), so the system
always answers.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import PropertyConfig
from ..context import render_from_index
from ..indexing import FrameIndex
from ..llm import DEFAULT_MODEL, llm_available
from .offline import AgentAnswer, OfflineAnalyst
from .tools import AnalystToolkit

log = logging.getLogger(__name__)


class SecurityAnalyst:
    def __init__(
        self,
        index: FrameIndex,
        config: PropertyConfig,
        *,
        context_text: Optional[str] = None,
        use_llm: Optional[bool] = None,
        model: str = DEFAULT_MODEL,
    ):
        self.toolkit = AnalystToolkit(index, config)
        self.offline = OfflineAnalyst(self.toolkit, config)
        self.llm_agent = None
        self.llm_error: Optional[str] = None
        if use_llm if use_llm is not None else llm_available():
            try:
                from .langchain_agent import LangChainAnalyst

                context = context_text or render_from_index(index, config)
                self.llm_agent = LangChainAnalyst(self.toolkit, context, model=model)
            except ImportError as exc:
                self.llm_error = f"LangChain agent unavailable ({exc}); using offline analyst."
                log.warning(self.llm_error)

    @property
    def mode(self) -> str:
        return "claude" if self.llm_agent else "offline"

    def ask(self, question: str) -> AgentAnswer:
        if self.llm_agent:
            try:
                return self.llm_agent.ask(question)
            except Exception as exc:  # network, auth, rate limit: degrade, don't fail the operator
                self.llm_error = f"{type(exc).__name__}: {exc}"
                log.warning("LLM agent failed, answering offline: %s", self.llm_error)
        return self.offline.ask(question)
