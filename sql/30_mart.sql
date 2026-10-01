-- MART: metrics and reconciliation. Portable SQL. Conventions:
--   * returns are on the consensus adj_close (vendor-adjusted); not a total-return index
--   * a return spanning more than 5 calendar days (days_since_prev > 5) is a
--     "gap return": kept, but excluded from volatility and sector averages,
--     because a missing stretch of data would otherwise look like one huge move
--   * rolling vol = sample stdev of the last 21 observations x sqrt(252); NULL
--     until a full 21-observation window of non-gap returns exists

CREATE OR REPLACE TABLE mart.price_returns AS
WITH base AS (
    SELECT security_key, ticker, gics_sector, date_key, trade_date, adj_close, reconciliation_flag,
           LAG(adj_close)  OVER (PARTITION BY security_key ORDER BY trade_date) AS prev_adj_close,
           LAG(trade_date) OVER (PARTITION BY security_key ORDER BY trade_date) AS prev_trade_date
    FROM model.price_trusted
),
ret AS (
    SELECT security_key, ticker, gics_sector, date_key, trade_date, adj_close, reconciliation_flag,
           DATEDIFF('day', prev_trade_date, trade_date) AS days_since_prev,
           CASE WHEN prev_adj_close IS NULL THEN NULL
                ELSE CAST(adj_close AS DOUBLE) / CAST(prev_adj_close AS DOUBLE) - 1 END AS daily_return
    FROM base
),
vol_in AS (
    SELECT *, CASE WHEN days_since_prev <= 5 THEN daily_return END AS return_for_vol
    FROM ret
)
SELECT security_key, ticker, gics_sector, date_key, trade_date, adj_close, reconciliation_flag,
       days_since_prev, daily_return,
       CASE WHEN daily_return IS NULL THEN NULL ELSE LN(1 + daily_return) END AS log_return,
       CASE WHEN days_since_prev > 5 THEN 1 ELSE 0 END AS is_gap_return,
       CASE WHEN COUNT(return_for_vol) OVER (PARTITION BY security_key ORDER BY trade_date
                 ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) = 21
            THEN STDDEV_SAMP(return_for_vol) OVER (PARTITION BY security_key ORDER BY trade_date
                 ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) * SQRT(252)
            ELSE NULL END AS vol_21d_ann
FROM vol_in;

CREATE OR REPLACE VIEW mart.v_latest_price AS
SELECT security_key, ticker, gics_sector, trade_date, adj_close, daily_return, vol_21d_ann,
       reconciliation_flag
FROM mart.price_returns
QUALIFY ROW_NUMBER() OVER (PARTITION BY security_key ORDER BY trade_date DESC) = 1;

CREATE OR REPLACE VIEW mart.v_security_summary AS
WITH agg AS (
    SELECT security_key, ticker, gics_sector, COUNT(*) AS observations,
           MIN(trade_date) AS first_date, MAX(trade_date) AS last_date,
           AVG(vol_21d_ann) AS avg_vol_21d_ann,
           SUM(CASE WHEN reconciliation_flag = 'disagreed' THEN 1 ELSE 0 END) AS disagreed_observations
    FROM mart.price_returns
    GROUP BY security_key, ticker, gics_sector
)
SELECT a.security_key, a.ticker, a.gics_sector, a.observations, a.first_date, a.last_date,
       a.avg_vol_21d_ann, a.disagreed_observations,
       f.adj_close AS first_adj_close, l.adj_close AS last_adj_close,
       CAST(l.adj_close AS DOUBLE) / CAST(f.adj_close AS DOUBLE) - 1 AS adj_close_return
FROM agg a
JOIN mart.price_returns f ON f.security_key = a.security_key AND f.trade_date = a.first_date
JOIN mart.price_returns l ON l.security_key = a.security_key AND l.trade_date = a.last_date;

-- Equal-weighted: simple mean across whichever securities have a non-gap
-- return that day. The member set varies with data coverage; read as a
-- sector barometer, not an index.
CREATE OR REPLACE VIEW mart.v_sector_daily_return AS
SELECT gics_sector, trade_date, COUNT(*) AS securities, AVG(daily_return) AS ew_return
FROM mart.price_returns
WHERE daily_return IS NOT NULL AND is_gap_return = 0
GROUP BY gics_sector, trade_date;

