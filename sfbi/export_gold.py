"""
Export the marketdata-lakehouse gold layer (Postgres) to Parquet + manifest.

    python -m sfbi.export_gold [--out export]

Reads with the same MDL_DB_* variables the lakehouse uses. One consistent
snapshot (REPEATABLE READ, read-only) so row counts and fingerprints
describe exactly the rows written.

Deliberate choices, all documented in docs/decision_log.md:
  * NUMERIC(18,6) is written as decimal128(18,6), never float, so prices
    arrive in Snowflake exactly as stored.
  * NaN in a NUMERIC column (the lakehouse stores a missing vendor value that
    way; constrained NUMERIC(p,s) cannot hold Infinity, so NaN is the only
    non-finite case) becomes NULL, because Snowflake NUMBER cannot hold it.
    The count per column is recorded in the manifest and printed; nothing is
    converted silently.
  * sources_available (a Postgres TEXT[]) is flattened to a pipe-delimited
    string. Loading Parquet LIST into a Snowflake ARRAY is behaviour this
    project has not verified, and nothing downstream needs the array type.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from sfbi.fingerprints import FINGERPRINTS, TABLES, fingerprint_row_to_dict, fingerprint_sql

I32, I16, I64 = pa.int32(), pa.int16(), pa.int64()
D186, D96 = pa.decimal128(18, 6), pa.decimal128(9, 6)

SPECS: dict[str, dict] = {
    "dim_date": {
        "order": "date_key",
        "schema": pa.schema([
            ("date_key", I32), ("date", pa.date32()), ("year", I16), ("quarter", I16),
            ("month", I16), ("month_name", pa.string()), ("day", I16), ("day_of_week", I16),
            ("day_name", pa.string()), ("is_weekday", pa.bool_()), ("is_trading_day", pa.bool_()),
        ]),
    },
    "dim_security": {
        "order": "security_key",
        "schema": pa.schema([
            ("security_key", I32), ("ticker", pa.string()), ("company_name", pa.string()),
            ("gics_sector", pa.string()), ("gics_sub_industry", pa.string()), ("figi", pa.string()),
            ("effective_from", pa.date32()), ("effective_to", pa.date32()), ("is_current", pa.bool_()),
        ]),
    },
    "fact_price_daily": {
        "order": "security_key, date_key, source",
        "schema": pa.schema([
            ("security_key", I32), ("date_key", I32), ("source", pa.string()),
            ("open", D186), ("high", D186), ("low", D186), ("close", D186),
            ("adj_open", D186), ("adj_high", D186), ("adj_low", D186), ("adj_close", D186),
            ("volume", I64), ("adj_volume", I64),
        ]),
    },
    "fact_price_daily_consensus": {
        "order": "security_key, date_key",
        "schema": pa.schema([
            ("security_key", I32), ("date_key", I32), ("adj_close", D186),
            ("primary_source", pa.string()), ("sources_available", pa.string()),
            ("pct_diff", D96), ("reconciliation_flag", pa.string()),
        ]),
    },
    "fact_corporate_action": {
        "order": "security_key, date_key, action_type, source",
        "schema": pa.schema([
            ("security_key", I32), ("date_key", I32), ("action_type", pa.string()),
            ("value", D186), ("source", pa.string()),
        ]),
    },
    "fact_macro_rate": {
        "order": "date_key, series_id",
        "schema": pa.schema([("date_key", I32), ("series_id", pa.string()), ("value", D186)]),
    },
}

# Columns whose Postgres expression differs from the plain column.
EXPR_OVERRIDES = {"fact_price_daily_consensus": {"sources_available": "array_to_string(sources_available, '|')"}}
NAN = "'NaN'::numeric"


def decimal_columns(table: str) -> list[str]:
    return [f.name for f in SPECS[table]["schema"] if pa.types.is_decimal(f.type)]


def clean_select(table: str, order: bool = True) -> str:
    """SELECT for `table` with NaN in NUMERIC columns mapped to NULL."""
    parts = []
    for f in SPECS[table]["schema"]:
        override = EXPR_OVERRIDES.get(table, {}).get(f.name)
        if override:
            expr = override
        elif pa.types.is_decimal(f.type):
            expr = f"NULLIF({f.name}, {NAN})"
        else:
            expr = f.name
        parts.append(f"{expr} AS {f.name}")
    sql = f"SELECT {', '.join(parts)} FROM {table}"
    return sql + (f" ORDER BY {SPECS[table]['order']}" if order else "")


def nan_counts(conn, table: str) -> dict[str, int]:
    cols = decimal_columns(table)
    if not cols:
        return {}
    sel = ", ".join(f"SUM(CASE WHEN {c} = {NAN} THEN 1 ELSE 0 END)" for c in cols)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {sel} FROM {table}")
        row = cur.fetchone()
    return {c: int(n) for c, n in zip(cols, row) if n}


from sfbi.fingerprints import DECIMAL_COLUMNS  # noqa: E402
assert set(SPECS) == set(TABLES) == set(FINGERPRINTS)
assert all(decimal_columns(t) == DECIMAL_COLUMNS[t] for t in TABLES), "SPECS decimals != fingerprints.DECIMAL_COLUMNS"


def get_connection():
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    return psycopg2.connect(
        host=os.environ.get("MDL_DB_HOST", "localhost"),
        port=int(os.environ.get("MDL_DB_PORT", "5432")),
        dbname=os.environ.get("MDL_DB_NAME", "marketdata_lakehouse"),
        user=os.environ.get("MDL_DB_USER", "mdl"),
        password=os.environ.get("MDL_DB_PASSWORD", "mdl_local_dev"),
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def export_table(conn, table: str, out_dir: Path, batch_rows: int = 100_000) -> dict:
    spec = SPECS[table]
    schema: pa.Schema = spec["schema"]
    path = out_dir / f"{table}.parquet"

    with conn.cursor() as cur:
        cur.execute(fingerprint_sql(table, source=f"({clean_select(table, order=False)}) AS t"))
        cols = [d[0] for d in cur.description]
        fp = fingerprint_row_to_dict(cols, cur.fetchone())
    expected_rows = int(fp.pop("row_count"))
    nan_to_null = nan_counts(conn, table)

    written = 0
    with pq.ParquetWriter(path, schema, compression="snappy") as writer:
        with conn.cursor(name=f"export_{table}") as cur:
            cur.itersize = batch_rows
            cur.execute(clean_select(table))
            while True:
                rows = cur.fetchmany(batch_rows)
                if not rows:
                    break
                arrays = [pa.array([r[i] for r in rows], type=schema.field(i).type)
                          for i in range(len(schema))]
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                written += len(rows)

    if written != expected_rows:
        raise RuntimeError(f"{table}: wrote {written} rows but snapshot count was {expected_rows}")
    if pq.ParquetFile(path).metadata.num_rows != written:
        raise RuntimeError(f"{table}: parquet footer row count disagrees with rows written")

    return {
        "rows": written,
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "nan_to_null": nan_to_null,
        "fingerprints": fp,
    }


def export_all(conn, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
    manifest = {
        "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tables": {},
    }
    with conn.cursor() as cur:
        cur.execute("SHOW server_version")
        manifest["postgres_version"] = cur.fetchone()[0]
    for table in TABLES:
        manifest["tables"][table] = export_table(conn, table, out_dir)
    conn.rollback()
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="export", help="output directory (git-ignored)")
    args = ap.parse_args(argv)
    conn = get_connection()
    try:
        manifest = export_all(conn, Path(args.out))
    finally:
        conn.close()
    for t, m in manifest["tables"].items():
        print(f"{t:<30} {m['rows']:>10,} rows  {m['bytes']:>12,} bytes")
        if m["nan_to_null"]:
            print(f"    NaN mapped to NULL: {m['nan_to_null']}")
    print(f"manifest: {Path(args.out) / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
