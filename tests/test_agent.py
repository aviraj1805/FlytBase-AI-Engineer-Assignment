"""Analyst agent: offline Q&A (always available), grounding, and the LangChain wiring with a fake model."""
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from drone_security.agent import NOT_ENOUGH_EVIDENCE, TOOL_NAMES, AnalystToolkit, SecurityAnalyst, check_citations
from drone_security.agent.langchain_agent import LangChainAnalyst


@pytest.fixture(scope="module")
def analyst(scenario):
    _, pipeline, _ = scenario
    return SecurityAnalyst(pipeline.index, pipeline.config, use_llm=False)


def frames_cited(text):
    return check_citations(text, set())["cited"]


# ------------------------------------------------------------------ offline analyst
def test_what_objects_were_in_the_video(analyst):
    answer = analyst.ask("What objects were in the video?")
    assert answer.tool_calls[0][0] == "summarize_window"
    for word in ("truck", "person", "fox", "bag"):
        assert word in answer.text
    assert frames_cited(answer.text)


def test_show_all_truck_events(analyst):
    answer = analyst.ask("Show all truck events")
    assert answer.tool_calls == [("query_frames", {"object": "truck", "limit": 25})]
    assert "Blue Ford F150" in answer.text and "[f" in answer.text


def test_vehicle_visit_count_and_pronoun_follow_up(analyst):
    answer = analyst.ask("How many times did the blue Ford F150 enter today?")
    assert "made 2 visits" in answer.text and "09:14" in answer.text and "16:45" in answer.text
    follow_up = analyst.ask("When did it leave?")
    assert follow_up.tool_calls[0] == ("entity_history", {"description": "blue ford f150"})
    assert "09:42" in follow_up.text and "17:10" in follow_up.text


def test_two_blue_pickups_are_reported_as_different_vehicles(analyst):
    answer = analyst.ask("Was the blue Chevrolet Silverado the same vehicle as the blue Ford F150?")
    assert answer.text.startswith("No")


def test_alert_questions_cite_frames(analyst):
    answer = analyst.ask("Was there any loitering at night?")
    assert "loitering at main gate" in answer.text and frames_cited(answer.text)
    critical = analyst.ask("List the critical alerts")
    assert critical.tool_calls[0] == ("list_alerts", {"min_severity": "CRITICAL"})
    assert critical.text.count("CRITICAL") >= 3


def test_what_happened_in_a_window(analyst):
    answer = analyst.ask("What happened between 23:00 and 23:30?")
    assert answer.tool_calls[0] == ("summarize_window", {"start": "23:00", "end": "23:30"})
    assert "SUV" in answer.text and "[f" in answer.text


def test_drone_status_at_a_time_and_overall(analyst):
    at = analyst.ask("What was the drone's battery at 03:30?")
    assert "battery" in at.text and frames_cited(at.text)
    overall = analyst.ask("Were there any drone health problems?")
    assert "GEOFENCE" in overall.text and "GPS_DRIFT" in overall.text


def test_daily_briefing_and_one_sentence_summary(analyst):
    briefing = analyst.ask("Give me the daily briefing")
    assert "Overnight:" in briefing.text and "Drone health:" in briefing.text
    summary = analyst.ask("Summarize the day")
    assert summary.text.startswith("Across 646 frames")


def test_unanswerable_questions_get_not_enough_evidence(analyst):
    plate = analyst.ask("What was the licence plate of the black SUV?")
    assert plate.text.startswith(NOT_ENOUGH_EVIDENCE) and plate.tool_calls == []
    nothing = analyst.ask("Show me the helicopter landing")  # "landing" alone matches the drone's dock landings
    assert nothing.text.startswith(NOT_ENOUGH_EVIDENCE) and "closest partial matches" in nothing.text


def test_every_offline_citation_points_at_a_retrieved_frame(analyst):
    questions = ["Show all truck events", "Was there any loitering at night?", "What happened between 02:00 and 03:00?",
                 "How many times did the blue Ford F150 enter today?", "What was the drone's battery at 03:30?"]
    for q in questions:
        analyst.toolkit.retrieved_ids = set()
        answer = analyst.ask(q)
        check = check_citations(answer.text, analyst.toolkit.retrieved_ids)
        assert check["grounded"], (q, check)


def test_toolkit_exposes_exactly_the_five_tools(scenario):
    _, pipeline, _ = scenario
    toolkit = AnalystToolkit(pipeline.index, pipeline.config)
    assert TOOL_NAMES == ["query_frames", "entity_history", "list_alerts", "summarize_window", "drone_status"]
    assert set(toolkit.as_dict()) == set(TOOL_NAMES)


# ------------------------------------------------------------------ LangChain agent (fake model, no network)
class ScriptedToolModel(GenericFakeChatModel):
    """Replays scripted AI messages; tool binding is a no-op."""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def agent(scenario):
    _, pipeline, _ = scenario
    model = ScriptedToolModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "entity_history", "args": {"description": "blue Ford F150"}, "id": "c1"}]),
        AIMessage(content="The blue Ford F150 entered twice [f256] [f454]."),
        AIMessage(content="It left at 17:10 [f99999]."),
    ]))
    return LangChainAnalyst(AnalystToolkit(pipeline.index, pipeline.config), "working memory", llm=model)


def test_agent_executes_tools_and_checks_citations(agent, scenario):
    day, _, _ = scenario
    answer = agent.ask("How many times did the F150 enter?")
    assert answer.mode == "gemini"
    assert answer.tool_calls == [("entity_history", {"description": "blue Ford F150"})]
    assert agent.last_grounding["grounded"], agent.last_grounding


def test_made_up_citations_are_flagged(agent):
    agent.ask("How many times did the F150 enter?")
    agent.ask("When did it leave?")
    assert agent.last_grounding["unsupported"] == [99999]
    humans = [m.content for m in agent.history if isinstance(m, HumanMessage)]
    assert humans == ["How many times did the F150 enter?", "When did it leave?"]


def test_llm_failure_degrades_to_the_offline_answer(scenario):
    _, pipeline, _ = scenario

    class Broken:
        def ask(self, question):
            raise TimeoutError("network down")

    analyst = SecurityAnalyst(pipeline.index, pipeline.config, use_llm=False)
    analyst.llm_agent = Broken()
    answer = analyst.ask("Show all truck events")
    assert answer.mode == "offline" and "Blue Ford F150" in answer.text and "network down" in analyst.llm_error
