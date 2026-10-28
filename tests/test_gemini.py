"""Gemini integration with fake clients: model choice, key handling, analyzers, fallbacks (no network)."""
from types import SimpleNamespace

import pytest

from drone_security import gemini
from drone_security.perception.gemini_analyzer import (CaptionBatch, CaptionResult, DetectedObject, GeminiFrameAnalyzer,
                                                       GeminiVisionAnalyzer, ImageAnalysis)

MODELS = [
    {"name": "gemini-2.0-flash", "actions": ["generateContent"], "display_name": "", "input_token_limit": 1},
    {"name": "gemini-2.5-flash", "actions": ["generateContent", "countTokens"], "display_name": "", "input_token_limit": 1},
    {"name": "gemini-3.0-flash-preview", "actions": ["generateContent"], "display_name": "", "input_token_limit": 1},
    {"name": "gemini-2.5-flash-image", "actions": ["generateContent"], "display_name": "", "input_token_limit": 1},
    {"name": "gemini-2.5-flash-lite", "actions": ["generateContent"], "display_name": "", "input_token_limit": 1},
    {"name": "gemini-2.5-pro", "actions": ["generateContent"], "display_name": "", "input_token_limit": 1},
    {"name": "gemini-embedding-001", "actions": ["embedContent"], "display_name": "", "input_token_limit": 1},
    {"name": "text-embedding-004", "actions": ["embedContent"], "display_name": "", "input_token_limit": 1},
]


def test_model_choice_prefers_the_newest_stable_flash_model(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_EMBED_MODEL", raising=False)
    chosen = gemini.select_models(None, MODELS)
    assert chosen == {"generation_model": "gemini-2.5-flash", "embedding_model": "gemini-embedding-001"}


def test_model_choice_can_be_overridden(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
    assert gemini.select_models(None, MODELS)["generation_model"] == "gemini-2.0-flash"


def test_key_is_read_from_env_file_and_offline_flag_wins(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\nGEMINI_API_KEY="abc123"\n')
    monkeypatch.setattr(gemini, "ENV_FILE", env)
    assert gemini.load_api_key() == "abc123"
    monkeypatch.setenv("DSA_OFFLINE", "1")
    assert not gemini.gemini_available()


def test_rate_limits_are_retried_then_succeed():
    class RateLimited(Exception):
        code = 429

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RateLimited('{"retryDelay": "0s"}')
        return "ok"

    client = gemini.GeminiClient(model="m", embed_model="e", client=object())
    assert client._retry(flaky) == "ok" and client.usage.retries == 2


class FakeGemini:
    """Stands in for GeminiClient.json: returns scripted structured results."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def json(self, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def obj(**kw):
    base = dict(category="vehicle", label="pickup", color="navy", make="ford", model="F-150", attributes=[],
                action="pulls in", zone=None, count=1)
    return DetectedObject(**{**base, **kw})


def test_llm_output_is_normalised_to_the_parser_vocabulary(config, make_frame):
    fake = FakeGemini([CaptionBatch(results=[CaptionResult(index=0, objects=[obj()])])])
    analyzer = GeminiFrameAnalyzer(config, client=fake)
    [det] = analyzer.analyze(make_frame("09:14", "Navy Ford F-150 pickup rolls in through the gate"))
    assert (det.label, det.color, det.make, det.model, det.zone) == ("truck", "blue", "Ford", "F150", "main_gate")
    assert det.display_name == "Blue Ford F150"


def test_captions_are_batched_and_cached(config, make_frame):
    frames = [make_frame("09:00", "Main gate quiet"), make_frame("09:02", "Fox on the lawn", zone="backyard"),
              make_frame("09:30", "Main gate quiet")]
    fake = FakeGemini([CaptionBatch(results=[CaptionResult(index=0, objects=[]),
                                             CaptionResult(index=1, objects=[obj(category="animal", label="fox", color=None,
                                                                                 make=None, model=None, action=None)])])])
    analyzer = GeminiFrameAnalyzer(config, client=fake)
    analyzer.prefetch(frames)
    results = [analyzer.analyze(f) for f in frames]
    assert len(fake.prompts) == 1 and analyzer.stats["cache_hits"] == 3
    assert [len(r) for r in results] == [0, 1, 0] and results[1][0].label == "fox"


def test_api_failure_falls_back_to_the_rule_parser(config, make_frame):
    analyzer = GeminiFrameAnalyzer(config, client=FakeGemini([RuntimeError("quota exceeded")]))
    [det] = analyzer.analyze(make_frame("09:14", "Blue Ford F150 entering through the main gate"))
    assert det.model == "F150" and analyzer.stats["fallbacks"] == 1


def test_vision_analyzer_returns_caption_and_detections(config):
    fake = FakeGemini([ImageAnalysis(caption="A blue pickup parked by a gate.", objects=[obj(action="parked")])])
    caption, [det] = GeminiVisionAnalyzer(config, client=fake).describe(b"jpeg", "image/jpeg", "main_gate")
    assert caption == "A blue pickup parked by a gate." and det.action == "parked" and det.zone == "main_gate"


def test_usage_is_accumulated_per_purpose():
    usage = gemini.Usage()
    usage.add("vlm", 1000, 50, 1.5)
    usage.add("vlm", 900, 40, 1.0)
    assert usage.requests == 2 and usage.by_purpose["vlm"]["prompt_tokens"] == 1900


def test_real_client_needs_a_key(monkeypatch):
    monkeypatch.setattr(gemini, "load_api_key", lambda: None)
    pytest.importorskip("google.genai")
    with pytest.raises(RuntimeError, match="No Gemini API key"):
        gemini.GeminiClient()


def test_model_listing_reads_the_sdk_objects():
    fake = SimpleNamespace(models=SimpleNamespace(list=lambda: [
        SimpleNamespace(name="models/gemini-2.5-flash", display_name="Flash", supported_actions=["generateContent"],
                        input_token_limit=1048576)]))
    assert gemini.list_models(fake)[0]["name"] == "gemini-2.5-flash"


def test_each_text_gets_its_own_embedding():
    """Multimodal embedding models merge a plain list of strings into one vector; texts must be sent as separate Contents."""
    calls = []

    def embed_content(model, contents, config):
        calls.append(contents)
        merged = isinstance(contents[0], str)  # what gemini-embedding-2 does with a plain list
        vectors = [[0.0, 1.0]] if merged else [[float(i), 1.0] for i, _ in enumerate(contents)]
        return SimpleNamespace(embeddings=[SimpleNamespace(values=v) for v in vectors])

    fake = SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
    client = gemini.GeminiClient(model="m", embed_model="gemini-embedding-2", client=fake)
    vectors = client.embed(["blue truck", "fox", "gate open"], dimensions=2)
    assert len(vectors) == 3 and len(calls) == 1


def test_daily_quota_errors_fail_fast_instead_of_sleeping():
    class DailyQuota(Exception):
        code = 429

    calls = []

    def exhausted():
        calls.append(1)
        raise DailyQuota('{"retryDelay": "4477s"}')

    client = gemini.GeminiClient(model="m", embed_model="e", client=object())
    with pytest.raises(DailyQuota):
        client._retry(exhausted)
    assert len(calls) == 1 and client.usage.retries == 0
