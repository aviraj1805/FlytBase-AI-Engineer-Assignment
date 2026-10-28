"""LangChain tool-calling agent backed by Gemini.

The agent gets the hierarchical memory (day/hour summaries, rolling window) in its system
prompt and the five toolkit methods as tools for anything finer. Conversation history is
kept across `ask` calls, so follow-ups ("when did it leave?") resolve against earlier turns.
After each answer the cited frame ids are checked against what the tools returned.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from .grounding import NOT_ENOUGH_EVIDENCE, check_citations, cited_frame_ids
from .offline import AgentAnswer
from .tools import TOOL_NAMES, AnalystToolkit

SYSTEM_PROMPT = """You are the security analyst for a docked patrol drone that monitors one property.
You answer the operator's questions about the drone's footage, telemetry and alerts.

Rules:
- Use ONLY facts from the working memory below and from tool results. Never use outside knowledge or guess.
- Cite every fact with the frame id it comes from, written exactly as [f123]. Only cite frame ids that appear in
  the working memory or in a tool result.
- If the tools and memory do not contain the answer, reply exactly "{not_enough}" and say briefly what is missing.
- The working memory is a summary. For exact times, counts, captions or telemetry, call a tool.
  Prefer one well-targeted tool call; drill down with summarize_window(start, end) or query_frames.
- Bare clock times ("00:01") refer to the footage day. Zones and night/business hours are in the memory.
- Answer in plain text: the direct answer first, then the supporting detail. Be concise.

# Working memory
{context}
"""


class LangChainAnalyst:
    mode = "gemini"

    def __init__(self, toolkit: AnalystToolkit, context_text: str, *, llm: Any = None, model: Optional[str] = None,
                 api_key: Optional[str] = None):
        self.toolkit = toolkit
        self.context_ids = cited_frame_ids(context_text)
        self.tools = [_as_tool(getattr(toolkit, name)) for name in TOOL_NAMES]
        if llm is None:
            from langchain_google_genai import ChatGoogleGenerativeAI

            llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0.0, timeout=120, max_retries=6)
        self.llm = llm
        prompt = SYSTEM_PROMPT.replace("{not_enough}", NOT_ENOUGH_EVIDENCE).replace("{context}", context_text)
        self.agent = create_agent(model=self.llm, tools=self.tools, system_prompt=prompt)
        self.history: list[BaseMessage] = []
        self.last_grounding: dict = {}
        self.last_usage: dict = {}

    def ask(self, question: str) -> AgentAnswer:
        self.toolkit.retrieved_ids = set()
        result = self.agent.invoke({"messages": [*self.history, HumanMessage(question)]})
        messages: list[BaseMessage] = result["messages"]
        new = messages[len(self.history) + 1:]
        self.history = messages  # full transcript, including tool calls, so follow-ups have context

        calls = [(c["name"], c.get("args", {})) for m in new if isinstance(m, AIMessage) for c in (m.tool_calls or [])]
        final = next((m for m in reversed(new) if isinstance(m, AIMessage) and not m.tool_calls), None)
        text = _text(final) if final else NOT_ENOUGH_EVIDENCE
        self.last_grounding = check_citations(text, self.toolkit.retrieved_ids | self.context_ids)
        self.last_usage = _usage(new)
        self.last_usage["tool_messages"] = sum(isinstance(m, ToolMessage) for m in new)
        return AgentAnswer(text, self.mode, calls)

    def reset(self) -> None:
        self.history = []


def _as_tool(method) -> StructuredTool:
    doc = inspect.getdoc(method) or method.__name__
    return StructuredTool.from_function(func=method, name=method.__name__, description=doc)


def _text(message: AIMessage) -> str:
    """Final answer text; content may be a string or a list of content blocks."""
    if isinstance(message.content, str):
        return message.content.strip()
    parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in message.content]
    return "".join(p for p in parts if p).strip()


def _usage(messages: list[BaseMessage]) -> dict:
    """Sum the token usage LangChain reports on each model response of this turn."""
    prompt = output = 0
    for m in messages:
        meta = getattr(m, "usage_metadata", None) or {}
        prompt += meta.get("input_tokens", 0)
        output += meta.get("output_tokens", 0)
    return {"prompt_tokens": prompt, "output_tokens": output, "model_calls": sum(isinstance(m, AIMessage) for m in messages)}


__all__ = ["LangChainAnalyst", "SYSTEM_PROMPT"]
