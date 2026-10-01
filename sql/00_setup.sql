-- Snowflake-only. Run once as a role allowed to create databases/warehouses
-- (on a trial account: ACCOUNTADMIN). Not executed by the DuckDB test-suite.
CREATE WAREHOUSE IF NOT EXISTS MARKETDATA_WH
    WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
CREATE DATABASE IF NOT EXISTS MARKETDATA;
USE DATABASE MARKETDATA;
CREATE SCHEMA IF NOT EXISTS RAW;      -- landed 1:1 from the Postgres gold layer
CREATE SCHEMA IF NOT EXISTS MODEL;    -- conformed views: facts joined to dims
CREATE SCHEMA IF NOT EXISTS MART;     -- metrics, reconciliation, audit; what Power BI reads
CREATE FILE FORMAT IF NOT EXISTS RAW.PARQUET_FF TYPE = PARQUET;
CREATE STAGE IF NOT EXISTS RAW.GOLD_STAGE FILE_FORMAT = RAW.PARQUET_FF;
