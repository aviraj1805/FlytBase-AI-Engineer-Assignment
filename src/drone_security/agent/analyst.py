"""SecurityAnalyst: one entry point for operator Q&A.

Uses the LangChain + Gemini agent when a Gemini key is configured, and the deterministic
offline analyst otherwise (or when a Gemini call fails), so the operator always gets an
answer and alerts never depend on the network.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import PropertyConfig
from ..gemini import gemini_available, load_api_key
from ..indexing import FrameIndex
from ..memory import render_from_index
from ..retrieval import HybridRetriever
from .offline import AgentAnswer, OfflineAnalyst
from .tools import AnalystToolkit

log = logging.getLogger(__name__)


class SecurityAnalyst:
    def __init__(self, index: FrameIndex, config: PropertyConfig, *, use_llm: Optional[bool] = None,
                 retriever: Optional[HybridRetriever] = None, context_text: Optional[str] = None,
                 model: Optional[str] = None, llm=None):
        self.toolkit = AnalystToolkit(index, config, retriever)
        self.offline = OfflineAnalyst(self.toolkit, config)
        self.llm_agent = None
        self.llm_error: Optional[str] = None
        wanted = use_llm if use_llm is not None else gemini_available()
        if wanted:
            try:
                from .langchain_agent import LangChainAnalyst

                if model is None and llm is None:
                    from ..gemini import GeminiClient

                    model = GeminiClient().model  # lists the available models and picks one
                context = context_text if context_text is not None else render_from_index(index, config)
                self.llm_agent = LangChainAnalyst(self.toolkit, context, llm=llm, model=model, api_key=load_api_key())
            except Exception as exc:  # missing package, bad key, no models: stay useful offline
                self.llm_error = f"Gemini agent unavailable ({type(exc).__name__}: {exc}); using the offline analyst."
                log.warning(self.llm_error)

    @property
    def mode(self) -> str:
        return "gemini" if self.llm_agent else "offline"

    def ask(self, question: str) -> AgentAnswer:
        if self.llm_agent:
            try:
                return self.llm_agent.ask(question)
            except Exception as exc:  # network, quota, safety block: degrade, don't fail the operator
                self.llm_error = f"{type(exc).__name__}: {exc}"
                log.warning("Gemini agent failed, answering offline: %s", self.llm_error)
        return self.offline.ask(question)
