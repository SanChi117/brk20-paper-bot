#!/usr/bin/env python3
"""15m свечи USD-M фьючерсов из data.binance.vision (SHA256 проверяется). 1h/4h/1d строятся из них."""
import argparse, csv, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'research'))
from fetch_vision import fetch_zip, months  # noqa: E402

M = 'https://data.binance.vision/data/futures/um/monthly/klines'
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT',
        'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'NEARUSDT', 'ATOMUSDT', 'FILUSDT', 'ETCUSDT', 'TRXUSDT', 'UNIUSDT',
        'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT', 'SUIUSDT']


def job(a):
    sym, mo = a
    txt, st = fetch_zip(f'{M}/{sym}/15m/{sym}-15m-{mo}.zip')
    rows = []
    if txt:
        for line in txt.splitlines():
            p = line.split(',')
            if not p[0].strip().isdigit():
                continue
            t = int(p[0]); t = t // 1000 if t > 10**14 else t
            rows.append((t, float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7])))
    return sym, mo, st, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='smc_data')
    ap.add_argument('--start', default='2021-10'); ap.add_argument('--end', default='2026-10')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    K = {s: [] for s in SYMS}; rec = []
    jobs = [(s, m) for s in SYMS for m in months(a.start, a.end)]
    with ThreadPoolExecutor(32) as ex:
        for sym, mo, st, rows in ex.map(job, jobs):
            K[sym] += rows; rec.append((sym, mo, st, len(rows)))
    for s in SYMS:
        with open(f'{a.out}/{s}_15m.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'o', 'h', 'l', 'c', 'qv']); w.writerows(sorted(set(K[s])))
        print(s, len(K[s]), flush=True)
    with open(f'{a.out}/receipt.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['symbol', 'month', 'status', 'rows']); w.writerows(rec)


if __name__ == '__main__':
    main()
