"""
Dry run: execute the full warehouse pipeline on the real export, locally, in DuckDB.

    python -m sfbi.dryrun_duckdb --export export [--report-file ...] [--db dryrun.duckdb]

Same SQL files and pipeline code the Snowflake run uses (load -> fingerprint
verification -> MODEL -> MART -> DQ checks). Proves the logic on real data
before a Snowflake trial clock is running. It proves nothing about Snowflake's
SQL dialect, PUT/COPY or authentication: those stay unverified until the live run.

Exit code: 0 all error-severity checks pass, 1 a check failed, 2 load verification failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sfbi.fingerprints import TABLES
from sfbi.pipeline import build_and_check, run_script, utc_stamp, verify_load
from sfbi.recon_report import parse_report
from sfbi.warehouse import DuckDBWarehouse


def _print_table(wh, title: str, sql: str) -> None:
    cols, rows = wh.query(sql)
    print(f"\n{title}")
    if not rows:
        print("  (none)")
    for r in rows:
        print("  " + "  ".join(f"{c}={v}" for c, v in zip(cols, r)))


def run(export_dir: Path, report_file: Path | None = None, db: str = ":memory:") -> int:
    manifest = json.loads((export_dir / "manifest.json").read_text())
    wh = DuckDBWarehouse(db)
    run_script(wh, "10_raw_ddl.sql")
    run_script(wh, "25_audit_ddl.sql")
    for t in TABLES:
        wh.execute(f"DELETE FROM raw.{t}")
        wh.execute(f"COPY raw.{t} FROM '{(export_dir / (t + '.parquet')).resolve().as_posix()}' (FORMAT PARQUET)")

    run_id, stamp = "dryrun", utc_stamp()
    problems = verify_load(wh, manifest, run_id, stamp)
    if problems:
        print("LOAD VERIFICATION FAILED:", *problems, sep="\n  ")
        return 2
    print(f"load verified: {len(TABLES)} tables, row counts + fingerprints match the Postgres manifest exactly")

    report = parse_report(report_file.read_text(encoding="utf-8")) if report_file else None
    results = build_and_check(wh, run_id, stamp, report)
    counts: dict[str, int] = {}
    for _, _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1
    print(f"checks: {counts}")
    for name, sev, status, failing in results:
        if status != "PASS":
            print(f"  {status:<4} {name}  failing={failing}  severity={sev}")

    _print_table(wh, "reconciliation (recomputed in the warehouse):", "SELECT * FROM mart.v_recon_summary")
    _print_table(wh, "rows with a NULL price field, by ticker/source:",
                 "SELECT ticker, source, COUNT(*) AS n_rows, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date "
                 "FROM model.price_daily WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL "
                 "OR adj_open IS NULL OR adj_high IS NULL OR adj_low IS NULL OR adj_close IS NULL "
                 "GROUP BY ticker, source ORDER BY n_rows DESC")
    _print_table(wh, "current securities with no price rows:",
                 "SELECT s.ticker FROM mart.dim_security_current s WHERE NOT EXISTS "
                 "(SELECT 1 FROM raw.fact_price_daily f WHERE f.security_key = s.security_key) ORDER BY s.ticker")
    _print_table(wh, "OHLC ordering anomalies:",
                 "SELECT ticker, trade_date, source, violated_rule FROM mart.v_price_anomalies ORDER BY trade_date LIMIT 10")
    return 1 if counts.get("FAIL") else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--export", default="export", type=Path)
    ap.add_argument("--report-file", type=Path, default=None)
    ap.add_argument("--db", default=":memory:", help="DuckDB file to keep the result (default: in-memory)")
    args = ap.parse_args(argv)
    return run(args.export, args.report_file, args.db)


if __name__ == "__main__":
    sys.exit(main())
