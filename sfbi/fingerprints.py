"""
Portable table fingerprints.

The same aggregate expressions are run in Postgres at export time and in
Snowflake after load, and compared exactly. Row counts alone miss the
failure modes that actually happen in a Postgres -> Parquet -> Snowflake
hop: NUMERIC precision loss, DATE/TIMESTAMP coercion, dropped partial
files. Sums of decimal columns and min/max keys catch those.

Expressions are deliberately limited to SQL that behaves identically in
Postgres, Snowflake and DuckDB (the engine the test-suite uses): no FILTER
clause, no COUNT_IF, no engine-specific casts.
"""
from __future__ import annotations

from decimal import Decimal

TABLES = [
    "dim_date",
    "dim_security",
    "fact_price_daily",
    "fact_price_daily_consensus",
    "fact_corporate_action",
    "fact_macro_rate",
]

FINGERPRINTS: dict[str, list[tuple[str, str]]] = {
    "dim_date": [
        ("min_date_key", "MIN(date_key)"),
        ("max_date_key", "MAX(date_key)"),
        ("trading_days", "SUM(CASE WHEN is_trading_day THEN 1 ELSE 0 END)"),
    ],
    "dim_security": [
        ("distinct_tickers", "COUNT(DISTINCT ticker)"),
        ("current_rows", "SUM(CASE WHEN is_current THEN 1 ELSE 0 END)"),
        ("max_security_key", "MAX(security_key)"),
    ],
    "fact_price_daily": [
        ("distinct_securities", "COUNT(DISTINCT security_key)"),
        ("min_date_key", "MIN(date_key)"),
        ("max_date_key", "MAX(date_key)"),
        ("sum_adj_close", "SUM(adj_close)"),
        ("sum_volume", "SUM(volume)"),
    ],
    "fact_price_daily_consensus": [
        ("sum_adj_close", "SUM(adj_close)"),
        ("sum_pct_diff", "SUM(pct_diff)"),
        ("disagreed_rows", "SUM(CASE WHEN reconciliation_flag = 'disagreed' THEN 1 ELSE 0 END)"),
        ("single_source_rows", "SUM(CASE WHEN reconciliation_flag = 'single_source' THEN 1 ELSE 0 END)"),
    ],
    "fact_corporate_action": [
        ("sum_value", "SUM(value)"),
        ("split_rows", "SUM(CASE WHEN action_type = 'split' THEN 1 ELSE 0 END)"),
    ],
    "fact_macro_rate": [
        ("distinct_series", "COUNT(DISTINCT series_id)"),
        ("sum_value", "SUM(value)"),
    ],
}


# Columns whose Postgres type is NUMERIC. The lakehouse can store NaN in these
# (its loader casts price fields with float() unguarded); Snowflake NUMBER
# cannot, so the export maps NaN to NULL. A per-column NULL count in every
# fingerprint proves the NULLs arrived intact.
DECIMAL_COLUMNS: dict[str, list[str]] = {
    "dim_date": [],
    "dim_security": [],
    "fact_price_daily": ["open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close"],
    "fact_price_daily_consensus": ["adj_close", "pct_diff"],
    "fact_corporate_action": ["value"],
    "fact_macro_rate": ["value"],
}
for _t, _cols in DECIMAL_COLUMNS.items():
    FINGERPRINTS[_t] += [(f"null_{c}", f"SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END)") for c in _cols]


def fingerprint_sql(table: str, schema_prefix: str = "", source: str | None = None) -> str:
    """One SELECT returning row_count plus every fingerprint for `table`.

    `source` overrides the FROM target (the exporter passes a derived table
    with NaN already mapped to NULL)."""
    cols = ["COUNT(*) AS row_count"] + [f"{expr} AS {name}" for name, expr in FINGERPRINTS[table]]
    return f"SELECT {', '.join(cols)} FROM {source or schema_prefix + table}"


def normalise(value) -> str | None:
    """Canonical string for exact cross-engine comparison."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if d == 0:
        return "0"
    return format(d.normalize(), "f")


def fingerprint_row_to_dict(columns: list[str], row: tuple) -> dict[str, str | None]:
    return {c.lower(): normalise(v) for c, v in zip(columns, row)}


def compare(expected: dict[str, str | None], observed: dict[str, str | None]) -> list[str]:
    """Return human-readable mismatches (empty list = exact match)."""
    problems = []
    for key in sorted(set(expected) | set(observed)):
        if expected.get(key) != observed.get(key):
            problems.append(f"{key}: expected {expected.get(key)!r}, got {observed.get(key)!r}")
    return problems
