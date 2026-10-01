"""
Parse the lakehouse's committed reconciliation_report_<date>.txt into
control figures. The report is a plain-text artefact; parsing it is the
only way to compare the warehouse against the number the lakehouse
actually published. Anything unparseable raises: a silent miss here would
turn the control check into a rubber stamp.
"""
from __future__ import annotations

import re
from datetime import date

_HEADLINE = {
    "report_ingest_date": r"ingest_date=(\d{4}-\d{2}-\d{2})",
    "compared_pairs": r"Compared:\s+(\d+)\s+\(ticker, date\) pairs",
    "flagged_pairs": r"Flagged \(> [\d.]+% adj_close discrepancy\):\s+(\d+)\s+\(",
    "only_yfinance": r"Present only in yfinance \([^)]*\):\s+(\d+)",
    "only_tiingo": r"Present only in Tiingo \([^)]*\):\s+(\d+)",
}
_AFFECTED = r"Affected tickers:\s+(\d+) of (\d+) compared"
_TICKER_ROW = re.compile(
    r"^\s+(?P<ticker>[A-Z0-9.\-]+)\s+(?P<flagged>\d+)\s+(?P<compared>\d+)\s+"
    r"[\d.]+%\s+[\d.]+%\s+[\d.]+%\s+(?P<first>\d{4}-\d{2}-\d{2}) to (?P<last>\d{4}-\d{2}-\d{2})\s*$"
)


def parse_report(text: str) -> dict:
    out: dict = {}
    for key, pattern in _HEADLINE.items():
        m = re.search(pattern, text)
        if not m:
            raise ValueError(f"reconciliation report: could not find '{key}'")
        out[key] = date.fromisoformat(m.group(1)) if key == "report_ingest_date" else int(m.group(1))
    m = re.search(_AFFECTED, text)
    if not m:
        raise ValueError("reconciliation report: could not find 'Affected tickers' line")
    out["tickers_affected"], out["tickers_compared"] = int(m.group(1)), int(m.group(2))

    tickers = []
    for line in text.splitlines():
        r = _TICKER_ROW.match(line)
        if r:
            tickers.append({
                "ticker": r["ticker"], "flagged": int(r["flagged"]), "compared": int(r["compared"]),
                "first_flagged": date.fromisoformat(r["first"]), "last_flagged": date.fromisoformat(r["last"]),
            })
    if len(tickers) != out["tickers_affected"]:
        raise ValueError(
            f"reconciliation report: parsed {len(tickers)} ticker rows but the header says "
            f"{out['tickers_affected']} tickers affected"
        )
    out["tickers"] = tickers
    return out
