# snowflake-bi-lab

Cloud-warehouse and BI layer on top of [`marketdata-lakehouse`](https://github.com/narasimhamungi/marketdata-lakehouse):
the Postgres gold layer is exported, loaded into **Snowflake**, modelled in SQL,
quality-checked in SQL, and surfaced in a **Power BI** report.

> **Status — read this first.** Keep this block truthful; edit it when each item is done.
>
> | Item | State |
> |---|---|
> | Export script (`sfbi/export_gold.py`) | Built, tested against real Postgres; 1.4M-row scale test passed (~15 s, 51 MB Parquet) |
> | SQL (RAW DDL, MODEL views, MART models, 30+ DQ checks, report-control checks) | Built; logic tested end-to-end in **DuckDB**, same files production uses |
> | Dry run on the real export (`sfbi/dryrun_duckdb.py`: same SQL, DuckDB) | Built and tested; ~3 s at 1.1M rows. **Not yet run on the real export** |
> | Snowflake loader (`sfbi/load_snowflake.py`) | Written; control flow tested against a recording fake. **Not yet run against a live Snowflake account** |
> | Live row-count / fingerprint reconciliation Postgres ↔ Snowflake | **Not done** |
> | Power BI report, screenshots, PDF | **Not done** (spec in `docs/powerbi_spec.md`) |
> | CI | Workflow written; **not yet run on GitHub** |

## Problem

The lakehouse proves cross-vendor reconciliation and data-quality gating on a Postgres gold layer.
It has no cloud-warehouse or BI layer. This repo adds one, and treats the warehouse as something to be
*verified* (is the copy exact? are the keys intact? can the reconciliation be reproduced?) rather than just loaded.

## Architecture

```
Postgres gold (lakehouse)
   │  sfbi/export_gold.py  — one REPEATABLE READ snapshot → Parquet (decimal128, no floats) + manifest.json
   ▼  (row counts + portable aggregate fingerprints computed in Postgres)
Snowflake  MARKETDATA
   RAW    landed 1:1 (TRUNCATE + COPY INTO; idempotent full refresh)
   MODEL  views: facts joined to dims; has_yfinance / has_tiingo flags
   MART   price_returns (returns, 21-obs rolling vol), recon views, audit tables, latest-run views
   │  sfbi/pipeline.py — verify_load: recompute fingerprints in Snowflake, compare exactly, write mart.load_log
   │                     40_dq_checks.sql → mart.dq_results   50_dq_report_control.sql → vs lakehouse report
   ▼
Power BI (Import)  3 pages: market overview · reconciliation & data quality · platform health
```

## What is distinctive here

* **Exact-copy verification, not just row counts.** The same aggregate SQL (decimal sums, min/max keys, flag counts) runs in Postgres and in Snowflake and is compared exactly. A test shows a one-micro-unit price change that leaves row counts intact is caught.
* **Reconciliation is reproducible.** The warehouse recomputes the vendor comparison from the two per-source fact tables (`mart.v_recon_pairs`) and checks it against the stored consensus flags and against the lakehouse's own published report (`sql/50_dq_report_control.sql`).
* **Coverage gaps split properly.** The lakehouse report's single "only in yfinance" figure mixes tickers Tiingo was never asked for with dates missing inside tickers both vendors cover. `mart.v_recon_summary` separates them.
* **Checks that can fail.** Every DQ check has a test that breaks the data and asserts the check fires.

## Reproduce

Windows / PowerShell. Use a Python 3.12 venv if pip cannot find wheels for your newest interpreter.

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # fill in; never commit .env
$env:PYTHONPATH = "."

python -m pytest tests -q                                   # needs a *_test Postgres, see tests/conftest.py
python -m sfbi.export_gold --out export                     # Postgres -> export\*.parquet + manifest.json
python -m sfbi.dryrun_duckdb --export export               # full pipeline on the real export, locally; no Snowflake needed
python -m sfbi.load_snowflake --export export `
    --report-file ..\marketdata-lakehouse\outputs\reconciliation_report_<DATE>.txt
```

`<DATE>` **must be the ingest date the gold layer was built from** (`python -m src.orchestrate.build_gold <DATE>`).
If it is not, the report-control checks fail — by design.

First run: set `SNOWFLAKE_WAREHOUSE` to one that already exists (e.g. `COMPUTE_WH` on a trial) and `SNOWFLAKE_ROLE` to a role allowed to create databases/warehouses; `sql/00_setup.sql` creates `MARKETDATA_WH` and the database. Later runs can use `--skip-setup` with a least-privilege role.

Snowflake auth: create an RSA key pair, register the public key on your user, point `SNOWFLAKE_PRIVATE_KEY_PATH` at the private key (see `.env.example`).
Check the current trial terms and any MFA/auth requirements for your account type before starting.

## Verified vs unverified

Verified here (DuckDB + real Postgres): export correctness, fingerprint equality across engines, every view's logic against independently computed values (reconciliation counts, returns, rolling volatility, gap handling), every check's ability to fail, report parsing against the two real committed lakehouse reports.

**Unverified against Snowflake — expect to fix on first run:** acceptance of every SQL statement (dialect), `PUT`/`COPY INTO … MATCH_BY_COLUMN_NAME` on these Parquet files (including a zero-row table such as an empty `fact_corporate_action`), key-pair connection, and the `QUALIFY` / window / `MEDIAN` behaviour. Fix and record what changed in `docs/decision_log.md`.

## Results

*Fill after the first live run — numbers must come from the live run, not from this README.*
Commands that produce them: `SELECT * FROM mart.v_load_latest;` `SELECT status, COUNT(*) FROM mart.v_dq_latest GROUP BY status;` `SELECT * FROM mart.v_recon_summary;`

## Docs

`docs/data_dictionary.md` · `docs/decision_log.md` · `docs/limitations.md` · `docs/powerbi_spec.md`
