"""Dry-run entry point on the synthetic gold layer, plus the rounding edge case
that motivated the strict '<' in consensus_flag_vs_tolerance."""
from __future__ import annotations

from sfbi import dryrun_duckdb
from sfbi.pipeline import build_and_check, utc_stamp
from tests.test_end_to_end import checks, world  # noqa: F401  (world is a fixture)


def test_dryrun_on_export_passes_and_reports(world, capsys):
    assert dryrun_duckdb.run(world["dir"]) == 0
    out = capsys.readouterr().out
    assert "load verified: 6 tables" in out
    assert "compared_pairs=" + str(world["truth"]["compared_pairs"]) in out
    assert "ticker=DDD  source=yfinance  n_rows=3" in out
    assert "violated_rule=low>open" in out


def test_dryrun_reports_load_failure(world, tmp_path):
    (tmp_path / "manifest.json").write_text((world["dir"] / "manifest.json").read_text().replace(
        '"rows": %d' % world["truth"]["n_price_rows"], '"rows": %d' % (world["truth"]["n_price_rows"] + 1)))
    for p in world["dir"].glob("*.parquet"):
        (tmp_path / p.name).write_bytes(p.read_bytes())
    assert dryrun_duckdb.run(tmp_path) == 2


def test_disagreed_row_rounded_onto_the_threshold_is_not_a_failure(world):
    wh = world["wh"]
    _, rows = wh.query("SELECT security_key, date_key FROM raw.fact_price_daily_consensus "
                       "WHERE reconciliation_flag = 'disagreed' ORDER BY security_key, date_key LIMIT 1")
    sk, dk = rows[0]
    wh.execute(f"UPDATE raw.fact_price_daily_consensus SET pct_diff = 0.005000 WHERE security_key = {sk} AND date_key = {dk}")
    assert checks(build_and_check(wh, "edge", utc_stamp()))["consensus_flag_vs_tolerance"][1] == "PASS"