CREATE OR REPLACE VIEW mart.dim_security_current AS
SELECT security_key, ticker, company_name, gics_sector, gics_sub_industry, figi
FROM raw.dim_security WHERE is_current;

-- ---------------------------------------------------------------- reconciliation
-- Independent recomputation of the lakehouse reconciliation from the two
-- per-source fact rows. 0.005 mirrors RECONCILIATION_TOLERANCE in the
-- lakehouse config; pct_diff is relative to the mean of both sources, as
-- in src/reconcile/price_reconciliation.py.
CREATE OR REPLACE VIEW mart.v_recon_pairs AS
SELECT y.security_key, y.date_key,
       CAST(y.adj_close AS DOUBLE) AS yfinance_adj_close,
       CAST(t.adj_close AS DOUBLE) AS tiingo_adj_close,
       ABS(CAST(y.adj_close AS DOUBLE) - CAST(t.adj_close AS DOUBLE))
         / ((CAST(y.adj_close AS DOUBLE) + CAST(t.adj_close AS DOUBLE)) / 2) AS pct_diff,
       CASE WHEN ABS(CAST(y.adj_close AS DOUBLE) - CAST(t.adj_close AS DOUBLE))
              / ((CAST(y.adj_close AS DOUBLE) + CAST(t.adj_close AS DOUBLE)) / 2) > 0.005
            THEN 1 ELSE 0 END AS is_flagged
FROM raw.fact_price_daily y
JOIN raw.fact_price_daily t ON t.security_key = y.security_key AND t.date_key = y.date_key
WHERE y.source = 'yfinance' AND t.source = 'tiingo'
  AND y.adj_close IS NOT NULL AND t.adj_close IS NOT NULL;

CREATE OR REPLACE VIEW mart.v_recon_by_ticker AS
SELECT s.ticker, s.gics_sector, p.security_key,
       COUNT(*) AS compared, SUM(p.is_flagged) AS flagged,
       CAST(SUM(p.is_flagged) AS DOUBLE) / COUNT(*) AS flagged_pct,
       MAX(p.pct_diff) AS max_pct_diff, MEDIAN(p.pct_diff) AS median_pct_diff,
       MIN(CASE WHEN p.is_flagged = 1 THEN d.date END) AS first_flagged_date,
       MAX(CASE WHEN p.is_flagged = 1 THEN d.date END) AS last_flagged_date
FROM mart.v_recon_pairs p
JOIN raw.dim_security s ON s.security_key = p.security_key
JOIN raw.dim_date d ON d.date_key = p.date_key
GROUP BY s.ticker, s.gics_sector, p.security_key;

-- Coverage, kept apart from discrepancies. Splits "gap" rows into the two
-- different things the lakehouse report lumps together: tickers Tiingo was
-- never asked for (sampling design) vs dates missing inside a ticker both
-- vendors cover (a real coverage gap).
CREATE OR REPLACE VIEW mart.v_recon_coverage AS
SELECT s.ticker, s.gics_sector, c.security_key,
       SUM(CASE WHEN c.has_yfinance = 1 THEN 1 ELSE 0 END) AS yfinance_rows,
       SUM(CASE WHEN c.has_tiingo = 1 THEN 1 ELSE 0 END) AS tiingo_rows,
       SUM(CASE WHEN c.has_yfinance = 1 AND c.has_tiingo = 1 THEN 1 ELSE 0 END) AS both_rows,
       SUM(CASE WHEN c.has_yfinance = 1 AND c.has_tiingo = 0 THEN 1 ELSE 0 END) AS yfinance_only_rows,
       SUM(CASE WHEN c.has_yfinance = 0 AND c.has_tiingo = 1 THEN 1 ELSE 0 END) AS tiingo_only_rows,
       CASE WHEN SUM(c.has_tiingo) > 0 THEN 1 ELSE 0 END AS in_tiingo_sample
FROM model.price_trusted c
JOIN raw.dim_security s ON s.security_key = c.security_key
GROUP BY s.ticker, s.gics_sector, c.security_key;

