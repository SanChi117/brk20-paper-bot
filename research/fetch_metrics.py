#!/usr/bin/env python3
"""Скачивает дневные архивы Binance USD-M "metrics" (5-мин: open interest, taker buy/sell ratio,
top-trader long/short) с data.binance.vision, проверяет SHA256 и агрегирует в 1h:
  oi        — open interest (контракты) на конце часа
  oi_value  — open interest в USDT на конце часа
  taker     — среднее sum_taker_long_short_vol_ratio за час
Пишет data_metrics/{SYM}_metrics_1h.csv

python3 research/fetch_metrics.py --start 2022-01-01 --end 2026-10-31
"""
import argparse
import csv
import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_vision import fetch_zip  # noqa: E402

BASE = 'https://data.binance.vision/data/futures/um/daily/metrics'
SYMS = ['SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'AVAXUSDT', 'NEARUSDT', 'LINKUSDT', 'DOGEUSDT',
        'AAVEUSDT', 'INJUSDT', 'OPUSDT', 'ARBUSDT']
H = 3600000


def days(a, b):
    d = dt.date.fromisoformat(a)
    e = dt.date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def parse_ts(s):
    s = s.strip()
    if s.isdigit():
        t = int(s)
        return t // 1000 if t > 10**14 else t
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def one(sym, day):
    url = f'{BASE}/{sym}/{sym}-metrics-{day}.zip'
    try:
        txt, st = fetch_zip(url)
    except Exception as e:  # сеть/битый архив — фиксируем в квитанции
        return sym, day, f'error:{type(e).__name__}', []
    if txt is None:
        return sym, day, st, []
    rows = []
    for r in csv.DictReader(txt.splitlines()):
        try:
            rows.append((parse_ts(r['create_time']), float(r['sum_open_interest']),
                         float(r['sum_open_interest_value']), float(r['sum_taker_long_short_vol_ratio'] or 'nan')))
        except (KeyError, ValueError):
            continue
    return sym, day, st, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2022-01-01')
    ap.add_argument('--end', default='2026-10-31')
    ap.add_argument('--out', default='data_metrics')
    ap.add_argument('--symbols', default=','.join(SYMS))
    ap.add_argument('--workers', type=int, default=24)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    syms = a.symbols.split(',')
    jobs = [(s, d) for s in syms for d in days(a.start, a.end)]
    per = {s: [] for s in syms}
    receipt = []
    with ThreadPoolExecutor(a.workers) as ex:
        for i, (sym, day, st, rows) in enumerate(ex.map(lambda j: one(*j), jobs)):
            per[sym].extend(rows)
            receipt.append((sym, day, st, len(rows)))
            if i % 2000 == 0:
                print(f'{i}/{len(jobs)}', flush=True)
    for sym, rows in per.items():
        rows.sort()
        hours = {}
        for t, oi, oiv, tk in rows:
            # 5-мин отметка t относится к часу, который закрывается в ceil(t/H)*H; берём последнее значение часа
            k = ((t - 1) // H) * H  # час, открывшийся в k (отметка ровно на закрытии часа попадает в него)
            hrec = hours.setdefault(k, [None, None, []])
            hrec[0], hrec[1] = oi, oiv
            if tk == tk:
                hrec[2].append(tk)
        with open(os.path.join(a.out, f'{sym}_metrics_1h.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['open_time', 'oi', 'oi_value', 'taker'])
            for k in sorted(hours):
                oi, oiv, tks = hours[k]
                w.writerow([k, oi, oiv, (sum(tks) / len(tks)) if tks else ''])
        print(f'{sym}: {len(hours)} hours', flush=True)
    with open(os.path.join(a.out, 'receipt.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol', 'day', 'status', 'rows'])
        w.writerows(receipt)


if __name__ == '__main__':
    main()
