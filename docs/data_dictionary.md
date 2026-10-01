# Warehouse data dictionary

Database `MARKETDATA`. Source: lakehouse Postgres gold layer (`src/model/schema.sql`).

## RAW — landed 1:1
| Table | Grain | Notes |
|---|---|---|
| `dim_date` | one row per calendar date | `is_trading_day` is derived empirically by the lakehouse (any security traded), not from a holiday calendar |
| `dim_security` | one row per ticker **version** (SCD-2) | `effective_from/to`, `is_current`; at most one current row per ticker (checked, not enforced) |
| `fact_price_daily` | (security_key, date_key, source) | `source` ∈ yfinance, tiingo. Raw OHLC is **not** comparable across vendors (split adjustment differs); only `adj_close` is |
| `fact_price_daily_consensus` | (security_key, date_key) | `primary_source`; `sources_available` pipe-delimited (e.g. `tiingo|yfinance`); `pct_diff` = abs diff / mean of both, NULL for single-source; `reconciliation_flag` ∈ agreed, single_source, disagreed (tolerance 0.5%) |
| `fact_corporate_action` | (security_key, date_key, action_type, source) | dividend amount or split ratio |
| `fact_macro_rate` | (date_key, series_id) | FRED series (DGS10, DGS3MO, FEDFUNDS, CPIAUCSL) |

## MODEL — views (`sql/20_model.sql`)
`price_daily` (source-grain prices + ticker, sector, trade_date) · `price_trusted` (consensus + ticker, sector, trade_date, `has_yfinance`, `has_tiingo`) · `corporate_action` · `macro_rate`.

## MART — `sql/30_mart.sql`, `sql/25_audit_ddl.sql`
| Object | Type | Meaning |
|---|---|---|
| `price_returns` | table | per security-date: `daily_return` (adj_close, DOUBLE), `log_return`, `days_since_prev`, `is_gap_return` (>5 calendar days), `vol_21d_ann` (sample stdev of 21 non-gap returns × √252; NULL until a full window) |
| `v_latest_price` | view | newest row per security |
| `v_security_summary` | view | observations, first/last date, avg vol, `adj_close_return` (last/first − 1, vendor-adjusted), `disagreed_observations` |
| `v_sector_daily_return` | view | equal-weighted mean non-gap return per sector-date |
| `dim_security_current`, `dim_date` | views | slicer dimensions for Power BI |
| `v_recon_pairs` | view | (security, date) present in both vendors: `pct_diff`, `is_flagged` (> 0.005) — recomputed from `raw.fact_price_daily` |
| `v_recon_by_ticker` | view | compared, flagged, flagged_pct, max/median diff, first/last flagged date |
| `v_recon_coverage` | view | per ticker rows by vendor; `in_tiingo_sample` |
| `v_recon_summary` | view | one row: compared/flagged pairs, tickers compared/affected, yfinance-only / tiingo-only rows, and yfinance-only split into unsampled tickers vs gaps inside sampled tickers |
| `v_price_anomalies` | view | OHLC rows violating ordering rules, with the rule |
| `v_platform_health` | view | row count and latest key per RAW table |
| `load_log` | table | per run and table: rows expected/loaded, `fingerprint_match`, mismatch detail |
| `dq_results` | table | per run and check: severity, status (PASS/WARN/FAIL), failing_count, checked_rows |
| `v_load_latest`, `v_dq_latest` | views | the most recent run only |
| `recon_report_control`, `recon_report_control_ticker` | tables | figures parsed from the lakehouse's committed reconciliation report |

## DQ checks (`sql/40_dq_checks.sql`, `sql/50_dq_report_control.sql`)
Uniqueness (7) · referential integrity (7) · not-null (3) · price/OHLC rules (7, incl. `null_price_fields`) · consensus consistency (3) · reconciliation reproducibility (3) · coverage (3) · report control (7, optional).