CREATE OR REPLACE VIEW mart.v_recon_summary AS
WITH p AS (
    SELECT COUNT(*) AS compared_pairs, COALESCE(SUM(is_flagged), 0) AS flagged_pairs,
           COUNT(DISTINCT security_key) AS tickers_compared FROM mart.v_recon_pairs
), a AS (
    SELECT COUNT(DISTINCT security_key) AS tickers_affected FROM mart.v_recon_pairs WHERE is_flagged = 1
), g AS (
    SELECT COALESCE(SUM(yfinance_only_rows), 0) AS only_yfinance,
           COALESCE(SUM(tiingo_only_rows), 0) AS only_tiingo,
           COALESCE(SUM(CASE WHEN in_tiingo_sample = 0 THEN yfinance_only_rows ELSE 0 END), 0)
               AS only_yfinance_unsampled_tickers,
           COALESCE(SUM(CASE WHEN in_tiingo_sample = 1 THEN yfinance_only_rows ELSE 0 END), 0)
               AS only_yfinance_within_sampled_tickers
    FROM mart.v_recon_coverage
)
SELECT p.compared_pairs, p.flagged_pairs,
       CASE WHEN p.compared_pairs = 0 THEN NULL
            ELSE CAST(p.flagged_pairs AS DOUBLE) / p.compared_pairs END AS flagged_pct,
       p.tickers_compared, a.tickers_affected,
       g.only_yfinance, g.only_tiingo, g.only_yfinance_unsampled_tickers,
       g.only_yfinance_within_sampled_tickers
FROM p CROSS JOIN a CROSS JOIN g;

-- OHLC rows that break basic ordering rules: the in-warehouse counterpart of
-- the bad print the lakehouse's bronze gate tolerates (HUBB, 2021-05-05).
CREATE OR REPLACE VIEW mart.v_price_anomalies AS
SELECT ticker, trade_date, source, open, high, low, close,
       CASE WHEN low > open THEN 'low>open' WHEN high < low THEN 'high<low'
            WHEN high < open THEN 'high<open' WHEN high < close THEN 'high<close'
            ELSE 'low>close' END AS violated_rule
FROM model.price_daily
WHERE low > open OR high < low OR high < open OR high < close OR low > close;

-- Row counts and latest dates, for the platform-health page.
CREATE OR REPLACE VIEW mart.v_platform_health AS
SELECT 'dim_date' AS table_name, COUNT(*) AS row_count, CAST(MAX(date) AS VARCHAR) AS latest_value FROM raw.dim_date
UNION ALL SELECT 'dim_security', COUNT(*), CAST(MAX(effective_from) AS VARCHAR) FROM raw.dim_security
UNION ALL SELECT 'fact_price_daily', COUNT(*), CAST(MAX(date_key) AS VARCHAR) FROM raw.fact_price_daily
UNION ALL SELECT 'fact_price_daily_consensus', COUNT(*), CAST(MAX(date_key) AS VARCHAR) FROM raw.fact_price_daily_consensus
UNION ALL SELECT 'fact_corporate_action', COUNT(*), CAST(MAX(date_key) AS VARCHAR) FROM raw.fact_corporate_action
UNION ALL SELECT 'fact_macro_rate', COUNT(*), CAST(MAX(date_key) AS VARCHAR) FROM raw.fact_macro_rate;

CREATE OR REPLACE VIEW mart.dim_date AS
SELECT date_key, date AS calendar_date, year, quarter, month, month_name, day_name, is_weekday, is_trading_day
FROM raw.dim_date;

-- "Latest run" views: the audit tables are append-only (one block of rows per
-- load). Power BI reads these so no DAX is needed to pick the current run.
CREATE OR REPLACE VIEW mart.v_load_latest AS
SELECT * FROM mart.load_log
WHERE run_id = (SELECT run_id FROM mart.load_log ORDER BY loaded_at_utc DESC LIMIT 1);

CREATE OR REPLACE VIEW mart.v_dq_latest AS
SELECT * FROM mart.dq_results
WHERE run_id = (SELECT run_id FROM mart.load_log ORDER BY loaded_at_utc DESC LIMIT 1);
