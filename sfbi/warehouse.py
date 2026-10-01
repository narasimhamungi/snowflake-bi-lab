"""Thin warehouse interface so the same pipeline code runs on Snowflake
(production) and DuckDB (the test-suite)."""
from __future__ import annotations

import os
from typing import Protocol


class Warehouse(Protocol):
    def execute(self, sql: str) -> None: ...
    def query(self, sql: str) -> tuple[list[str], list[tuple]]: ...


class SnowflakeWarehouse:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql: str) -> None:
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()

    def query(self, sql: str):
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return cols, cur.fetchall()
        finally:
            cur.close()


class DuckDBWarehouse:
    """Local engine for tests and the dry run (sfbi/dryrun_duckdb.py)."""

    def __init__(self, path: str = ":memory:"):
        import duckdb

        self.con = duckdb.connect(path)
        for schema in ("raw", "model", "mart"):
            self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    def execute(self, sql: str) -> None:
        self.con.execute(sql)

    def query(self, sql: str):
        cur = self.con.execute(sql)
        return [d[0] for d in cur.description], cur.fetchall()


def snowflake_connection():
    """Key-pair auth by default; password only if SNOWFLAKE_PASSWORD is set.

    Credentials come from the environment / a git-ignored .env, never from
    code. Snowflake has been tightening password-only sign-in, so key-pair
    is the supported path (see README)."""
    import snowflake.connector
    from dotenv import load_dotenv

    load_dotenv()
    cfg = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "MARKETDATA_WH"),
        role=os.environ.get("SNOWFLAKE_ROLE") or None,
    )
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        from cryptography.hazmat.primitives import serialization

        passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        with open(key_path, "rb") as f:
            key = serialization.load_pem_private_key(
                f.read(), password=passphrase.encode() if passphrase else None
            )
        cfg["private_key"] = key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    elif os.environ.get("SNOWFLAKE_PASSWORD"):
        cfg["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        raise RuntimeError("Set SNOWFLAKE_PRIVATE_KEY_PATH (preferred) or SNOWFLAKE_PASSWORD")
    return snowflake.connector.connect(**{k: v for k, v in cfg.items() if v is not None})
