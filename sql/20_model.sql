-- MODEL: facts joined to their dimensions. Views, not tables: no storage,
-- no refresh step, and the join logic is visible in one place.
CREATE OR REPLACE VIEW model.price_daily AS
SELECT f.security_key, s.ticker, s.gics_sector, f.date_key, d.date AS trade_date, f.source,
       f.open, f.high, f.low, f.close, f.adj_open, f.adj_high, f.adj_low, f.adj_close,
       f.volume, f.adj_volume
FROM raw.fact_price_daily f
JOIN raw.dim_security s ON s.security_key = f.security_key
JOIN raw.dim_date d ON d.date_key = f.date_key;

-- The "trusted price": one row per (security, date), reconciliation outcome
-- carried as a column. has_* flags replace the Postgres TEXT[] membership test.
CREATE OR REPLACE VIEW model.price_trusted AS
SELECT c.security_key, s.ticker, s.gics_sector, c.date_key, d.date AS trade_date,
       c.adj_close, c.primary_source, c.sources_available, c.pct_diff, c.reconciliation_flag,
       CASE WHEN c.sources_available LIKE '%yfinance%' THEN 1 ELSE 0 END AS has_yfinance,
       CASE WHEN c.sources_available LIKE '%tiingo%' THEN 1 ELSE 0 END AS has_tiingo
FROM raw.fact_price_daily_consensus c
JOIN raw.dim_security s ON s.security_key = c.security_key
JOIN raw.dim_date d ON d.date_key = c.date_key;

CREATE OR REPLACE VIEW model.corporate_action AS
SELECT a.security_key, s.ticker, a.date_key, d.date AS action_date, a.action_type, a.value, a.source
FROM raw.fact_corporate_action a
JOIN raw.dim_security s ON s.security_key = a.security_key
JOIN raw.dim_date d ON d.date_key = a.date_key;

CREATE OR REPLACE VIEW model.macro_rate AS
SELECT m.date_key, d.date AS rate_date, m.series_id, m.value
FROM raw.fact_macro_rate m
JOIN raw.dim_date d ON d.date_key = m.date_key;
