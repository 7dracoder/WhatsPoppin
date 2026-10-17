from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, insert, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from backend.models.base import Base
from company_graph.db import TABLES, create_tables
from company_graph.schemas import CONFIDENCE

GRAPH_TABLES = {"entities", "entity_relationships", "market_entities", "graph_company_profiles",
                "graph_link_runs", "graph_processed_filings", "graph_events", "graph_highlights",
                "graph_board_runs", "graph_board_seats"}
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    event.listen(eng, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    return eng


def test_creates_every_table_on_an_empty_database(engine):
    with engine.begin() as conn:
        create_tables(conn)
    assert set(inspect(engine).get_table_names()) == GRAPH_TABLES


def test_creates_tables_next_to_existing_market_tables_without_touching_them(engine):
    with engine.begin() as conn:  # stand-ins for other modules' tables, with a row each
        conn.execute(text("CREATE TABLE markets (source TEXT, market_id TEXT, title TEXT)"))
        conn.execute(text("CREATE TABLE alerts (id INTEGER PRIMARY KEY, summary TEXT)"))
        conn.execute(text("INSERT INTO markets VALUES ('kalshi', 'M1', 'Will Tesla ship?')"))
        conn.execute(text("INSERT INTO alerts VALUES (1, 'odds moved')"))
    with engine.begin() as conn:
        create_tables(conn)
        create_tables(conn)  # running twice is harmless
    assert set(inspect(engine).get_table_names()) == GRAPH_TABLES | {"markets", "alerts"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM markets")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM alerts")).scalar() == 1


def test_postgres_ddl_compiles_for_the_whole_shared_metadata():
    import backend.models  # noqa: F401  other modules' tables, so the full create_all is covered

    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=dialect))
    assert GRAPH_TABLES <= set(Base.metadata.tables)


def test_no_foreign_key_to_tables_reset_db_drops():
    for table in TABLES:
        for fk in table.foreign_keys:
            assert fk.column.table.name not in {"markets", "alerts"}, f"{table.name} -> {fk.target_fullname}"


def test_relationship_is_unique_per_pair_and_type_and_needs_known_entities(engine):
    from backend.models.entity import Entity
    from backend.models.entity_relationship import EntityRelationship

    with engine.begin() as conn:
        create_tables(conn)
        conn.execute(insert(Entity), [{"symbol": "TSLA", "name": "Tesla, Inc.", "type": "company"},
                                      {"symbol": "PCRFY", "name": "Panasonic", "type": "company"}])
        edge = {"entity_symbol": "TSLA", "related_entity_symbol": "PCRFY", "relationship_type": "supplier",
                "confidence": CONFIDENCE["filing"], "source": "filing", "summary": "Supplies cells.",
                "evidence_url": "https://www.sec.gov/Archives/x.htm"}
        conn.execute(insert(EntityRelationship), [edge])
        conn.execute(insert(EntityRelationship), [{**edge, "relationship_type": "partner"}])
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(EntityRelationship), [edge])
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(EntityRelationship), [{**edge, "related_entity_symbol": "NOPE"}])


def test_event_is_unique_per_company_and_url_and_highlights_cascade(engine):
    from backend.models.graph_event import GraphEvent
    from backend.models.graph_highlight import GraphHighlight

    with engine.begin() as conn:
        create_tables(conn)
        ev = {"entity_symbol": "TSLA", "source": "news", "event_type": "product_launch",
              "title": "Tesla launches X", "url": "https://news.example/x", "occurred_at": NOW}
        event_id = conn.execute(insert(GraphEvent).values(**ev).returning(GraphEvent.id)).scalar_one()
        conn.execute(insert(GraphHighlight).values(
            event_id=event_id, source_symbol="TSLA", target_symbol="PCRFY", direction="may_benefit",
            reason="Supplies the cells for X.", source_url=ev["url"], event_time=NOW))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(GraphEvent).values(**ev))
    with engine.begin() as conn:
        conn.execute(GraphEvent.__table__.delete())
        assert conn.execute(text("SELECT count(*) FROM graph_highlights")).scalar() == 0


def test_board_seat_is_unique_per_company_and_person_and_one_person_can_sit_on_two_boards(engine):
    from datetime import date

    from backend.models.graph_board_seat import GraphBoardSeat

    seat = {"company_symbol": "TSLA", "person_cik": 1001, "name": "Marigold Quillfeather",
            "raw_name": "QUILLFEATHER MARIGOLD", "role": "Director", "is_officer": False,
            "filed_at": date(2026, 8, 15), "evidence_url": "https://www.sec.gov/Archives/x.xml"}
    with engine.begin() as conn:
        create_tables(conn)
        conn.execute(insert(GraphBoardSeat), [seat])
        conn.execute(insert(GraphBoardSeat), [{**seat, "company_symbol": "NVDA"}])  # no entities row needed
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(GraphBoardSeat), [seat])
