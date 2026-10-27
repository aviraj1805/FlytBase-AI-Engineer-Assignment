"""Context management: hourly digests and the budgeted working-memory block."""
from drone_security.context import ContextManager, render_from_index
from drone_security.pipeline import SecurityPipeline


def test_hourly_digests_are_written_to_the_index(demo_run):
    pipeline = demo_run[0]
    digests = pipeline.index.summaries("chunk")
    assert len(digests) == len(pipeline.context.digests) >= 20
    assert any("quiet" in d["text"] for d in digests)
    assert any("CRITICAL" in d["text"] for d in digests)


def test_render_respects_budget_and_keeps_priority_sections(demo_run):
    text = demo_run[0].context.render(budget_chars=1500)
    assert len(text) < 1500 * 1.3  # each section may keep one line past the budget
    for section in ("## Property", "## High-severity alerts", "## Key entities"):
        assert section in text
    assert "use the search tools" in text  # truncated sections point the agent at its tools


def test_larger_budget_includes_more_history(demo_run):
    small, large = demo_run[0].context.render(1500), demo_run[0].context.render(20000)
    assert len(large) > len(small) and "Latest frames" in large


def test_render_from_index_matches_live_context(demo_run):
    pipeline = demo_run[0]
    rebuilt = render_from_index(pipeline.index, pipeline.config, 20000)
    assert "Blue Ford F150: 3 visit(s)" in rebuilt
    assert "Masked person in black jacket climbing" in rebuilt


def test_failing_llm_summarizer_falls_back_to_template(config, make_frame):
    pipeline = SecurityPipeline(config)

    def broken(chunk):
        raise TimeoutError("LLM timed out")

    pipeline.context = ContextManager(config, pipeline.tracker, index=pipeline.index, summarizer=broken)
    pipeline.run([make_frame("00:01", "Person loitering near main gate"), make_frame("02:00", "Backyard clear")])
    assert len(pipeline.context.digests) == 2
    assert "Person loitering" in pipeline.context.digests[0][2] or "Person @" in pipeline.context.digests[0][2]
