-- RAW: 1:1 with the lakehouse Postgres gold layer (src/model/schema.sql).
-- Portable SQL (Snowflake + DuckDB). No PRIMARY/FOREIGN KEY constraints on
-- purpose: Snowflake does not enforce them, so declaring them would
-- document an integrity guarantee the engine does not provide. The checks
-- in 40_dq_checks.sql enforce uniqueness and referential integrity instead.
-- The only intentional difference from Postgres: sources_available is a
-- pipe-delimited VARCHAR, not TEXT[] (see docs/decision_log.md).
CREATE TABLE IF NOT EXISTS raw.dim_date (
    date_key INTEGER, date DATE, year SMALLINT, quarter SMALLINT, month SMALLINT,
    month_name VARCHAR, day SMALLINT, day_of_week SMALLINT, day_name VARCHAR,
    is_weekday BOOLEAN, is_trading_day BOOLEAN
);
CREATE TABLE IF NOT EXISTS raw.dim_security (
    security_key INTEGER, ticker VARCHAR, company_name VARCHAR, gics_sector VARCHAR,
    gics_sub_industry VARCHAR, figi VARCHAR, effective_from DATE, effective_to DATE,
    is_current BOOLEAN
);
CREATE TABLE IF NOT EXISTS raw.fact_price_daily (
    security_key INTEGER, date_key INTEGER, source VARCHAR,
    open NUMERIC(18,6), high NUMERIC(18,6), low NUMERIC(18,6), close NUMERIC(18,6),
    adj_open NUMERIC(18,6), adj_high NUMERIC(18,6), adj_low NUMERIC(18,6), adj_close NUMERIC(18,6),
    volume BIGINT, adj_volume BIGINT
);
CREATE TABLE IF NOT EXISTS raw.fact_price_daily_consensus (
    security_key INTEGER, date_key INTEGER, adj_close NUMERIC(18,6), primary_source VARCHAR,
    sources_available VARCHAR, pct_diff NUMERIC(9,6), reconciliation_flag VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.fact_corporate_action (
    security_key INTEGER, date_key INTEGER, action_type VARCHAR, value NUMERIC(18,6), source VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.fact_macro_rate (
    date_key INTEGER, series_id VARCHAR, value NUMERIC(18,6)
);
