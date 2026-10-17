"""Free-text tags ("ev", "electric vehicle", "microchips") -> entities in the map.

Markets are tagged with map entities only, so a search tag has to become a map entity before
it can match anything. Exact matches (name, ticker or company alias, any case) are free. The
rest go to Jev in one call: for each tag, a Choice question over the map's entities plus
"none". Jev's Choice takes at most 255 options and the map has ~300, so each tag gets one
question per half of the map and the more confident non-"none" pick wins.

Synchronous, like classify(); call it through asyncio.to_thread from async code.
"""

from backend.classification.jev_client import call_jev
from backend.classification.primitives import ChoiceQuestion
from backend.entities import EntityMap, MapEntity

NONE = "none of these"
MAX_OPTIONS = 254  # Jev's 255, minus NONE
THRESHOLD = 0.5


def _exact(entity_map: EntityMap) -> dict[str, MapEntity]:
    lookup: dict[str, MapEntity] = {}
    for e in entity_map.entities:
        for key in (e.symbol, e.name, *e.aliases):
            lookup.setdefault(key.casefold(), e)
    return lookup


def _halves(entity_map: EntityMap) -> list[list[MapEntity]]:
    half = -(-len(entity_map.entities) // 2)
    chunks = [entity_map.entities[i:i + half] for i in range(0, len(entity_map.entities), half)]
    assert all(len(c) <= MAX_OPTIONS for c in chunks), "map too big for two Choice questions per tag"
    return chunks


def match_tags(tags: list[str], entity_map: EntityMap, threshold: float = THRESHOLD,
               call_jev_fn=call_jev) -> dict[str, MapEntity | None]:
    """Each tag -> its map entity, or None when nothing in the map fits. Raises JevError if
    the Jev call fails. Makes no Jev call when every tag matches exactly.
    """
    exact = _exact(entity_map)
    result: dict[str, MapEntity | None] = {}
    fuzzy = []
    for tag in dict.fromkeys(t.strip() for t in tags if t.strip()):
        if tag.casefold() in exact:
            result[tag] = exact[tag.casefold()]
        else:
            fuzzy.append(tag)
    if not fuzzy:
        return result

    halves = _halves(entity_map)
    by_name = {e.name: e for e in entity_map.entities}
    questions = {}
    for i, tag in enumerate(fuzzy):
        for j, half in enumerate(halves):
            criteria = {e.name: f"{e.category}" + (f", also known as {', '.join(e.aliases)}" if e.aliases else "")
                        for e in half}
            questions[f"t{i}h{j}"] = ChoiceQuestion(
                instructions=f"A user searched prediction markets for {tag!r}. Which option is the same thing "
                             f"(a synonym, abbreviation, spelling or close name)? If none is, pick {NONE!r}.",
                criteria={**criteria, NONE: "the search term is not any of these"},
            )
    answers = call_jev_fn("Search terms: " + "; ".join(fuzzy), questions)

    for i, tag in enumerate(fuzzy):
        best, best_p = None, threshold
        for j in range(len(halves)):
            answer = answers[f"t{i}h{j}"]
            p = answer.probabilities.get(answer.choice, 0.0)
            if answer.choice != NONE and answer.choice in by_name and p >= best_p:
                best, best_p = by_name[answer.choice], p
        result[tag] = best
    return result
