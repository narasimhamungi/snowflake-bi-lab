-- Data-quality checks. One row per check is appended to mart.dq_results.
-- Placeholders {{RUN_ID}} and {{CHECKED_AT}} are substituted by the runner.
--
-- Status: PASS when failing_count = 0; otherwise FAIL for severity 'error',
-- WARN for severity 'warn'. Warn = a real data property worth seeing but not
-- a pipeline fault (e.g. a vendor bad print the lakehouse already tolerates).
--
-- Relationship to the lakehouse Great Expectations suite: GE gates content
-- on bronze, before the data is modelled. These checks run after load and
-- cover what GE cannot see: that the warehouse copy is complete, keys are
-- unique and referentially intact (Snowflake enforces neither), and that
-- the reconciliation outcome in gold is reproducible from the two vendors'
-- rows. The OHLC rules deliberately repeat the GE ordering rules on the
-- gold copy so a regression introduced between bronze and gold would show.
INSERT INTO mart.dq_results
SELECT '{{RUN_ID}}', c.check_name, c.table_name, c.severity,
       CASE WHEN c.failing_count = 0 THEN 'PASS' WHEN c.severity = 'warn' THEN 'WARN' ELSE 'FAIL' END,
       c.failing_count, c.checked_rows, c.description, CAST('{{CHECKED_AT}}' AS TIMESTAMP)
