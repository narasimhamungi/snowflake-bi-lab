"""
Load the exported gold layer into Snowflake, verify it, model it, check it.

    python -m sfbi.load_snowflake --export export --report-file path/to/reconciliation_report_2026-09-08.txt

Idempotent full refresh: each RAW table is TRUNCATEd and re-COPYed from the
export, so a rerun reproduces the same state. The source is a complete
snapshot export, so MERGE would add complexity without adding correctness
(see docs/decision_log.md).

STATUS: written and unit-tested against a recording fake; NOT yet executed
against a live Snowflake account. The first real run is the real test.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from sfbi.fingerprints import TABLES
from sfbi.pipeline import build_and_check, run_script, utc_stamp, verify_load
from sfbi.recon_report import parse_report
from sfbi.warehouse import SnowflakeWarehouse, Warehouse, snowflake_connection


def stage_and_copy(wh: Warehouse, export_dir: Path, run_id: str) -> None:
    for table in TABLES:
        path = (export_dir / f"{table}.parquet").resolve().as_posix()
        wh.execute(
            f"PUT 'file://{path}' @RAW.GOLD_STAGE/{run_id}/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        )
        wh.execute(f"TRUNCATE TABLE raw.{table}")
        wh.execute(
            f"COPY INTO raw.{table} FROM @RAW.GOLD_STAGE/{run_id}/{table}.parquet "
            "FILE_FORMAT = (FORMAT_NAME = 'RAW.PARQUET_FF') "
            "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = ABORT_STATEMENT"
        )


def run(wh: Warehouse, export_dir: Path, report_file: Path | None, skip_setup: bool) -> int:
    manifest = json.loads((export_dir / "manifest.json").read_text())
    report = parse_report(report_file.read_text(encoding="utf-8")) if report_file else None
    run_id = uuid.uuid4().hex[:12]
    stamp = utc_stamp()

    if not skip_setup:
        run_script(wh, "00_setup.sql")
        wh.execute("USE WAREHOUSE MARKETDATA_WH")   # created by 00_setup.sql
    wh.execute("USE DATABASE MARKETDATA")           # with --skip-setup the connection's warehouse is used
    run_script(wh, "10_raw_ddl.sql")
    run_script(wh, "25_audit_ddl.sql")

    stage_and_copy(wh, export_dir, run_id)
    problems = verify_load(wh, manifest, run_id, stamp)
    if problems:
        print("LOAD VERIFICATION FAILED - marts not built:", *problems, sep="\n  ")
        return 2

    results = build_and_check(wh, run_id, stamp, report)
    counts: dict[str, int] = {}
    for _, _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1
    print(f"run_id={run_id}  checks: {counts}")
    for name, sev, status, failing in results:
        if status != "PASS":
            print(f"  {status:<4} {name} (failing={failing}, severity={sev})")
    return 1 if counts.get("FAIL") else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--export", default="export", type=Path)
    ap.add_argument("--report-file", type=Path, default=None,
                    help="lakehouse reconciliation_report_<date>.txt for the SAME ingest date as the gold build")
    ap.add_argument("--skip-setup", action="store_true", help="skip 00_setup.sql (warehouse/db already exist)")
    args = ap.parse_args(argv)
    conn = snowflake_connection()
    try:
        return run(SnowflakeWarehouse(conn), args.export, args.report_file, args.skip_setup)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
