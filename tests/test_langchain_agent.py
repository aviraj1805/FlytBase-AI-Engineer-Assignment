"""LangChain agent wiring with a scripted fake chat model (no network, no API key).

Checks that tools are exposed and executed, tool calls are reported, and the
conversation history carries over so follow-up questions have context.
"""
import pytest

pytest.importorskip("langchain")
pytest.importorskip("langchain_anthropic")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from drone_security.agent.langchain_agent import LangChainAnalyst  # noqa: E402
from drone_security.agent.tools import TOOL_NAMES, AnalystToolkit  # noqa: E402


class ScriptedToolModel(GenericFakeChatModel):
    """Replays scripted AI messages; tool binding is a no-op."""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def agent(demo_run):
    pipeline = demo_run[0]
    model = ScriptedToolModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "find_detections", "args": {"label": "truck"}, "id": "call_1"}]),
        AIMessage(content="The blue Ford F150 accounts for all 7 truck detections."),
        AIMessage(content="It left at 09:40 and 12:20."),
    ]))
    return LangChainAnalyst(AnalystToolkit(pipeline.index, pipeline.config), "working memory", llm=model)


def test_all_toolkit_methods_are_exposed_as_tools(agent):
    assert [t.name for t in agent.tools] == TOOL_NAMES
    assert all(t.description for t in agent.tools)


def test_agent_executes_tool_and_answers(agent):
    answer = agent.ask("Show all truck events")
    assert answer.mode == "claude"
    assert answer.tool_calls == [("find_detections", {"label": "truck"})]
    assert answer.text == "The blue Ford F150 accounts for all 7 truck detections."
    tool_result = next(m for m in agent.history if isinstance(m, ToolMessage))
    assert '"count": 7' in tool_result.content or "'count': 7" in tool_result.content


def test_history_is_kept_for_follow_ups(agent):
    agent.ask("Show all truck events")
    answer = agent.ask("When did it leave?")
    assert answer.text == "It left at 09:40 and 12:20."
    humans = [m.content for m in agent.history if isinstance(m, HumanMessage)]
    assert humans == ["Show all truck events", "When did it leave?"]
