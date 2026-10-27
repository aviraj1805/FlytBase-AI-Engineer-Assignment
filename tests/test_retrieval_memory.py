"""Hybrid retrieval and hierarchical memory."""
from drone_security.memory import estimate_tokens, render_from_index, render_naive
from drone_security.retrieval import HybridRetriever, TfidfEmbedder, rrf


# ------------------------------------------------------------------ retrieval
def test_tfidf_matches_spelling_variants():
    emb = TfidfEmbedder()
    docs = ["Blue Ford F150 parked at the garage", "Fox crossing the backyard lawn", "Main gate closed"]
    emb.fit(docs)
    scores = emb.score("ford f-150 pick-up")
    assert scores.argmax() == 0 and scores[0] > 5 * max(scores[1], scores[2])


def test_rrf_rewards_frames_found_by_both_rankers():
    a = [{"frame_id": 1, "ts": "t1"}, {"frame_id": 2, "ts": "t2"}]
    b = [{"frame_id": 2, "ts": "t2"}, {"frame_id": 3, "ts": "t3"}]
    assert [r["frame_id"] for r in rrf([a, b])] == [2, 1, 3]


def test_hybrid_finds_paraphrases_that_strict_keyword_search_misses(scenario):
    _, pipeline, _ = scenario
    query = "blue pickups at the garage"
    keyword = HybridRetriever(pipeline.index, mode="keyword").search(query, limit=10)
    hybrid = HybridRetriever(pipeline.index, mode="hybrid").search(query, limit=10)
    assert len(hybrid) >= len(keyword)
    assert any("garage" in r["description"].lower() and "F" in r["description"] for r in hybrid)


def test_semantic_search_respects_time_and_zone_filters(scenario):
    _, pipeline, _ = scenario
    rows = HybridRetriever(pipeline.index, mode="semantic").search("F150 parked", start="16:00", end="18:00", zone="garage")
    assert rows and all("16:00" <= r["ts"][11:16] <= "18:00" and r["zone"] == "garage" for r in rows)


# ------------------------------------------------------------------ memory
def test_summaries_exist_at_every_level_and_cite_frames(scenario):
    _, pipeline, _ = scenario
    index = pipeline.index
    levels = {level: index.summaries(level) for level in ("minute", "hour", "day")}
    assert len(levels["hour"]) == 24 and len(levels["day"]) == 1
    busy_hour = next(s for s in levels["hour"] if s["period_start"].endswith("02:00:00"))
    assert "[f" in busy_hour["text"] and busy_hour["frame_ids"]
    assert any("quiet" in s["text"] for s in levels["minute"])


def test_hour_summary_cites_the_alerts_of_that_hour(scenario):
    _, pipeline, _ = scenario
    index = pipeline.index
    [hour] = index.summaries("hour", start="00:00", end="00:30")[:1]
    loiter = index.alerts(rule_id="LOITERING")[0]
    assert loiter["frame_id"] in hour["frame_ids"]


def test_context_stays_within_budget_and_is_far_smaller_than_the_whole_video(scenario, config):
    _, pipeline, _ = scenario
    context = render_from_index(pipeline.index, config, budget_tokens=1500)
    naive = render_naive(pipeline.index, config)
    assert estimate_tokens(context) <= 1500 * 1.15  # budget plus section headers
    assert estimate_tokens(naive) > 5 * estimate_tokens(context)
    assert "## Today, hour by hour" in context and "Quiet hours" in context


def test_live_memory_and_rebuilt_memory_agree(scenario, config):
    _, pipeline, _ = scenario
    live = pipeline.memory.render(budget_tokens=5000)
    rebuilt = render_from_index(pipeline.index, config, budget_tokens=5000)
    assert live.split("## Latest frames")[0] == rebuilt.split("## Latest frames")[0]
