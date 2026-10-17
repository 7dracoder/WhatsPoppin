"""Backwards matching: an alert's market tags -> the companies it may affect (pure, no I/O).

Tier 1: company entities the market is tagged with. Enrichment already asked Jev, for every
        map company in a chosen category, "is this market about it, or would its outcome
        directly affect it?", so these are the directly affected companies.
Tier 2: companies the company graph links (from filings) to a tier-1 company: its suppliers,
        customers, partners and competitors.
Tier 3: companies Jev says the market's outcome would matter to (jev_match.py), for markets
        about things (gas prices, crude oil) rather than companies.
"""

from dataclasses import dataclass, field


@dataclass
class Impact:
    symbol: str
    tier: int
    matched: list[str] = field(default_factory=list)  # 1: the tag; 2: the tier-1 companies; 3: the market's tags
    confidence: float = 0.0  # tier 2: the strongest link's confidence; tier 3: Jev's probability
    relevance: float | None = None  # tier 3: Jev's probability, stored


def match_companies(tags: list[tuple[str, str]], links: list[tuple[str, str, float]], limit: int,
                    jev_hits: list[tuple[str, float]] = ()) -> list[Impact]:
    """`tags`: the market's (entity symbol, entity type). `links`: company graph edges as
    (company, other company, confidence), in either direction. `jev_hits`: (company, probability)
    from jev_match, for tier 3. Best first: tier, then how many tier-1 companies a tier-2 company
    links to, then confidence (link or Jev), then symbol.
    """
    tier1 = set(symbol for symbol, kind in tags if kind == "company")
    impacts = {s: Impact(s, 1, [s]) for s in sorted(tier1)}
    for a, b, confidence in links:
        for direct, other in ((a, b), (b, a)):
            if direct in tier1 and other not in tier1:
                impact = impacts.setdefault(other, Impact(other, 2))
                if direct not in impact.matched:
                    impact.matched.append(direct)
                impact.confidence = max(impact.confidence, confidence)
    why = [symbol for symbol, kind in tags if kind != "company"] or ["jev"]
    for symbol, probability in jev_hits:
        if symbol not in impacts:
            impacts[symbol] = Impact(symbol, 3, list(why), probability, round(probability, 3))
    ranked = sorted(impacts.values(), key=lambda i: (i.tier, -len(i.matched), -i.confidence, i.symbol))
    return ranked[:limit]
