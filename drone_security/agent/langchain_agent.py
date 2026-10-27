"""LangChain tool-calling agent backed by Claude.

The agent gets the working-memory block (see context.py) in its system prompt and the
AnalystToolkit methods as tools for anything deeper. Conversation history is kept
across `ask` calls, so follow-ups ("when did it leave?") resolve against earlier turns.
"""
from __future__ import annotations

import inspect
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool

from ..llm import DEFAULT_MODEL, FALLBACK_BETA
from .offline import AgentAnswer
from .tools import TOOL_NAMES, AnalystToolkit

try:  # LangChain >= 1.0
    from langchain.agents import create_agent as _create_agent

    def _build_agent(model, tools, system_prompt):
        return _create_agent(model=model, tools=tools, system_prompt=system_prompt)
except ImportError:  # older stacks: LangGraph's prebuilt ReAct agent
    from langgraph.prebuilt import create_react_agent as _create_react_agent

    def _build_agent(model, tools, system_prompt):
        return _create_react_agent(model, tools, prompt=system_prompt)


SYSTEM_PROMPT = """You are the security analyst for a docked patrol drone that monitors one property.
You answer the operator's questions about the drone's footage, telemetry and alerts, and recommend actions.

How to work:
- The working memory below is a bounded digest. For specifics (exact times, counts, captions, telemetry),
  call the tools; they query the full frame-by-frame index. Prefer one well-targeted tool call over many.
- Ground every claim in tool results or the working memory. Quote times as HH:MM and name zones.
  If the data does not answer the question, say so instead of guessing.
- Bare clock times ("00:01") refer to the footage day. Night hours and zone types are in the working memory.
- When asked for recommendations, prioritise: immediate safety/intrusion response, then identities to
  verify, then hardening of the site or the patrol plan. Be concrete and short.
- Answer in plain text, concise: a direct answer first, then supporting detail as a short list if useful.

# Working memory
{context}
"""


class LangChainAnalyst:
    mode = "claude"

    def __init__(self, toolkit: AnalystToolkit, context_text: str, *, model: str = DEFAULT_MODEL, llm: Any = None):
        self.toolkit = toolkit
        self.tools = [_as_tool(getattr(toolkit, name)) for name in TOOL_NAMES]
        self.llm = llm or ChatAnthropic(
            model=model,
            max_tokens=16000,
            effort="medium",  # Opus 5.5 always thinks; effort is the depth/cost control
            betas=[FALLBACK_BETA],  # routes through client.beta.messages.create
            model_kwargs={"fallbacks": "default"},  # server-side retry if a safety classifier declines
        )
        self.agent = _build_agent(self.llm, self.tools, SYSTEM_PROMPT.format(context=context_text))
        self.history: list[BaseMessage] = []

    def ask(self, question: str) -> AgentAnswer:
        result = self.agent.invoke({"messages": [*self.history, HumanMessage(question)]})
        messages: list[BaseMessage] = result["messages"]
        new = messages[len(self.history) + 1:]
        self.history = messages  # full transcript, including tool calls, so follow-ups have context

        calls = [(c["name"], c.get("args", {})) for m in new if isinstance(m, AIMessage) for c in (m.tool_calls or [])]
        final = next((m for m in reversed(new) if isinstance(m, AIMessage) and not m.tool_calls), None)
        return AgentAnswer(_text(final) if final else "(no answer)", self.mode, calls)

    def reset(self) -> None:
        self.history = []


def _as_tool(method) -> StructuredTool:
    doc = inspect.getdoc(method) or method.__name__
    return StructuredTool.from_function(func=method, name=method.__name__, description=doc)


def _text(message: AIMessage) -> str:
    """Final answer text; Claude returns content blocks (thinking + text), keep only the text."""
    if isinstance(message.content, str):
        return message.content.strip()
    return "".join(b.get("text", "") for b in message.content if isinstance(b, dict) and b.get("type") == "text").strip()


__all__ = ["LangChainAnalyst"]
