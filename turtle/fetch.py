#!/usr/bin/env python3
"""Дневки + фандинг (data.binance.vision, SHA256) для небольшого набора старых ликвидных фьючерсов."""
import csv, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'research'))
from fetch_vision import fetch_zip, months  # noqa: E402
M = 'https://data.binance.vision/data/futures/um/monthly'
SYMS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'LINKUSDT', 'LTCUSDT', 'BCHUSDT']


def job(a):
    kind, s, mo = a
    if kind == 'k':
        txt, _ = fetch_zip(f'{M}/klines/{s}/1d/{s}-1d-{mo}.zip')
        return kind, s, [(int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]))
                         for p in (l.split(',') for l in (txt or '').splitlines()) if p[0].strip().isdigit()]
    txt, _ = fetch_zip(f'{M}/fundingRate/{s}/{s}-fundingRate-{mo}.zip')
    return kind, s, [(int(p[0]), float(p[2])) for p in (l.split(',') for l in (txt or '').splitlines()) if p[0].strip().isdigit()]


def main():
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    jobs = [(k, s, m) for k in 'kf' for s in SYMS for m in months('2019-09', '2026-10')]
    K = {s: [] for s in SYMS}; F = {s: [] for s in SYMS}
    with ThreadPoolExecutor(32) as ex:
        for k, s, rows in ex.map(job, jobs):
            (K if k == 'k' else F)[s] += rows
    for s in SYMS:
        with open(f'{out}/{s}_1d.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'o', 'h', 'l', 'c']); w.writerows(sorted(set(K[s])))
        with open(f'{out}/{s}_funding.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'rate']); w.writerows(sorted(set(F[s])))
        print(s, len(K[s]), len(F[s]), flush=True)


if __name__ == '__main__':
    main()
