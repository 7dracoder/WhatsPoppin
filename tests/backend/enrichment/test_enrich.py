import pytest

from backend.classification import ClassificationResult, JevError
from backend.enrichment.enrich import ENTITY_QUESTION, ENTITY_QUESTIONS, OUTCOMES_MAX_CHARS, enrich_event, event_text
from backend.entities import CATEGORIES, EntityMap, MapEntity

MAP = EntityMap(version=1, entities=[
    MapEntity(symbol="TSLA", name="Tesla, Inc.", category="company", aliases=["Tesla"]),
    MapEntity(symbol="AAPL", name="Apple Inc.", category="company", aliases=["Apple"]),
    MapEntity(symbol="Elon Musk", name="Elon Musk", category="person"),
    MapEntity(symbol="Gold", name="Gold", category="resource"),
])
MARKET = {"source": "kalshi", "market_id": "KXTSLA-1", "title": "Will Tesla deliver 500k cars in Q4?",
          "outcome_label": "Above 500,000", "event_id": "KXTSLA", "event_title": "Tesla Q4 deliveries",
          "series_title": None, "category": "Companies", "tags": ["tesla", "ev"], "rules_primary": "Resolves Yes if ..."}
LADDER = [{**MARKET, "market_id": f"KXBTCD-T{k}", "title": "Bitcoin price on Oct 16, 2026?",
           "outcome_label": f"${k},000 or above", "event_title": "BTC price on Oct 16, 2026 at 5pm EDT?"}
          for k in (70, 71, 72)]


class FakeJev:
    """Answers multi_select specs: picks the given labels, records every (title, text, spec) it was asked."""

    def __init__(self, picks: dict[str, list[str]]):
        self.picks = picks  # first label of the spec -> labels to select
        self.calls = []

    @property
    def specs(self):
        return [spec for _, _, spec in self.calls]

    def __call__(self, title, text, spec):
        self.calls.append((title, text, spec))
        chosen = self.picks.get(next(iter(spec.labels)), [])
        return ClassificationResult(mode="multi_select", label=chosen, raw={})


def test_single_market_event_text_uses_the_market():
    title, text = event_text([MARKET])
    assert title == "Will Tesla deliver 500k cars in Q4?"
    assert "Outcome: Above 500,000" in text and "Tags: tesla, ev" in text and "Series" not in text


def test_single_market_falls_back_to_event_title():
    title, _ = event_text([{"market_id": "X", "event_title": "Fed decision"}])
    assert title == "Fed decision"


def test_multi_market_event_text_uses_the_event_and_lists_outcomes():
    title, text = event_text(LADDER)
    assert title == "BTC price on Oct 16, 2026 at 5pm EDT?"
    assert "Outcomes: $70,000 or above; $71,000 or above; $72,000 or above" in text


def test_outcome_list_is_capped():
    ladder = [{**LADDER[0], "market_id": f"M{i}", "outcome_label": f"${i:,} or above"} for i in range(1000)]
    _, text = event_text(ladder)
    outcomes = next(line for line in text.splitlines() if line.startswith("Outcomes: "))
    assert len(outcomes) <= len("Outcomes: ") + OUTCOMES_MAX_CHARS


def test_one_jev_run_per_event_however_many_markets():
    jev = FakeJev({"company": ["resource"], "Gold": []})
    enrich_event(LADDER, MAP, classify_fn=jev)
    assert len(jev.calls) == 2  # pass 1 + one category, not per market


def test_two_passes_only_ask_about_chosen_categories():
    jev = FakeJev({"company": ["company", "person"], "Tesla, Inc.": ["Tesla, Inc."], "Elon Musk": ["Elon Musk"]})

    symbols = enrich_event([MARKET], MAP, classify_fn=jev)

    assert symbols == ["TSLA", "Elon Musk"]
    assert list(jev.specs[0].labels) == list(CATEGORIES)
    assert [list(s.labels) for s in jev.specs[1:]] == [["Tesla, Inc.", "Apple Inc."], ["Elon Musk"]]
    assert jev.specs[1].question == ENTITY_QUESTION
    assert jev.specs[1].labels["Tesla, Inc."] == "also known as Tesla"


def test_no_categories_means_no_entities_and_one_call():
    jev = FakeJev({})
    assert enrich_event([MARKET], MAP, classify_fn=jev) == []
    assert len(jev.calls) == 1


def test_category_without_map_entities_is_skipped():
    jev = FakeJev({"company": ["event"]})
    assert enrich_event([MARKET], MAP, classify_fn=jev) == []
    assert len(jev.calls) == 1


def test_categories_use_the_lower_threshold_and_entities_the_stricter_one():
    jev = FakeJev({"company": ["company"]})
    enrich_event([MARKET], MAP, threshold=0.6, category_threshold=0.25, classify_fn=jev)
    assert jev.specs[0].threshold == 0.25
    assert jev.specs[1].threshold == 0.6


def test_country_question_counts_places_in_the_country():
    country_map = EntityMap(version=1, entities=[MapEntity(symbol="United States", name="United States",
                                                           category="country")])
    jev = FakeJev({"company": ["country"]})
    enrich_event([{"market_id": "W", "title": "Highest temperature in NYC?"}], country_map, classify_fn=jev)
    assert jev.specs[1].question == ENTITY_QUESTIONS["country"]
    assert "city" in ENTITY_QUESTIONS["country"] and "city" in CATEGORIES["country"]


def test_jev_errors_propagate_for_the_worker_to_record():
    def failing(title, text, spec):
        raise JevError("down")

    with pytest.raises(JevError):
        enrich_event([MARKET], MAP, classify_fn=failing)
