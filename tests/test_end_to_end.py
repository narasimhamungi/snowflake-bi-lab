"""Postgres (real) -> export_gold (real) -> Parquet -> DuckDB running the SAME
SQL files and pipeline code that production runs against Snowflake.

What this proves: export correctness, cross-engine fingerprint equality, and
the logic of every MODEL/MART view and DQ check. What it does NOT prove:
that Snowflake accepts the SQL or loads the Parquet (see README, 'Unverified')."""
from __future__ import annotations

import json
import math
import statistics
from datetime import date
from pathlib import Path

import duckdb
import pytest

from sfbi.export_gold import export_all
from sfbi.fingerprints import TABLES
from sfbi.pipeline import build_and_check, run_script, utc_stamp, verify_load
from sfbi.recon_report import parse_report
from tests import synthetic_gold

pytestmark = pytest.mark.filterwarnings("ignore")


class DuckWarehouse:
    def __init__(self):
        self.con = duckdb.connect(":memory:")
        for s in ("raw", "model", "mart"):
            self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")

    def execute(self, sql):
        self.con.execute(sql)

    def query(self, sql):
        cur = self.con.execute(sql)
        return [d[0] for d in cur.description], cur.fetchall()

    def scalar(self, sql):
        return self.query(sql)[1][0][0]


@pytest.fixture()
def world(pg_conn, tmp_path):
    truth = synthetic_gold.build(pg_conn)
    manifest = export_all(pg_conn, tmp_path)
    wh = DuckWarehouse()
    run_script(wh, "10_raw_ddl.sql")
    run_script(wh, "25_audit_ddl.sql")
    for t in TABLES:
        wh.execute(f"COPY raw.{t} FROM '{(tmp_path / (t + '.parquet')).as_posix()}' (FORMAT PARQUET)")
    return dict(wh=wh, truth=truth, manifest=manifest, dir=tmp_path)


def checks(rows):
    return {name: (sev, status, failing) for name, sev, status, failing in rows}


# --------------------------------------------------------------------- export
def test_export_counts_and_manifest(world):
    t, m = world["truth"], world["manifest"]
    assert m["tables"]["fact_price_daily"]["rows"] == t["n_price_rows"]
    assert m["tables"]["fact_price_daily_consensus"]["rows"] == t["n_consensus"]
    assert m["tables"]["dim_security"]["rows"] == 5          # 4 current + 1 historical
    assert json.loads((world["dir"] / "manifest.json").read_text())["tables"].keys() == set(TABLES)


def test_verify_load_exact_match_across_engines(world):
    problems = verify_load(world["wh"], world["manifest"], "r1", utc_stamp())
    assert problems == []
    _, rows = world["wh"].query("SELECT table_name, fingerprint_match FROM mart.load_log")
    assert len(rows) == len(TABLES) and all(ok for _, ok in rows)


def test_verify_load_catches_silent_value_corruption(world):
    wh = world["wh"]
    # same row count, one price nudged by one micro-unit: row counts alone would miss this
    wh.execute("UPDATE raw.fact_price_daily SET adj_close = adj_close + 0.000001 "
               "WHERE security_key = 1 AND date_key = 20240102 AND source = 'yfinance'")
    problems = verify_load(wh, world["manifest"], "r2", utc_stamp())
    assert any("fact_price_daily: sum_adj_close" in p for p in problems)
    assert not any("row_count" in p for p in problems)


def test_verify_load_catches_dropped_rows(world):
    wh = world["wh"]
    wh.execute("DELETE FROM raw.fact_macro_rate WHERE series_id = 'DGS10'")
    problems = verify_load(wh, world["manifest"], "r3", utc_stamp())
    assert any(p.startswith("fact_macro_rate: row_count") for p in problems)


# ------------------------------------------------------------- marts + checks
def test_clean_load_passes_all_error_checks_and_surfaces_the_bad_print(world):
    res = checks(build_and_check(world["wh"], "r4", utc_stamp()))
    assert [n for n, (sev, st, _) in res.items() if sev == "error" and st != "PASS"] == []
    assert res["ohlc_low_le_open"] == ("warn", "WARN", 1)
    assert res["recon_flag_mismatch"][1] == "PASS"
    assert res["security_without_prices"][1] == "PASS"
    ticker, d = world["truth"]["bad_print"][0], world["truth"]["bad_print"][1]
    rows = world["wh"].query("SELECT ticker, trade_date, source, violated_rule FROM mart.v_price_anomalies")[1]
    assert rows == [(ticker, d, "yfinance", "low>open")]


