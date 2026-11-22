#!/usr/bin/env python3
"""Доп. данные: метрики позиционирования (топ-трейдеры, все аккаунты, тейкеры) 5m→1h и индекс премии 1h.
data.binance.vision, SHA256."""
import csv, datetime as dt, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'research'))
from fetch_vision import fetch_zip, months  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mme'))
from fetch import SYMS  # noqa: E402

H = 3600000
DD = 'https://data.binance.vision/data/futures/um/daily/metrics'
MM = 'https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines'
COLS = ['count_toptrader_long_short_ratio', 'sum_toptrader_long_short_ratio', 'count_long_short_ratio', 'sum_taker_long_short_vol_ratio']


def ts(s):
    s = s.strip()
    if s.isdigit():
        t = int(s); return t // 1000 if t > 10**14 else t
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def met(a):
    sym, day = a
    try:
        txt, _ = fetch_zip(f'{DD}/{sym}/{sym}-metrics-{day}.zip')
    except Exception:
        return 'm', sym, []
    rows = []
    for r in csv.DictReader((txt or '').splitlines()):
        try:
            rows.append((ts(r['create_time']), *[float(r[k]) if r.get(k) not in (None, '') else float('nan') for k in COLS]))
        except (KeyError, ValueError):
            pass
    return 'm', sym, rows


def prem(a):
    sym, mo = a
    try:
        txt, _ = fetch_zip(f'{MM}/{sym}/1h/{sym}-1h-{mo}.zip')
    except Exception:
        return 'p', sym, []
    rows = []
    for line in (txt or '').splitlines():
        p = line.split(',')
        if p[0].strip().isdigit():
            rows.append((ts(p[0]), float(p[4])))
    return 'p', sym, rows


def main():
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    d0, d1 = dt.date(2021, 12, 1), dt.date(2026, 10, 31)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    jobs = [(met, (s, d)) for s in SYMS for d in days] + [(prem, (s, m)) for s in SYMS for m in months('2021-10', '2026-10')]
    M = {s: {} for s in SYMS}; P = {s: [] for s in SYMS}
    with ThreadPoolExecutor(48) as ex:
        for k, (kind, sym, rows) in enumerate(ex.map(lambda j: j[0](j[1]), jobs)):
            if kind == 'm':
                for r in rows:
                    M[sym][((r[0] - 1) // H) * H] = r[1:]   # последний снимок часа
            else:
                P[sym] += rows
            if k % 5000 == 0:
                print(k, '/', len(jobs), flush=True)
    for s in SYMS:
        with open(f'{out}/{s}_metrics.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t'] + COLS)
            for t in sorted(M[s]):
                w.writerow([t, *M[s][t]])
        with open(f'{out}/{s}_premium.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'prem']); w.writerows(sorted(set(P[s])))
        print(s, len(M[s]), len(P[s]), flush=True)


if __name__ == '__main__':
    main()
