"""Control-flow test of the Snowflake loader against a recording fake.
Proves ordering and the fail-closed behaviour; says nothing about Snowflake's
acceptance of the SQL (that needs a live account)."""
from __future__ import annotations

import json
from pathlib import Path

from sfbi import load_snowflake
from sfbi.fingerprints import FINGERPRINTS, TABLES


class FakeWH:
    def __init__(self, manifest, corrupt=None):
        self.log, self.manifest, self.corrupt = [], manifest, corrupt

    def execute(self, sql):
        self.log.append(sql)

    def query(self, sql):
        self.log.append(sql)
        if sql.startswith("SELECT COUNT(*) AS row_count"):
            table = sql.split(" FROM raw.")[1]
            m = self.manifest["tables"][table]
            cols = ["ROW_COUNT"] + [n.upper() for n, _ in FINGERPRINTS[table]]
            vals = [m["rows"]] + [m["fingerprints"][n] for n, _ in FINGERPRINTS[table]]
            if self.corrupt == table:
                vals[1] = "-1"
            return cols, [tuple(vals)]
        return ["check_name", "severity", "status", "failing_count"], [("x", "error", "PASS", 0)]


def _manifest():
    return {"tables": {t: {"rows": 10, "fingerprints": {n: "1" for n, _ in FINGERPRINTS[t]}} for t in TABLES}}


def test_happy_path_order(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(json.dumps(_manifest()))
    wh = FakeWH(_manifest())
    assert load_snowflake.run(wh, tmp_path, None, skip_setup=True) == 0
    joined = "\n".join(wh.log)
    assert joined.index("PUT 'file://") < joined.index("COPY INTO raw.dim_date")
    assert joined.index("COPY INTO raw.fact_macro_rate") < joined.index("INSERT INTO mart.load_log")
    assert joined.index("INSERT INTO mart.load_log") < joined.index("CREATE OR REPLACE VIEW model.")
    assert joined.count("TRUNCATE TABLE raw.") == len(TABLES)        # idempotent refresh
    assert "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in joined


def test_fingerprint_mismatch_stops_before_marts(tmp_path: Path, capsys):
    (tmp_path / "manifest.json").write_text(json.dumps(_manifest()))
    wh = FakeWH(_manifest(), corrupt="fact_price_daily")
    assert load_snowflake.run(wh, tmp_path, None, skip_setup=True) == 2
    assert not any("CREATE OR REPLACE TABLE mart.price_returns" in s for s in wh.log)
    assert "LOAD VERIFICATION FAILED" in capsys.readouterr().out


def test_skip_setup_does_not_force_a_warehouse_name(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(json.dumps(_manifest()))
    wh = FakeWH(_manifest())
    load_snowflake.run(wh, tmp_path, None, skip_setup=True)
    assert not any(s.startswith("USE WAREHOUSE") for s in wh.log)
    wh2 = FakeWH(_manifest())
    load_snowflake.run(wh2, tmp_path, None, skip_setup=False)
    assert any(s == "USE WAREHOUSE MARKETDATA_WH" for s in wh2.log)