FROM (
    -- ===== uniqueness (Snowflake does not enforce primary keys)
    SELECT 'pk_unique__dim_date' AS check_name, 'dim_date' AS table_name, 'error' AS severity,
           (SELECT COUNT(*) FROM (SELECT date_key FROM raw.dim_date GROUP BY date_key HAVING COUNT(*) > 1) x) AS failing_count,
           (SELECT COUNT(*) FROM raw.dim_date) AS checked_rows,
           'duplicate date_key values' AS description
    UNION ALL SELECT 'pk_unique__dim_security', 'dim_security', 'error',
           (SELECT COUNT(*) FROM (SELECT security_key FROM raw.dim_security GROUP BY security_key HAVING COUNT(*) > 1) x),
           (SELECT COUNT(*) FROM raw.dim_security), 'duplicate security_key values'
    UNION ALL SELECT 'one_current_row_per_ticker', 'dim_security', 'error',
           (SELECT COUNT(*) FROM (SELECT ticker FROM raw.dim_security WHERE is_current GROUP BY ticker HAVING COUNT(*) > 1) x),
           (SELECT COUNT(*) FROM raw.dim_security WHERE is_current), 'tickers with more than one is_current row (SCD-2 invariant)'
    UNION ALL SELECT 'pk_unique__fact_price_daily', 'fact_price_daily', 'error',
           (SELECT COUNT(*) FROM (SELECT security_key, date_key, source FROM raw.fact_price_daily GROUP BY security_key, date_key, source HAVING COUNT(*) > 1) x),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'duplicate (security_key, date_key, source)'
    UNION ALL SELECT 'pk_unique__fact_price_daily_consensus', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM (SELECT security_key, date_key FROM raw.fact_price_daily_consensus GROUP BY security_key, date_key HAVING COUNT(*) > 1) x),
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus), 'duplicate (security_key, date_key)'
    UNION ALL SELECT 'pk_unique__fact_corporate_action', 'fact_corporate_action', 'error',
           (SELECT COUNT(*) FROM (SELECT security_key, date_key, action_type, source FROM raw.fact_corporate_action GROUP BY security_key, date_key, action_type, source HAVING COUNT(*) > 1) x),
           (SELECT COUNT(*) FROM raw.fact_corporate_action), 'duplicate (security_key, date_key, action_type, source)'
    UNION ALL SELECT 'pk_unique__fact_macro_rate', 'fact_macro_rate', 'error',
           (SELECT COUNT(*) FROM (SELECT date_key, series_id FROM raw.fact_macro_rate GROUP BY date_key, series_id HAVING COUNT(*) > 1) x),
           (SELECT COUNT(*) FROM raw.fact_macro_rate), 'duplicate (date_key, series_id)'

    -- ===== referential integrity (facts -> dimensions)
    UNION ALL SELECT 'fk_security__fact_price_daily', 'fact_price_daily', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily f WHERE NOT EXISTS (SELECT 1 FROM raw.dim_security s WHERE s.security_key = f.security_key)),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'rows whose security_key is not in dim_security'
    UNION ALL SELECT 'fk_date__fact_price_daily', 'fact_price_daily', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily f WHERE NOT EXISTS (SELECT 1 FROM raw.dim_date d WHERE d.date_key = f.date_key)),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'rows whose date_key is not in dim_date'
    UNION ALL SELECT 'fk_security__fact_price_daily_consensus', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus f WHERE NOT EXISTS (SELECT 1 FROM raw.dim_security s WHERE s.security_key = f.security_key)),
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus), 'rows whose security_key is not in dim_security'
    UNION ALL SELECT 'fk_date__fact_price_daily_consensus', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus f WHERE NOT EXISTS (SELECT 1 FROM raw.dim_date d WHERE d.date_key = f.date_key)),
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus), 'rows whose date_key is not in dim_date'
    UNION ALL SELECT 'fk_security__fact_corporate_action', 'fact_corporate_action', 'error',
           (SELECT COUNT(*) FROM raw.fact_corporate_action f WHERE NOT EXISTS (SELECT 1 FROM raw.dim_security s WHERE s.security_key = f.security_key)),
           (SELECT COUNT(*) FROM raw.fact_corporate_action), 'rows whose security_key is not in dim_security'
    UNION ALL SELECT 'fk_date__fact_corporate_action', 'fact_corporate_action', 'error',
           (SELECT COUNT(*) FROM raw.fact_corporate_action f WHERE NOT EXISTS (SELECT 1 FROM raw.dim_date d WHERE d.date_key = f.date_key)),
           (SELECT COUNT(*) FROM raw.fact_corporate_action), 'rows whose date_key is not in dim_date'
    UNION ALL SELECT 'fk_date__fact_macro_rate', 'fact_macro_rate', 'error',
           (SELECT COUNT(*) FROM raw.fact_macro_rate f WHERE NOT EXISTS (SELECT 1 FROM raw.dim_date d WHERE d.date_key = f.date_key)),
           (SELECT COUNT(*) FROM raw.fact_macro_rate), 'rows whose date_key is not in dim_date'

    -- ===== nulls the downstream metrics cannot tolerate
    UNION ALL SELECT 'not_null__dim_security_ticker', 'dim_security', 'error',
           (SELECT COUNT(*) FROM raw.dim_security WHERE ticker IS NULL),
           (SELECT COUNT(*) FROM raw.dim_security), 'null ticker'
    UNION ALL SELECT 'not_null__fact_price_daily_adj_close', 'fact_price_daily', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE adj_close IS NULL),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'null adj_close (returns and reconciliation depend on it)'
    UNION ALL SELECT 'not_null__fact_price_daily_consensus_adj_close', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus WHERE adj_close IS NULL),
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus), 'null adj_close'

    -- ===== value rules (repeat the lakehouse GE ordering rules on the gold copy)
    UNION ALL SELECT 'price_positive__fact_price_daily', 'fact_price_daily', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE close <= 0 OR adj_close <= 0),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'close or adj_close <= 0'
    UNION ALL SELECT 'null_price_fields', 'fact_price_daily', 'warn',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR adj_open IS NULL OR adj_high IS NULL OR adj_low IS NULL OR adj_close IS NULL),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'rows with a NULL price field (the lakehouse stores a missing vendor value as NaN; the export maps NaN to NULL because Snowflake NUMBER cannot hold it)'
    UNION ALL SELECT 'ohlc_high_ge_low', 'fact_price_daily', 'warn',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE high < low),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'high < low'
    UNION ALL SELECT 'ohlc_low_le_open', 'fact_price_daily', 'warn',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE low > open),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'low > open (the rule the lakehouse bronze gate failed on for HUBB)'
    UNION ALL SELECT 'ohlc_low_le_close', 'fact_price_daily', 'warn',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE low > close),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'low > close'
    UNION ALL SELECT 'ohlc_high_ge_open', 'fact_price_daily', 'warn',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE high < open),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'high < open'
    UNION ALL SELECT 'ohlc_high_ge_close', 'fact_price_daily', 'warn',
           (SELECT COUNT(*) FROM raw.fact_price_daily WHERE high < close),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'high < close'

    -- ===== internal consistency of the consensus table
    UNION ALL SELECT 'consensus_flag_domain', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus WHERE reconciliation_flag IS NULL OR reconciliation_flag NOT IN ('agreed', 'single_source', 'disagreed')),
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus), 'reconciliation_flag outside agreed/single_source/disagreed'
    UNION ALL SELECT 'consensus_pct_diff_vs_flag', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus WHERE (reconciliation_flag = 'single_source' AND pct_diff IS NOT NULL) OR (reconciliation_flag IN ('agreed', 'disagreed') AND pct_diff IS NULL)),
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus), 'single_source rows must have NULL pct_diff; agreed/disagreed must not'
    UNION ALL SELECT 'consensus_flag_vs_tolerance', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus WHERE (reconciliation_flag = 'agreed' AND pct_diff > 0.005) OR (reconciliation_flag = 'disagreed' AND pct_diff < 0.005)),
           (SELECT COUNT(*) FROM raw.fact_price_daily_consensus WHERE pct_diff IS NOT NULL), 'flag contradicts the 0.5% tolerance applied to its own pct_diff (strict < on the disagreed side: the lakehouse flags on the unrounded value, gold stores 6dp, so 0.0050004 is stored as 0.005000 on a correctly disagreed row)'

    -- ===== reconciliation reproducible from the two vendors' rows
    UNION ALL SELECT 'recon_pair_count_match', 'fact_price_daily_consensus', 'error',
           (SELECT ABS((SELECT COUNT(*) FROM mart.v_recon_pairs) - (SELECT COUNT(*) FROM raw.fact_price_daily_consensus WHERE reconciliation_flag IN ('agreed', 'disagreed')))),
           (SELECT COUNT(*) FROM mart.v_recon_pairs), 'recomputed compared pairs differ from consensus rows flagged agreed/disagreed'
    UNION ALL SELECT 'recon_flag_mismatch', 'fact_price_daily_consensus', 'warn',
           (SELECT COUNT(*) FROM mart.v_recon_pairs p JOIN raw.fact_price_daily_consensus c ON c.security_key = p.security_key AND c.date_key = p.date_key WHERE p.is_flagged <> CASE WHEN c.reconciliation_flag = 'disagreed' THEN 1 ELSE 0 END),
           (SELECT COUNT(*) FROM mart.v_recon_pairs), 'pairs whose recomputed flag differs from stored flag (gold stores prices rounded to 6dp, so a pair within rounding of the 0.5% line can flip)'
    UNION ALL SELECT 'consensus_covers_price_keys', 'fact_price_daily_consensus', 'error',
           (SELECT COUNT(*) FROM (SELECT DISTINCT security_key, date_key FROM raw.fact_price_daily) k WHERE NOT EXISTS (SELECT 1 FROM raw.fact_price_daily_consensus c WHERE c.security_key = k.security_key AND c.date_key = k.date_key)),
           (SELECT COUNT(*) FROM (SELECT DISTINCT security_key, date_key FROM raw.fact_price_daily) k2), '(security, date) present in fact_price_daily but missing from consensus'

    -- ===== coverage (warn: properties of the data, not faults)
    UNION ALL SELECT 'security_without_prices', 'dim_security', 'warn',
           (SELECT COUNT(*) FROM raw.dim_security s WHERE s.is_current AND NOT EXISTS (SELECT 1 FROM raw.fact_price_daily f WHERE f.security_key = s.security_key)),
           (SELECT COUNT(*) FROM raw.dim_security WHERE is_current), 'current securities with no price rows in either source'
    UNION ALL SELECT 'trading_day_without_prices', 'dim_date', 'warn',
           (SELECT COUNT(*) FROM raw.dim_date d WHERE d.is_trading_day AND NOT EXISTS (SELECT 1 FROM raw.fact_price_daily f WHERE f.date_key = d.date_key)),
           (SELECT COUNT(*) FROM raw.dim_date WHERE is_trading_day), 'dates marked trading days with no price rows'
    UNION ALL SELECT 'price_on_non_trading_day', 'fact_price_daily', 'warn',
           (SELECT COUNT(*) FROM raw.fact_price_daily f JOIN raw.dim_date d ON d.date_key = f.date_key WHERE NOT d.is_trading_day),
           (SELECT COUNT(*) FROM raw.fact_price_daily), 'price rows on dates marked non-trading'
) c;
