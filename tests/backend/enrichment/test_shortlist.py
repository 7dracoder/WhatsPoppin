import numpy as np

from backend.classification import ClassificationResult, MultiSelectSpec
from backend.enrichment.enrich import enrich_event_shortlist
from backend.enrichment.vectors import EntityIndex, entity_text
from backend.entities import EntityMap, MapEntity

MAP = EntityMap(version=1, entities=[
    MapEntity(symbol="NVDA", name="NVIDIA", category="company", aliases=["Nvidia"]),
    MapEntity(symbol="AMD", name="AMD", category="company"),
    MapEntity(symbol="United States", name="United States", category="country"),
    MapEntity(symbol="China", name="China", category="country"),
])
VECTORS = {"NVIDIA": [1, 0], "AMD": [0.8, 0.6], "United States": [0, 1], "China": [0.6, 0.8]}


def fake_embed(texts, input_type="document", model=None):
    rows = [next(v for name, v in VECTORS.items() if text.startswith(name)) for text in texts]
    return np.asarray(rows, dtype=np.float32)


def test_entity_text_carries_ticker_category_and_aliases():
    text = entity_text(MAP.entities[0])
    assert text.startswith("NVIDIA (NVDA): a specific publicly traded company") and "Nvidia" in text


def test_shortlist_is_per_category():
    index = EntityIndex(MAP, embed_fn=fake_embed)
    event = np.array([1, 0], dtype=np.float32)
    assert [e.symbol for e in index.shortlist(event, 1)] == ["NVDA"]
    assert [e.symbol for e in index.shortlist(event, 1, ["company", "country"])] == ["NVDA", "China"]


def test_enrich_event_shortlist_makes_two_calls_and_tags_only_candidates():
    specs = []

    def classify_fn(title, text, spec: MultiSelectSpec):
        specs.append(spec)
        if len(specs) == 1:  # category pass
            return ClassificationResult(mode="multi_select", label=["company", "country"], raw={})
        return ClassificationResult(mode="multi_select", label=["NVIDIA", "China", "Not offered"], raw={})

    index = EntityIndex(MAP, embed_fn=fake_embed)
    vector = np.array([1, 0], dtype=np.float32)
    markets = [{"market_id": "m1", "title": "Will the US restrict NVIDIA chip sales to China?"}]
    symbols = enrich_event_shortlist(markets, lambda cats, k: index.shortlist(vector, k, cats), k=1,
                                     classify_fn=classify_fn)
    assert len(specs) == 2
    assert set(specs[1].labels) == {"NVIDIA", "China"}  # one candidate per chosen category
    assert symbols == ["NVDA", "China"]  # a name Jev made up is never tagged


def test_no_category_means_no_second_call():
    calls = []

    def classify_fn(*args):
        calls.append(args)
        return ClassificationResult(mode="multi_select", label=[], raw={})

    assert enrich_event_shortlist([{"market_id": "m", "title": "t"}], lambda c, k: [], classify_fn=classify_fn) == []
    assert len(calls) == 1
