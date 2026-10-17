from backend.entities import load_entities


def test_load_entities_returns_seed_list():
    entities = load_entities()
    assert len(entities) == 100


def test_load_entities_has_expected_fields():
    entities = load_entities()
    by_symbol = {e.symbol: e for e in entities}

    nvda = by_symbol["NVDA"]
    assert "Nvidia" in nvda.aliases

    no_duplicate_symbols = len(by_symbol) == len(entities)
    assert no_duplicate_symbols
