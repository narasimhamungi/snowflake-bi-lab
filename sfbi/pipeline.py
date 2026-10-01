"""Engine-independent pipeline stages: verify a load, build marts, run checks."""
from __future__ import annotations

from datetime import datetime, timezone

from sfbi.fingerprints import TABLES, compare, fingerprint_row_to_dict, fingerprint_sql
from sfbi.sqlrun import literal, load_script
from sfbi.warehouse import Warehouse


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def run_script(wh: Warehouse, name: str, params: dict[str, str] | None = None) -> None:
    for stmt in load_script(name, params):
        wh.execute(stmt)


def verify_load(wh: Warehouse, manifest: dict, run_id: str, checked_at: str) -> list[str]:
    """Recompute row counts + fingerprints in the warehouse's RAW tables,
    compare to the export manifest (computed in Postgres), write load_log.
    Returns a list of problems; empty means every table matched exactly."""
    problems: list[str] = []
    for table in TABLES:
        expected = manifest["tables"][table]
        cols, rows = wh.query(fingerprint_sql(table, "raw."))
        observed = fingerprint_row_to_dict(cols, rows[0])
        loaded = int(observed.pop("row_count"))
        issues = []
        if loaded != expected["rows"]:
            issues.append(f"row_count: expected {expected['rows']}, got {loaded}")
        issues += compare(expected["fingerprints"], observed)
        detail = "; ".join(issues)
        wh.execute(
            "INSERT INTO mart.load_log VALUES ("
            + ", ".join([
                literal(run_id), literal(table), literal(expected["rows"]), literal(loaded),
                literal(not issues), literal(detail or None),
                f"CAST('{checked_at}' AS TIMESTAMP)",
            ])
            + ")"
        )
        problems += [f"{table}: {i}" for i in issues]
    return problems


def load_report_control(wh: Warehouse, report: dict) -> None:
    d = report["report_ingest_date"]
    wh.execute(f"DELETE FROM mart.recon_report_control WHERE report_ingest_date = {literal(d)}")
    wh.execute(f"DELETE FROM mart.recon_report_control_ticker WHERE report_ingest_date = {literal(d)}")
    wh.execute(
        "INSERT INTO mart.recon_report_control VALUES ("
        + ", ".join(literal(report[k]) for k in (
            "report_ingest_date", "compared_pairs", "flagged_pairs", "only_yfinance",
            "only_tiingo", "tickers_compared", "tickers_affected"))
        + ")"
    )
    for t in report["tickers"]:
        wh.execute(
            "INSERT INTO mart.recon_report_control_ticker VALUES ("
            + ", ".join(literal(v) for v in (d, t["ticker"], t["flagged"], t["compared"],
                                             t["first_flagged"], t["last_flagged"]))
            + ")"
        )


def build_and_check(wh: Warehouse, run_id: str, checked_at: str, report: dict | None = None) -> list[tuple]:
    """MODEL views -> MART models -> DQ checks (+ optional report control).
    Returns the dq_results rows for this run: (check_name, severity, status, failing_count)."""
    run_script(wh, "20_model.sql")
    run_script(wh, "30_mart.sql")
    params = {"RUN_ID": run_id, "CHECKED_AT": checked_at}
    run_script(wh, "40_dq_checks.sql", params)
    if report is not None:
        load_report_control(wh, report)
        run_script(wh, "50_dq_report_control.sql",
                   {**params, "REPORT_DATE": report["report_ingest_date"].isoformat()})
    _, rows = wh.query(
        "SELECT check_name, severity, status, failing_count FROM mart.dq_results "
        f"WHERE run_id = {literal(run_id)} ORDER BY check_name"
    )
    return rows
