# Limitations

* **Scale.** S&P 500 daily prices over ~5+ years: single-digit millions of rows at most. Nothing here demonstrates warehouse performance tuning, clustering, or cost optimisation at scale.
* **Single snapshot, no scheduling.** One export is loaded wholesale. There is no incremental load, no orchestration of this repo, and "freshness" means "when the snapshot was loaded", reported honestly via `mart.load_log`.
* **Two free vendors, one sample.** Tiingo's free tier limits the reconciled universe to a sample of tickers; the reconciliation says nothing about tickers outside it.
* **Adjusted prices are vendor-adjusted.** Returns on `adj_close` inherit each vendor's corporate-action adjustment. The large flagged windows in the lakehouse report (e.g. T, IFF) are adjustment-timing differences between vendors, not data bugs; returns inside a window are internally consistent but a vendor restatement can distort returns near its boundary.
* **Rounding.** Gold stores prices at 6 decimal places while the original reconciliation used unrounded floats, so a pair sitting almost exactly on the 0.5% line can flip. `recon_flag_mismatch` is therefore `warn`, not `error`; expect zero or very few.
* **DuckDB ≠ Snowflake.** See README "Verified vs unverified".
* **No production or team experience is demonstrated.** This is a personal build on public data.
* **Trial warehouse expires.** Evidence is code, tests, screenshots and a PDF export.
