"""Analyst agent: offline Q&A (always available) and the LLM fallback path."""
import pytest

from drone_security.agent import SecurityAnalyst


@pytest.fixture(scope="module")
def analyst(demo_run):
    pipeline = demo_run[0]
    return SecurityAnalyst(pipeline.index, pipeline.config, use_llm=False)


def test_what_objects_were_in_the_video(analyst):
    answer = analyst.ask("What objects were in the video?")
    assert answer.tool_calls[0][0] == "object_inventory"
    for word in ("truck", "person", "deer", "package"):
        assert word in answer.text


def test_show_all_truck_events(analyst):
    answer = analyst.ask("Show all truck events")
    assert answer.tool_calls == [("find_detections", {"label": "truck"})]
    assert answer.text.startswith("7 truck events")


def test_vehicle_visit_count_and_pronoun_follow_up(analyst):
    first = analyst.ask("How many times did the blue Ford F150 enter today?")
    assert "made 3 visits" in first.text
    follow_up = analyst.ask("When did it leave?")
    assert follow_up.tool_calls[0][1]["description"] == "Blue Ford F150"
    assert "09:40, 12:20" in follow_up.text and "no exit observed" in follow_up.text


def test_night_filter(analyst):
    answer = analyst.ask("Were there any people at the main gate at night?")
    assert "at night" in answer.text and "00:01" in answer.text and "07:55" not in answer.text


def test_alerts_in_a_time_window(analyst):
    answer = analyst.ask("Any alerts between 23:00 and 23:59?")
    assert answer.text.startswith("3 alerts") and "tampering" in answer.text


def test_what_happened_at_a_time_gives_the_timeline(analyst):
    answer = analyst.ask("What happened at 02:15?")
    assert [c[0] for c in answer.tool_calls] == ["timeline", "get_alerts"]
    assert "climbing" in answer.text and "Door open at warehouse" in answer.text


def test_drone_status_at_a_time(analyst):
    answer = analyst.ask("What was the drone battery at 03:34?")
    assert "battery 14%" in answer.text


def test_recommendations_are_prioritised(analyst):
    text = analyst.ask("What should I do?").text
    assert text.index("[P1]") < text.index("[P2]") < text.index("[P3]")


def test_one_sentence_video_summary(analyst):
    summary = analyst.ask("Summarize the video").text.splitlines()[0]
    assert summary.startswith("Across 67 frames") and "17 alerts" in summary and summary.count(".") <= 3


def test_free_text_fallback(analyst):
    answer = analyst.ask("person with a ladder")
    assert answer.tool_calls[0][0] == "search_frames" and "ladder" in answer.text


class _BrokenAgent:
    def ask(self, question):
        raise ConnectionError("network down")


def test_llm_failure_degrades_to_offline_answer(demo_run):
    pipeline = demo_run[0]
    analyst = SecurityAnalyst(pipeline.index, pipeline.config, use_llm=False)
    analyst.llm_agent = _BrokenAgent()
    answer = analyst.ask("Any critical alerts?")
    assert answer.mode == "offline" and "4 critical alerts" in answer.text
    assert "network down" in analyst.llm_error
