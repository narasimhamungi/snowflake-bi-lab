"""Deterministic synthetic gold layer + independently computed ground truth.

Shapes deliberately exercised:
  AAA  both vendors; 3 dates missing from Tiingo (in-sample coverage gap);
       one bad print (low > open) in the yfinance row; historical SCD-2 row
  BBB  both vendors; constant 3% Tiingo offset for the first 20 days (disagreed stretch)
  CCC  both vendors; 9-day hole in BOTH vendors (gap return); 2 Tiingo-only days at the end
  DDD  yfinance only (a ticker Tiingo was never asked for)
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from psycopg2.extras import execute_values

TOL = 0.005
START, END = date(2024, 1, 1), date(2024, 3, 29)
HOLIDAY = date(2024, 1, 15)
CCC_HOLE = (date(2024, 2, 5), date(2024, 2, 13))


def q6(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def build(conn) -> dict:
    rnd = random.Random(42)
    days = [START + timedelta(d) for d in range((END - START).days + 1)]
    trading = [d for d in days if d.weekday() < 5 and d != HOLIDAY]
    key = lambda d: int(d.strftime("%Y%m%d"))

    with conn.cursor() as cur:
        execute_values(cur,
            "INSERT INTO dim_date (date_key, date, year, quarter, month, month_name, day, "
            "day_of_week, day_name, is_weekday, is_trading_day) VALUES %s",
            [(key(d), d, d.year, (d.month - 1) // 3 + 1, d.month, d.strftime("%B"), d.day,
              d.weekday(), d.strftime("%A"), d.weekday() < 5, d in trading) for d in days])
        secs = [("AAA", "Alpha", "Technology", True, None, date(2020, 1, 1)),
                ("BBB", "Bravo", "Technology", True, None, date(2020, 1, 1)),
                ("CCC", "Charlie", "Utilities", True, None, date(2020, 1, 1)),
                ("DDD", "Delta", "Energy", True, None, date(2020, 1, 1))]
        sk = {}
        for t, name, sector, cur_flag, eff_to, eff_from in secs:
            cur.execute(
                "INSERT INTO dim_security (ticker, company_name, gics_sector, gics_sub_industry, figi, "
                "effective_from, effective_to, is_current) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING security_key",
                (t, name, sector, "Sub", f"BBG{t}0000000", eff_from, eff_to, cur_flag))
            sk[t] = cur.fetchone()[0]
        # historical SCD-2 version of AAA (no facts reference it)
        cur.execute(
            "INSERT INTO dim_security (ticker, company_name, gics_sector, gics_sub_industry, figi, "
            "effective_from, effective_to, is_current) VALUES ('AAA','Alpha Old','Technology','Sub',"
            "'BBGAAA0000000','2019-01-01','2019-12-31',FALSE)")

    in_hole = lambda d: CCC_HOLE[0] <= d <= CCC_HOLE[1]
    yf: dict[tuple[str, date], dict] = {}
    tg: dict[tuple[str, date], dict] = {}
    base = {"AAA": 100.0, "BBB": 50.0, "CCC": 20.0, "DDD": 75.0}
    for t in base:
        px = base[t]
        for i, d in enumerate(trading):
            px *= 1 + rnd.uniform(-0.02, 0.02)
            if in_hole(d) and t == "CCC":
                continue
            o = px * (1 + rnd.uniform(-0.003, 0.003))
            row = dict(open=o, close=px, high=max(o, px) * 1.004, low=min(o, px) * 0.996, adj=px * 0.98,
                       volume=1_000_000 + i)
            yf[(t, d)] = row
    aaa_missing_tiingo = set(trading[10:13])
    for (t, d), r in list(yf.items()):
        if t == "DDD" or (t == "AAA" and d in aaa_missing_tiingo):
            continue
        adj = r["adj"] * (1.03 if t == "BBB" and d in trading[:20] else 1 + rnd.uniform(-0.001, 0.001))
        tg[(t, d)] = dict(r, adj=adj)
    # bad print injected AFTER the Tiingo copy: like HUBB it exists in one vendor only
    yf[("AAA", trading[30])]["low"] = yf[("AAA", trading[30])]["open"] * 1.01   # low > open
    bad_print = ("AAA", trading[30])
    for d in trading[-2:]:                       # CCC Tiingo-only days
        yf.pop(("CCC", d), None)
        tg[("CCC", d)] = dict(open=20.0, close=20.0, high=20.1, low=19.9, adj=19.6, volume=5)

    price_rows = []
    for src, store in (("yfinance", yf), ("tiingo", tg)):
        for (t, d), r in store.items():
            price_rows.append((sk[t], key(d), src, q6(r["open"]), q6(r["high"]), q6(r["low"]), q6(r["close"]),
                               q6(r["open"] * .98), q6(r["high"] * .98), q6(r["low"] * .98), q6(r["adj"]),
                               r["volume"], r["volume"]))
    with conn.cursor() as cur:
        execute_values(cur, "INSERT INTO fact_price_daily VALUES %s", price_rows)

    adj_y = {k: q6(v["adj"]) for k, v in yf.items()}
    adj_t = {k: q6(v["adj"]) for k, v in tg.items()}
    cons, pairs = [], {}
    for k in sorted(set(adj_y) | set(adj_t)):
        t, d = k
        if k in adj_y and k in adj_t:
            a, b = float(adj_y[k]), float(adj_t[k])
            pct = abs(a - b) / ((a + b) / 2)
            pairs[k] = pct
            flag = "disagreed" if pct > TOL else "agreed"
            cons.append((sk[t], key(d), adj_y[k], "yfinance", ["tiingo", "yfinance"], q6(pct), flag))
        elif k in adj_y:
            cons.append((sk[t], key(d), adj_y[k], "yfinance", ["yfinance"], None, "single_source"))
        else:
            cons.append((sk[t], key(d), adj_t[k], "tiingo", ["tiingo"], None, "single_source"))
    with conn.cursor() as cur:
        execute_values(cur, "INSERT INTO fact_price_daily_consensus VALUES %s", cons)
        cur.execute("INSERT INTO fact_corporate_action VALUES (%s,%s,'dividend',0.250000,'yfinance'),"
                    "(%s,%s,'split',2.000000,'yfinance')", (sk["AAA"], key(trading[20]), sk["BBB"], key(trading[40])))
        execute_values(cur, "INSERT INTO fact_macro_rate VALUES %s",
                       [(key(d), s, q6(v)) for d in trading[:5] for s, v in (("DGS10", 4.25), ("FEDFUNDS", 5.33))])
    conn.commit()

    # Real-world defect reproduced: the lakehouse loader writes a missing vendor value as
    # NaN (float() is unguarded on price fields) and Postgres NUMERIC stores it. DDD is
    # yfinance-only, so this does not disturb the reconciliation expectations below.
    d0, d1, d2 = (key(d) for d in trading[:3])
    with conn.cursor() as cur:
        cur.execute("UPDATE fact_price_daily SET adj_open = 'NaN', adj_high = 'NaN' "
                    "WHERE security_key = %s AND source = 'yfinance' AND date_key IN (%s, %s)",
                    (sk["DDD"], d0, d1))
        cur.execute("UPDATE fact_price_daily SET adj_low = 'NaN' "
                    "WHERE security_key = %s AND source = 'yfinance' AND date_key = %s", (sk["DDD"], d2))
    conn.commit()

    flagged = {k for k, p in pairs.items() if p > TOL}
    by_t = lambda ks: {t: sum(1 for (tt, _) in ks if tt == t) for t in base}
    only_y = [k for k in adj_y if k not in adj_t]
    only_t = [k for k in adj_t if k not in adj_y]
    cons_adj = {}
    for k in set(adj_y) | set(adj_t):
        cons_adj[k] = adj_y.get(k, adj_t.get(k))
    return dict(
        trading=trading, sk=sk, bad_print=bad_print,
        compared_pairs=len(pairs), flagged_pairs=len(flagged), flagged_by_ticker=by_t(flagged),
        tickers_compared=len({t for t, _ in pairs}), tickers_affected=len({t for t, _ in flagged}),
        only_yfinance=len(only_y), only_tiingo=len(only_t),
        only_yfinance_unsampled=sum(1 for (t, _) in only_y if t == "DDD"),
        n_price_rows=len(price_rows), n_consensus=len(cons), cons_adj=cons_adj,
        ccc_hole=CCC_HOLE,
        nan_cols={"adj_open": 2, "adj_high": 2, "adj_low": 1}, rows_with_null_price=3,
    )