def test_reconciliation_recomputed_matches_independent_truth(world):
    t, wh = world["truth"], world["wh"]
    build_and_check(wh, "r5", utc_stamp())
    s = wh.query("SELECT * FROM mart.v_recon_summary")
    row = dict(zip(*[s[0], s[1][0]]))
    assert row["compared_pairs"] == t["compared_pairs"]
    assert row["flagged_pairs"] == t["flagged_pairs"] and t["flagged_pairs"] > 0
    assert row["tickers_compared"] == t["tickers_compared"] == 3
    assert row["tickers_affected"] == t["tickers_affected"]
    assert row["only_yfinance"] == t["only_yfinance"]
    assert row["only_tiingo"] == t["only_tiingo"] == 2
    assert row["only_yfinance_unsampled_tickers"] == t["only_yfinance_unsampled"]
    assert row["only_yfinance_within_sampled_tickers"] == t["only_yfinance"] - t["only_yfinance_unsampled"] == 3
    per = dict(wh.query("SELECT ticker, flagged FROM mart.v_recon_by_ticker")[1])
    assert {k: int(v) for k, v in per.items()} == {k: v for k, v in t["flagged_by_ticker"].items() if k in per}


def test_returns_volatility_gap_handling_and_latest_price(world):
    t, wh = world["truth"], world["wh"]
    build_and_check(wh, "r6", utc_stamp())
    rows = wh.query("SELECT trade_date, adj_close, daily_return, vol_21d_ann, is_gap_return, days_since_prev "
                    "FROM mart.price_returns WHERE ticker = 'AAA' ORDER BY trade_date")[1]
    px = [float(r[1]) for r in rows]
    rets = [None] + [px[i] / px[i - 1] - 1 for i in range(1, len(px))]
    assert rows[0][2] is None
    for i in (1, 7, 40):
        assert rows[i][2] == pytest.approx(rets[i], rel=1e-9)
    assert all(r[3] is None for r in rows[:21])
    i = len(rows) - 1
    expected = statistics.stdev(rets[i - 20:i + 1]) * math.sqrt(252)
    assert rows[i][3] == pytest.approx(expected, rel=1e-9)

    ccc = wh.query("SELECT trade_date, is_gap_return, vol_21d_ann, days_since_prev FROM mart.price_returns "
                   "WHERE ticker = 'CCC' ORDER BY trade_date")[1]
    gap_idx = [i for i, r in enumerate(ccc) if r[1] == 1]
    assert len(gap_idx) == 1 and ccc[gap_idx[0]][3] > 5
    assert all(r[2] is None for r in ccc[gap_idx[0]:gap_idx[0] + 21])      # no vol across / right after a gap

    latest = wh.query("SELECT ticker, trade_date FROM mart.v_latest_price ORDER BY ticker")[1]
    assert [r[0] for r in latest] == ["AAA", "BBB", "CCC", "DDD"]
    assert dict(latest)["AAA"] == t["trading"][-1]


def test_model_view_has_flags_instead_of_arrays(world):
    wh = world["wh"]
    run_script(wh, "20_model.sql")
    both = wh.scalar("SELECT COUNT(*) FROM model.price_trusted WHERE has_yfinance = 1 AND has_tiingo = 1")
    assert both == world["truth"]["compared_pairs"]


# ------------------------------------------------------- checks must be able to fail
@pytest.mark.parametrize("breakage, check, expected_status", [
    ("INSERT INTO raw.fact_price_daily SELECT * FROM raw.fact_price_daily LIMIT 1", "pk_unique__fact_price_daily", "FAIL"),
    ("UPDATE raw.fact_price_daily SET security_key = 999 WHERE security_key = 1 AND date_key = 20240102 AND source = 'tiingo'",
     "fk_security__fact_price_daily", "FAIL"),
    ("UPDATE raw.dim_security SET is_current = TRUE WHERE effective_to IS NOT NULL", "one_current_row_per_ticker", "FAIL"),
    ("UPDATE raw.fact_price_daily SET adj_close = NULL WHERE security_key = 1 AND date_key = 20240102 AND source = 'yfinance'",
     "not_null__fact_price_daily_adj_close", "FAIL"),
    ("UPDATE raw.fact_price_daily_consensus SET reconciliation_flag = 'weird' WHERE security_key = 1 AND date_key = 20240102",
     "consensus_flag_domain", "FAIL"),
    ("UPDATE raw.fact_price_daily_consensus SET reconciliation_flag = 'agreed' WHERE reconciliation_flag = 'disagreed'",
     "consensus_flag_vs_tolerance", "FAIL"),
    ("DELETE FROM raw.fact_price_daily_consensus WHERE security_key = 1 AND date_key = 20240102",
     "consensus_covers_price_keys", "FAIL"),
    ("DELETE FROM raw.fact_price_daily WHERE source = 'tiingo' AND security_key = 1 AND date_key = 20240102",
     "recon_pair_count_match", "FAIL"),
    ("UPDATE raw.fact_price_daily SET high = low - 1 WHERE security_key = 4 AND date_key = 20240102",
     "ohlc_high_ge_low", "WARN"),
])
def test_each_check_actually_fires(world, breakage, check, expected_status):
    wh = world["wh"]
    wh.execute(breakage)
    res = checks(build_and_check(wh, "neg", utc_stamp()))
    assert res[check][1] == expected_status, res[check]


