"""Shared fixtures. The Postgres fixture refuses to run against any database
whose name does not end in '_test': the tests TRUNCATE every gold table."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _pg_conn():
    psycopg2 = pytest.importorskip("psycopg2")
    name = os.environ.get("MDL_TEST_DB_NAME", "marketdata_lakehouse_test")
    if not name.endswith("_test"):
        pytest.skip(f"refusing to run against non-test database {name!r}")
    try:
        return psycopg2.connect(
            host=os.environ.get("MDL_DB_HOST", "localhost"),
            port=int(os.environ.get("MDL_DB_PORT", "5432")),
            dbname=name,
            user=os.environ.get("MDL_DB_USER", "mdl"),
            password=os.environ.get("MDL_DB_PASSWORD", "mdl_local_dev"),
        )
    except psycopg2.OperationalError as e:
        pytest.skip(f"no test Postgres available: {e}")


@pytest.fixture()
def pg_conn():
    conn = _pg_conn()
    with conn.cursor() as cur:
        cur.execute((FIXTURES / "lakehouse_gold_schema.sql").read_text())
        cur.execute(
            "TRUNCATE fact_macro_rate, fact_corporate_action, fact_price_daily_consensus, "
            "fact_price_daily, dim_security, dim_date RESTART IDENTITY CASCADE"
        )
    conn.commit()
    yield conn
    conn.close()
