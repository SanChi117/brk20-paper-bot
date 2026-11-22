#!/usr/bin/env python3
"""Дневки + фандинг по всем USDT-M фьючерсам из архивов Binance (включая делистинг)."""
import csv, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'research'))
from fetch_vision import fetch_zip, months  # noqa: E402
M = 'https://data.binance.vision/data/futures/um/monthly'
LIST = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/'


def symbols():
    out, marker = [], ''
    while True:
        x = urllib.request.urlopen(LIST + (f'&marker={marker}' if marker else ''), timeout=60).read().decode()
        ps = re.findall(r'<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>', x)
        out += ps
        if '<IsTruncated>true</IsTruncated>' not in x or not ps:
            break
        marker = f'data/futures/um/monthly/klines/{ps[-1]}/'
    return sorted({s for s in out if s.endswith('USDT') and s.isascii() and s.isalnum()})


def kl(sym, mo):
    txt, st = fetch_zip(f'{M}/klines/{sym}/1d/{sym}-1d-{mo}.zip')
    rows = []
    for line in (txt or '').splitlines():
        p = line.split(',')
        if p[0].strip().isdigit():
            rows.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7])))
    return sym, 'k', rows


def fr(sym, mo):
    txt, st = fetch_zip(f'{M}/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip')
    rows = []
    for r in csv.reader((txt or '').splitlines()):
        if r and r[0].strip().isdigit():
            rows.append((int(r[0]), float(r[2])))
    return sym, 'f', rows


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'trend_data'
    os.makedirs(out, exist_ok=True)
    syms = symbols(); print('symbols', len(syms), flush=True)
    mos = list(months('2020-09', '2026-10'))
    K = {s: [] for s in syms}; F = {s: [] for s in syms}
    jobs = [(kl, s, m) for s in syms for m in mos] + [(fr, s, m) for s in syms for m in mos]
    with ThreadPoolExecutor(48) as ex:
        for i, (s, k, rows) in enumerate(ex.map(lambda j: j[0](j[1], j[2]), jobs)):
            (K if k == 'k' else F)[s].extend(rows)
            if i % 5000 == 0:
                print(i, '/', len(jobs), flush=True)
    n = 0
    for s in syms:
        if not K[s]:
            continue
        n += 1
        with open(f'{out}/{s}_1d.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'o', 'h', 'l', 'c', 'qv']); w.writerows(sorted(set(K[s])))
        with open(f'{out}/{s}_funding.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t', 'rate']); w.writerows(sorted(set(F[s])))
    print('saved', n, flush=True)


if __name__ == '__main__':
    main()
