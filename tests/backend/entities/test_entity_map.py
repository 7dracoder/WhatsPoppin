import json

import pytest

from backend.entities import CATEGORIES, load_entities, load_entity_map


def test_seed_map_has_about_fifty_entities_in_every_category():
    # Companies are the exception: the list grew to 100 so names people type ("Intel", "TSMC")
    # resolve and the graph's neighbourhood is tagged; enrichment batches 50 per Jev call, so
    # that is one extra call per market, an accepted trade-off.
    entity_map = load_entity_map()
    for category in CATEGORIES:
        upper = 110 if category == "company" else 60
        assert 40 <= len(entity_map.in_category(category)) <= upper, category


def test_companies_come_from_the_shared_company_list():
    companies = load_entity_map().in_category("company")
    assert [c.symbol for c in companies] == [e.symbol for e in load_entities()]
    tesla = next(c for c in companies if c.symbol == "TSLA")
    assert tesla.name == "Tesla, Inc." and "Tesla" in tesla.aliases


def test_non_company_symbol_is_the_name():
    gold = next(e for e in load_entity_map().entities if e.name == "Gold")
    assert (gold.symbol, gold.category) == ("Gold", "resource")


def _write_map(tmp_path, categories, companies=None):
    (tmp_path / "companies.json").write_text(json.dumps(companies or [
        {"symbol": "TSLA", "name": "Tesla, Inc.", "aliases": ["Tesla"]}]))
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"map_version": 3, "companies_file": "companies.json", "categories": categories}))
    return path


def test_loads_version_and_categories(tmp_path):
    entity_map = load_entity_map(_write_map(tmp_path, {"resource": ["Gold"]}))
    assert entity_map.version == 3
    assert entity_map.symbols == ["TSLA", "Gold"]


@pytest.mark.parametrize("categories", [
    {"planet": ["Mars"]},                    # unknown category
    {"resource": ["Gold", "gold"]},          # duplicate name, case-insensitive
    {"sector": ["Oil/Gas"]},                 # "/" breaks the URL path
])
def test_rejects_bad_maps(tmp_path, categories):
    with pytest.raises(ValueError):
        load_entity_map(_write_map(tmp_path, categories))

