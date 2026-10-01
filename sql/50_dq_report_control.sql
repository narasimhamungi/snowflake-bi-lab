-- Optional: compare the warehouse's recomputed reconciliation against the
-- figures printed in the lakehouse's committed report for the SAME ingest
-- date the gold layer was built from. Placeholders: {{RUN_ID}},
-- {{CHECKED_AT}}, {{REPORT_DATE}} (YYYY-MM-DD). Rows are only meaningful if
-- the report date matches the gold build; if it does not, the checks fail,
-- which is the intended signal.
INSERT INTO mart.dq_results
SELECT '{{RUN_ID}}', c.check_name, 'reconciliation_report_{{REPORT_DATE}}', 'error',
       CASE WHEN c.failing_count = 0 THEN 'PASS' ELSE 'FAIL' END,
       c.failing_count, c.checked_rows, c.description, CAST('{{CHECKED_AT}}' AS TIMESTAMP)
FROM (
    SELECT 'report_control__compared_pairs' AS check_name,
           (SELECT ABS(w.compared_pairs - r.compared_pairs) FROM mart.v_recon_summary w, mart.recon_report_control r WHERE r.report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)) AS failing_count,
           (SELECT compared_pairs FROM mart.v_recon_summary) AS checked_rows,
           'abs(warehouse compared pairs - report compared pairs)' AS description
    UNION ALL SELECT 'report_control__flagged_pairs',
           (SELECT ABS(w.flagged_pairs - r.flagged_pairs) FROM mart.v_recon_summary w, mart.recon_report_control r WHERE r.report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)),
           (SELECT flagged_pairs FROM mart.v_recon_summary), 'abs(warehouse flagged pairs - report flagged pairs)'
    UNION ALL SELECT 'report_control__only_yfinance',
           (SELECT ABS(w.only_yfinance - r.only_yfinance) FROM mart.v_recon_summary w, mart.recon_report_control r WHERE r.report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)),
           (SELECT only_yfinance FROM mart.v_recon_summary), 'abs(warehouse yfinance-only rows - report coverage gap)'
    UNION ALL SELECT 'report_control__only_tiingo',
           (SELECT ABS(w.only_tiingo - r.only_tiingo) FROM mart.v_recon_summary w, mart.recon_report_control r WHERE r.report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)),
           (SELECT only_tiingo FROM mart.v_recon_summary), 'abs(warehouse tiingo-only rows - report coverage gap)'
    UNION ALL SELECT 'report_control__tickers_compared',
           (SELECT ABS(w.tickers_compared - r.tickers_compared) FROM mart.v_recon_summary w, mart.recon_report_control r WHERE r.report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)),
           (SELECT tickers_compared FROM mart.v_recon_summary), 'abs(warehouse tickers compared - report)'
    UNION ALL SELECT 'report_control__tickers_affected',
           (SELECT ABS(w.tickers_affected - r.tickers_affected) FROM mart.v_recon_summary w, mart.recon_report_control r WHERE r.report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)),
           (SELECT tickers_affected FROM mart.v_recon_summary), 'abs(warehouse affected tickers - report)'
    UNION ALL SELECT 'report_control__per_ticker_flagged',
           (SELECT COUNT(*) FROM (
                SELECT COALESCE(w.ticker, r.ticker) AS ticker
                FROM (SELECT ticker, flagged FROM mart.v_recon_by_ticker WHERE flagged > 0) w
                FULL OUTER JOIN (SELECT ticker, flagged FROM mart.recon_report_control_ticker WHERE report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)) r
                  ON r.ticker = w.ticker
                WHERE w.flagged IS NULL OR r.flagged IS NULL OR w.flagged <> r.flagged) m),
           (SELECT COUNT(*) FROM mart.recon_report_control_ticker WHERE report_ingest_date = CAST('{{REPORT_DATE}}' AS DATE)),
           'tickers whose flagged count differs from the report table (or appear in only one)'
) c;
