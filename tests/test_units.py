from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sfbi import sqlrun
from sfbi.fingerprints import compare, normalise
from sfbi.recon_report import parse_report

FIX = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------ sqlrun
def test_split_ignores_semicolons_and_quotes_inside_comments_and_strings():
    sql = """-- Snowflake doesn't enforce this; really
    CREATE TABLE a (x VARCHAR);   /* it's; a block; comment */
    INSERT INTO a VALUES ('semi;colon', 'it''s');
    -- trailing comment only;
    """
    assert sqlrun.split_statements(sql) == [
        "-- Snowflake doesn't enforce this; really\n    CREATE TABLE a (x VARCHAR)",
        "/* it's; a block; comment */\n    INSERT INTO a VALUES ('semi;colon', 'it''s')",
    ]


def test_every_sql_file_splits_into_nonempty_statements():
    for p in sorted(sqlrun.SQL_DIR.glob("*.sql")):
        stmts = sqlrun.split_statements(p.read_text())
        assert stmts and all(s.strip() for s in stmts), p.name


def test_render_fills_and_rejects_unfilled_or_quoted_placeholders():
    assert sqlrun.render("a {{X}} b", {"X": "1"}) == "a 1 b"
    with pytest.raises(KeyError):
        sqlrun.render("a {{X}}", {})
    with pytest.raises(ValueError):
        sqlrun.render("a {{X}}", {"X": "1' OR '1'='1"})


def test_literal_escaping():
    assert sqlrun.literal("O'Neil") == "'O''Neil'"
    assert sqlrun.literal(None) == "NULL" and sqlrun.literal(True) == "TRUE"
    assert sqlrun.literal(date(2024, 1, 2)) == "CAST('2024-01-02' AS DATE)"


# ------------------------------------------------------------- fingerprints
def test_normalise_is_exact_and_representation_independent():
    assert normalise(Decimal("1.500000")) == normalise(Decimal("1.5")) == "1.5"
    assert normalise(Decimal("0.000000")) == "0" and normalise(0) == "0"
    assert normalise(Decimal("1E+2")) == "100" and normalise(None) is None
    assert normalise(Decimal("123456789012.123456")) == "123456789012.123456"


def test_compare_reports_each_mismatch():
    assert compare({"a": "1", "b": "2"}, {"a": "1", "b": "2"}) == []
    assert len(compare({"a": "1", "b": "2"}, {"a": "1", "b": "3", "c": "9"})) == 2


# ----------------------------------------- the two real committed reports
def test_parse_real_report_2026_09_08():
    r = parse_report((FIX / "reconciliation_report_2026-09-08.txt").read_text(encoding="utf-8"))
    assert r["report_ingest_date"] == date(2026, 9, 8)
    assert (r["compared_pairs"], r["flagged_pairs"]) == (63575, 1262)
    assert (r["only_yfinance"], r["only_tiingo"]) == (635830, 15361)
    assert (r["tickers_affected"], r["tickers_compared"]) == (3, 34)
    assert {t["ticker"]: (t["flagged"], t["compared"]) for t in r["tickers"]} == {
        "T": (761, 1932), "IFF": (500, 1932), "GRMN": (1, 1932)}
    assert next(t for t in r["tickers"] if t["ticker"] == "T")["last_flagged"] == date(2022, 1, 6)


def test_parse_real_report_2026_09_07():
    r = parse_report((FIX / "reconciliation_report_2026-09-07.txt").read_text(encoding="utf-8"))
    assert (r["compared_pairs"], r["flagged_pairs"]) == (192037, 9094)
    assert (r["only_yfinance"], r["only_tiingo"]) == (758785, 0)
    assert (r["tickers_affected"], r["tickers_compared"]) == (10, 101)
    assert len(r["tickers"]) == 10 and sum(t["flagged"] for t in r["tickers"]) == 9094


def test_parser_refuses_to_guess():
    with pytest.raises(ValueError):
        parse_report("ingest_date=2026-09-08\nnothing else")
