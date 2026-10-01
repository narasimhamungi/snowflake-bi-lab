-- Audit/control tables in MART (Power BI page 3 and page 2 read these).
CREATE TABLE IF NOT EXISTS mart.load_log (
    run_id VARCHAR, table_name VARCHAR, rows_expected BIGINT, rows_loaded BIGINT,
    fingerprint_match BOOLEAN, mismatch_detail VARCHAR, loaded_at_utc TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mart.dq_results (
    run_id VARCHAR, check_name VARCHAR, table_name VARCHAR, severity VARCHAR, status VARCHAR,
    failing_count BIGINT, checked_rows BIGINT, description VARCHAR, checked_at_utc TIMESTAMP
);
-- Headline and per-ticker figures parsed from the lakehouse's committed
-- reconciliation_report_<date>.txt: an external control total.
CREATE TABLE IF NOT EXISTS mart.recon_report_control (
    report_ingest_date DATE, compared_pairs BIGINT, flagged_pairs BIGINT,
    only_yfinance BIGINT, only_tiingo BIGINT, tickers_compared INTEGER, tickers_affected INTEGER
);
CREATE TABLE IF NOT EXISTS mart.recon_report_control_ticker (
    report_ingest_date DATE, ticker VARCHAR, flagged BIGINT, compared BIGINT,
    first_flagged DATE, last_flagged DATE
);
