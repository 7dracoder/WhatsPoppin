from backend.alert_impact.match import match_companies


def test_tagged_companies_are_tier_one_and_graph_links_tier_two():
    tags = [("NVDA", "company"), ("Semiconductors", "sector"), ("Taiwan", "country")]
    links = [("NVDA", "TSM", 0.9), ("MU", "NVDA", 0.9), ("AAPL", "TSM", 0.9)]  # AAPL-TSM: not linked to NVDA
    impacts = match_companies(tags, links, 10)
    assert [(i.symbol, i.tier, i.matched) for i in impacts] == [
        ("NVDA", 1, ["NVDA"]), ("MU", 2, ["NVDA"]), ("TSM", 2, ["NVDA"])]


def test_links_between_two_tagged_companies_stay_tier_one():
    impacts = match_companies([("XOM", "company"), ("CVX", "company")], [("XOM", "CVX", 0.9)], 10)
    assert [(i.symbol, i.tier) for i in impacts] == [("CVX", 1), ("XOM", 1)]


def test_tier_two_ranks_by_links_then_confidence_and_respects_limit():
    tags = [("XOM", "company"), ("CVX", "company")]
    links = [("XOM", "SLB", 0.3), ("CVX", "SLB", 0.3), ("XOM", "HAL", 0.9), ("XOM", "BKR", 0.5)]
    impacts = match_companies(tags, links, 4)
    assert [(i.symbol, i.matched) for i in impacts] == [
        ("CVX", ["CVX"]), ("XOM", ["XOM"]), ("SLB", ["XOM", "CVX"]), ("HAL", ["XOM"])]


def test_no_company_tags_means_no_impacts():
    assert match_companies([("Crude Oil", "resource")], [("XOM", "CVX", 0.9)], 10) == []


def test_jev_hits_are_tier_three_below_tags_and_links_ranked_by_probability():
    tags = [("Gasoline", "resource"), ("United States", "country")]
    impacts = match_companies(tags, [], 10, jev_hits=[("XOM", 0.91), ("CVX", 0.97)])
    assert [(i.symbol, i.tier, i.matched, i.relevance) for i in impacts] == [
        ("CVX", 3, ["Gasoline", "United States"], 0.97), ("XOM", 3, ["Gasoline", "United States"], 0.91)]


def test_jev_hits_never_demote_a_tagged_or_linked_company():
    impacts = match_companies([("NVDA", "company")], [("NVDA", "TSM", 0.9)], 10,
                              jev_hits=[("NVDA", 0.99), ("TSM", 0.95), ("AMD", 0.8)])
    assert [(i.symbol, i.tier) for i in impacts] == [("NVDA", 1), ("TSM", 2), ("AMD", 3)]