# -------------------------------------------------------------- report control
def _control_text(world, **override) -> str:
    t = world["truth"]
    flagged = {k: v for k, v in t["flagged_by_ticker"].items() if v}
    lines = [
        "Reconciliation report — ingest_date=2026-09-08",
        f"  Compared: {override.get('compared', t['compared_pairs'])} (ticker, date) pairs present in both sources",
        f"  Flagged (> 0.50% adj_close discrepancy): {t['flagged_pairs']} (1.00%)",
        f"  Present only in yfinance (coverage gap, not a discrepancy): {t['only_yfinance']}",
        f"  Present only in Tiingo (coverage gap, not a discrepancy): {t['only_tiingo']}",
        "",
        f"  Affected tickers: {t['tickers_affected']} of {t['tickers_compared']} compared — x",
        "  ticker    flagged  compared  flagged%  max diff  median diff  flagged window           ",
    ]
    for tk, n in flagged.items():
        lines.append(f"  {tk}   {n}   100   1.0%   3.00%   3.00%  2024-01-02 to 2024-01-29")
    return "\n".join(lines) + "\n"


def test_report_control_passes_when_report_matches_and_fails_when_it_does_not(world):
    wh = world["wh"]
    good = parse_report(_control_text(world))
    res = checks(build_and_check(wh, "ok", utc_stamp(), good))
    assert {k: v[1] for k, v in res.items() if k.startswith("report_control")} == {
        k: "PASS" for k in res if k.startswith("report_control")} and len(
        [k for k in res if k.startswith("report_control")]) == 7

    wh2 = DuckWarehouse()
    run_script(wh2, "10_raw_ddl.sql"); run_script(wh2, "25_audit_ddl.sql")
    for t in TABLES:
        wh2.execute(f"COPY raw.{t} FROM '{(world['dir'] / (t + '.parquet')).as_posix()}' (FORMAT PARQUET)")
    bad = parse_report(_control_text(world, compared=world["truth"]["compared_pairs"] + 5))
    res2 = checks(build_and_check(wh2, "bad", utc_stamp(), bad))
    assert res2["report_control__compared_pairs"] == ("error", "FAIL", 5)


def test_latest_run_views_show_only_the_most_recent_run(world):
    wh = world["wh"]
    verify_load(wh, world["manifest"], "run_old", "2026-10-01 10:00:00")
    build_and_check(wh, "run_old", "2026-10-01 10:00:00")
    verify_load(wh, world["manifest"], "run_new", "2026-10-02 10:00:00")
    build_and_check(wh, "run_new", "2026-10-02 10:00:00")
    assert {r[0] for r in wh.query("SELECT DISTINCT run_id FROM mart.v_load_latest")[1]} == {"run_new"}
    assert {r[0] for r in wh.query("SELECT DISTINCT run_id FROM mart.v_dq_latest")[1]} == {"run_new"}
    assert wh.scalar("SELECT COUNT(*) FROM mart.dq_results") == 2 * wh.scalar("SELECT COUNT(*) FROM mart.v_dq_latest")


# ------------------------------------------------ NaN in NUMERIC columns
def test_nan_becomes_null_is_counted_and_surfaces_as_a_warning(world):
    import pyarrow.parquet as pq

    t, wh, m = world["truth"], world["wh"], world["manifest"]
    assert m["tables"]["fact_price_daily"]["nan_to_null"] == t["nan_cols"]
    assert all(m["tables"][x]["nan_to_null"] == {} for x in TABLES if x != "fact_price_daily")

    tbl = pq.read_table(world["dir"] / "fact_price_daily.parquet")
    assert {c: tbl.column(c).null_count for c in t["nan_cols"]} == t["nan_cols"]

    # NULL counts arrive intact: per-column null fingerprints match across engines
    assert verify_load(wh, m, "nf1", utc_stamp()) == []
    assert m["tables"]["fact_price_daily"]["fingerprints"]["null_adj_open"] == "2"

    res = checks(build_and_check(wh, "nf2", utc_stamp()))
    assert res["null_price_fields"] == ("warn", "WARN", t["rows_with_null_price"])
    assert [n for n, (sev, st, _) in res.items() if sev == "error" and st != "PASS"] == []


def test_null_fingerprint_catches_a_load_that_turns_nulls_into_values(world):
    wh = world["wh"]
    wh.execute("UPDATE raw.fact_price_daily SET adj_open = 1.000000 WHERE adj_open IS NULL")
    problems = verify_load(wh, world["manifest"], "nf3", utc_stamp())
    assert any("fact_price_daily: null_adj_open" in p for p in problems)

