"""SQL file helpers: placeholder substitution and statement splitting."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def render(sql: str, params: dict[str, str] | None = None) -> str:
    """Substitute {{NAME}} placeholders; an unfilled placeholder is an error."""
    params = params or {}

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in params:
            raise KeyError(f"unfilled SQL placeholder {{{{{name}}}}}")
        value = str(params[name])
        if "'" in value:
            raise ValueError(f"placeholder {name} contains a quote")
        return value

    return _PLACEHOLDER.sub(sub, sql)


def split_statements(sql: str) -> list[str]:
    """Split on top-level semicolons, respecting '..' strings, -- and /* */ comments."""
    statements, buf = [], []
    i, n = 0, len(sql)
    in_quote = in_line = in_block = False
    while i < n:
        ch, nxt = sql[i], sql[i + 1] if i + 1 < n else ""
        if in_line:
            buf.append(ch)
            in_line = ch != "\n"
        elif in_block:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 1
                in_block = False
        elif in_quote:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":          # escaped quote
                    buf.append(nxt)
                    i += 1
                else:
                    in_quote = False
        elif ch == "-" and nxt == "-":
            in_line = True
            buf.append(ch)
        elif ch == "/" and nxt == "*":
            in_block = True
            buf.append(ch)
        elif ch == "'":
            in_quote = True
            buf.append(ch)
        elif ch == ";":
            statements.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    statements.append("".join(buf))
    return [s.strip() for s in statements if _has_code(s)]


def _has_code(stmt: str) -> bool:
    no_block = re.sub(r"/\*.*?\*/", "", stmt, flags=re.S)
    no_line = re.sub(r"--[^\n]*", "", no_block)
    return bool(no_line.strip())


def load_script(name: str, params: dict[str, str] | None = None) -> list[str]:
    return split_statements(render((SQL_DIR / name).read_text(), params))


def literal(value) -> str:
    """Render a Python value as a SQL literal (used for control-table inserts)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"CAST('{value.isoformat(sep=' ')}' AS TIMESTAMP)"
    if isinstance(value, date):
        return f"CAST('{value.isoformat()}' AS DATE)"
    return "'" + str(value).replace("'", "''") + "'"
