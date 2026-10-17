import time

from company_graph.trim import (
    DEFAULT_PHRASES,
    MAX_CHUNKS,
    Chunk,
    trim_by_name,
    trim_by_phrases,
)

FILLER = "x" * 10_000


def test_no_hits_returns_empty():
    assert trim_by_phrases("nothing relevant here " * 100) == []
    assert trim_by_name("nothing relevant here " * 100, ["Acme Corp"]) == []


def test_empty_inputs():
    assert trim_by_phrases("", DEFAULT_PHRASES) == []
    assert trim_by_phrases("supplier", []) == []
    assert trim_by_name("Acme", ["", "  "]) == []


def test_single_hit_window_and_offset():
    text = FILLER + "our sole source of chips" + FILLER
    hit = text.index("sole source")
    [chunk] = trim_by_phrases(text, radius=100)
    assert chunk.offset == hit - 100
    assert chunk.text == text[chunk.offset : chunk.offset + len(chunk.text)]
    assert len(chunk.text) == len("sole source") + 200


def test_window_clipped_at_text_edges():
    text = "supplier" + "y" * 50
    [chunk] = trim_by_phrases(text, radius=1500)
    assert chunk == Chunk(0, text)


def test_overlapping_hits_merge_into_one_chunk():
    text = FILLER + "supplier" + "z" * 300 + "competitors" + FILLER
    first = text.index("supplier")
    second = text.index("competitors")
    chunks = trim_by_phrases(text, radius=200)
    assert len(chunks) == 1
    [chunk] = chunks
    assert chunk.offset == first - 200
    assert chunk.offset + len(chunk.text) == second + len("competitors") + 200
    assert "supplier" in chunk.text and "competitors" in chunk.text


def test_far_apart_hits_stay_separate():
    text = FILLER + "supplier" + FILLER + "competitors" + FILLER
    chunks = trim_by_phrases(text, radius=200)
    assert [c.offset for c in chunks] == [
        text.index("supplier") - 200,
        text.index("competitors") - 200,
    ]
    for c in chunks:
        assert text[c.offset : c.offset + len(c.text)] == c.text


def test_case_insensitive_and_percent_phrase():
    text = FILLER + "Customer A ACCOUNTED FOR 14% of Revenue in 2025" + FILLER
    chunks = trim_by_phrases(text, radius=50)
    assert len(chunks) == 1
    assert "14% of Revenue" in chunks[0].text


def test_cap_at_twelve_chunks_keeps_busiest_in_order():
    pieces = []
    for i in range(20):
        # Chunks 05 and 15 have three hits; the rest have one.
        hits = "supplier supplier supplier" if i in (5, 15) else "supplier"
        pieces.append(f"[{i:02d}] {hits}")
    text = FILLER.join([""] + pieces + [""])
    chunks = trim_by_phrases(text, radius=100)
    assert len(chunks) == MAX_CHUNKS
    offsets = [c.offset for c in chunks]
    assert offsets == sorted(offsets)
    labels = [c.text[c.text.index("[") + 1 : c.text.index("]")] for c in chunks]
    # The two busy chunks survive, then the earliest single-hit ones fill the rest.
    assert labels == ["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "15"]


def test_trim_by_name_whole_word_and_variants():
    text = (
        FILLER + " we buy from Acme Corp. " + FILLER
        + " pineapple acmecorp nothing " + FILLER
        + " ACME CORPORATION signed " + FILLER
    )
    chunks = trim_by_name(text, ["Acme Corp", "Acme Corporation"], radius=20)
    assert len(chunks) == 2
    assert "Acme Corp." in chunks[0].text
    assert "ACME CORPORATION" in chunks[1].text


def test_name_does_not_match_inside_word():
    text = FILLER + "Pineapple sales rose" + FILLER
    assert trim_by_name(text, ["Apple"]) == []


def test_300_page_filing_trims_under_one_second():
    # ~300 pages at ~3,500 characters a page: about 1M characters.
    page = (
        "Item 1A. Risk Factors. The Company operates in a highly regulated market "
        "and its results may vary from period to period for many reasons. "
    ) * 25
    pages = []
    for i in range(300):
        extra = ""
        if i % 7 == 0:
            extra = " One supplier accounted for 18% of net sales. We compete with Acme Corp. "
        pages.append(page + extra)
    text = "\n".join(pages)
    assert len(text) >= 1_000_000

    start = time.perf_counter()
    by_phrase = trim_by_phrases(text)
    by_name = trim_by_name(text, ["Acme Corp", "Acme Corporation", "Acme"])
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert 0 < len(by_phrase) <= MAX_CHUNKS
    assert 0 < len(by_name) <= MAX_CHUNKS
    for c in by_phrase + by_name:
        assert text[c.offset : c.offset + len(c.text)] == c.text
