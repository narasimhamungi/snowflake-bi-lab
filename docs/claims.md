# Claims ledger

Generated 2026-11-04, checked against the repo's actual README (commit b3c6713).

| Claim in README | Tag | Evidence |
|---|---|---|
| Export script tested against real Postgres, 1.4M-row scale test (~15s, 51MB Parquet) | Demonstrated | `sfbi/export_gold.py`, Status table |
| SQL (RAW/MODEL/MART, 30+ DQ checks) tested end-to-end in DuckDB, same files production uses | Demonstrated | dry-run script, Status table |
| Snowflake load run live 2026-10-01 (trial, key-pair auth, run `2ebde46ce4d7`) | Demonstrated | Status table |
| Postgres ↔ Snowflake row-count/fingerprint reconciliation exact, 6/6 tables | Demonstrated | Status table, Results section |
| "Surfaced in a Power BI report" (opening paragraph, line 7, before fix) | **Overclaim — fixed.** The Status table two lines down says "Power BI report, screenshots, PDF: Not done (spec only)," and the architecture diagram presented all 3 Power BI pages as if built. The opening sentence and diagram line read as a completed capability while the repo's own status table says otherwise. | README commit b3c6713, lines 7 and 40 |
| Power BI (3 pages: market overview, reconciliation & DQ, platform health) | Unsupported — spec only, not built | `docs/powerbi_spec.md` |

## Fix applied
- Opening paragraph (line 7): "...and surfaced in a Power BI report" → "...and quality-checked in SQL. A Power BI report is specced (`docs/powerbi_spec.md`) but not built — see Status below."
- Architecture diagram (line 40): "Power BI (Import)" → "Power BI (Import, PLANNED — not built)"
- Diff saved to `snowflake-bi-lab-readme.patch` in this handoff; apply with `git apply snowflake-bi-lab-readme.patch` from the repo root, or edit the two lines by hand.
