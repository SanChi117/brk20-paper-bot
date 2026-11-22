#!/usr/bin/env python3
"""Загрузка данных для стратегии «Механизм рынка»: 1h свечи с taker buy, funding, OI (metrics 5m -> 1h).
Источник: data.binance.vision (официальные архивы Binance USD-M), SHA256 проверяется."""
import argparse, csv, datetime as dt, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'research'))
from fetch_vision import fetch_zip, months  # noqa: E402

M = 'https://data.binance.vision/data/futures/um/monthly'
D = 'https://data.binance.vision/data/futures/um/daily'
H = 3600000
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT',
        'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'NEARUSDT', 'ATOMUSDT', 'FILUSDT', 'ETCUSDT', 'TRXUSDT', 'UNIUSDT',
        'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT', 'SUIUSDT']


def ts(s):
    s = s.strip()
    if s.isdigit():
        t = int(s)
        return t // 1000 if t > 10**14 else t
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def kl(sym, mo):
    txt, st = fetch_zip(f'{M}/klines/{sym}/1h/{sym}-1h-{mo}.zip')
    rows = []
    if txt:
        for line in txt.splitlines():
            p = line.split(',')
            if not p[0].strip().isdigit():
                continue
            # open_time, o, h, l, c, volume, close_time, quote_vol, trades, taker_buy_base, taker_buy_quote
            rows.append((ts(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7]), float(p[10])))
    return sym, 'k', mo, st, rows


def fr(sym, mo):
    txt, st = fetch_zip(f'{M}/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip')
    rows = []
    if txt:
        for r in csv.reader(txt.splitlines()):
            if r and r[0].strip().isdigit():
                rows.append((ts(r[0]), float(r[2])))
    return sym, 'f', mo, st, rows


def mt(sym, day):
    try:
        txt, st = fetch_zip(f'{D}/metrics/{sym}/{sym}-metrics-{day}.zip')
    except Exception as e:
        return sym, 'm', day, f'error:{type(e).__name__}', []
    rows = []
    if txt:
        for r in csv.DictReader(txt.splitlines()):
            try:
                rows.append((ts(r['create_time']), float(r['sum_open_interest_value'])))
            except (KeyError, ValueError):
                pass
    return sym, 'm', day, st, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='mme_data')
    ap.add_argument('--start', default='2021-10'); ap.add_argument('--end', default='2026-10')
    ap.add_argument('--mstart', default='2022-01-01'); ap.add_argument('--mend', default='2026-10-31')
    ap.add_argument('--symbols', default=','.join(SYMS))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    syms = a.symbols.split(',')
    mos = list(months(a.start, a.end))
    d0, d1 = dt.date.fromisoformat(a.mstart), dt.date.fromisoformat(a.mend)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    jobs = [(kl, s, m) for s in syms for m in mos] + [(fr, s, m) for s in syms for m in mos] + [(mt, s, d) for s in syms for d in days]
    K = {s: [] for s in syms}; F = {s: [] for s in syms}; O = {s: {} for s in syms}; rec = []
    with ThreadPoolExecutor(32) as ex:
        for i, (sym, kind, per, st, rows) in enumerate(ex.map(lambda j: j[0](j[1], j[2]), jobs)):
            rec.append((sym, kind, per, st, len(rows)))
            if kind == 'k': K[sym] += rows
            elif kind == 'f': F[sym] += rows
            else:
                for t, v in rows:
                    O[sym][((t - 1) // H) * H] = v  # последний снимок часа = OI на закрытии часа
            if i % 5000 == 0:
                print(f'{i}/{len(jobs)}', flush=True)
    for s in syms:
        oi = O[s]
        with open(f'{a.out}/{s}_1h.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'o', 'h', 'l', 'c', 'qv', 'tbq', 'oi'])
            for r in sorted(set(K[s])):
                w.writerow(list(r) + [oi.get(r[0], '')])
        with open(f'{a.out}/{s}_funding.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'rate']); w.writerows(sorted(set(F[s])))
        print(s, len(K[s]), 'bars', len(oi), 'oi-hours', len(F[s]), 'funding', flush=True)
    with open(f'{a.out}/receipt.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['symbol', 'kind', 'period', 'status', 'rows']); w.writerows(rec)


if __name__ == '__main__':
    main()
