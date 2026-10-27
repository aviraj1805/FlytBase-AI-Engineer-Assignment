"""LLM-backed components with the API stubbed out: wiring, caching and fallbacks.

No network or API key needed. Live calls are covered by `python -m drone_security run --analyzer claude`.
"""
import pytest

pytest.importorskip("pydantic")

from drone_security.perception.llm_analyzer import ClaudeFrameAnalyzer, DetectedObject, FrameAnalysis  # noqa: E402


class StubClient:
    def __init__(self, analysis=None, error=None):
        self.analysis, self.error, self.calls = analysis, error, 0

    def parse(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        assert kwargs["output_format"] is FrameAnalysis
        return self.analysis


def truck_analysis(zone="garage"):
    return FrameAnalysis(objects=[DetectedObject(
        category="vehicle", label="Truck", color="Blue", make="ford", model="F-150", attributes=[],
        action="parked", zone=zone, count=1)])


def test_structured_output_is_normalised_into_detections(config, make_frame):
    analyzer = ClaudeFrameAnalyzer(config, client=StubClient(truck_analysis()))
    [det] = analyzer.analyze(make_frame("12:00", "a blue pickup by the garage", zone="garage"))
    assert (det.label, det.color, det.make, det.model, det.zone) == ("truck", "blue", "Ford", "F150", "garage")


def test_identical_captions_hit_the_cache(config, make_frame):
    client = StubClient(truck_analysis())
    analyzer = ClaudeFrameAnalyzer(config, client=client)
    for t in ("12:00", "12:30", "13:00"):
        analyzer.analyze(make_frame(t, "a blue pickup by the garage", zone="garage"))
    assert client.calls == 1 and analyzer.stats["cache_hits"] == 2


def test_unknown_zone_from_model_falls_back_to_telemetry_zone(config, make_frame):
    analyzer = ClaudeFrameAnalyzer(config, client=StubClient(truck_analysis(zone="the moon")))
    [det] = analyzer.analyze(make_frame("12:00", "truck", zone="driveway"))
    assert det.zone == "driveway"


def test_api_error_falls_back_to_rule_parser(config, make_frame):
    analyzer = ClaudeFrameAnalyzer(config, client=StubClient(error=TimeoutError("timeout")))
    [det] = analyzer.analyze(make_frame("12:00", "Blue Ford F150 parked at garage", zone="garage"))
    assert det.model == "F150" and analyzer.stats["fallbacks"] == 1
