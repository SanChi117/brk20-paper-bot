#!/usr/bin/env python3
"""Скачивает официальные месячные архивы Binance USD-M Futures (data.binance.vision):
1h klines и funding rate. Проверяет SHA256 из .CHECKSUM. Пишет CSV в data/.

python3 research/fetch_vision.py --start 2021-01 --end 2026-10
"""
import argparse
import csv
import hashlib
import io
import os
import sys
import urllib.error
import urllib.request
import zipfile

BASE = 'https://data.binance.vision/data/futures/um/monthly'
SYMS = ['SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'AVAXUSDT', 'SUIUSDT', 'APTUSDT', 'NEARUSDT',
        'LINKUSDT', 'AAVEUSDT', 'ARBUSDT', 'OPUSDT', 'DOGEUSDT', 'TAOUSDT', 'ONDOUSDT', 'INJUSDT', 'SEIUSDT']


def months(start, end):
    y, m = map(int, start.split('-'))
    ey, em = map(int, end.split('-'))
    while (y, m) <= (ey, em):
        yield f'{y:04d}-{m:02d}'
        m += 1
        if m == 13:
            y, m = y + 1, 1


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch_zip(url):
    data = get(url)
    if data is None:
        return None, 'missing'
    chk = get(url + '.CHECKSUM')
    if chk:
        want = chk.decode().split()[0]
        if hashlib.sha256(data).hexdigest() != want:
            raise RuntimeError(f'SHA256 mismatch {url}')
    z = zipfile.ZipFile(io.BytesIO(data))
    return z.read(z.namelist()[0]).decode(), 'ok' if chk else 'ok-nochecksum'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2021-01')
    ap.add_argument('--end', default='2026-10')
    ap.add_argument('--out', default='data')
    ap.add_argument('--symbols', default=','.join(SYMS))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    receipt = []
    for sym in a.symbols.split(','):
        kl_rows, fr_rows = [], []
        for mo in months(a.start, a.end):
            txt, st = fetch_zip(f'{BASE}/klines/{sym}/1h/{sym}-1h-{mo}.zip')
            receipt.append((sym, 'klines', mo, st))
            if txt:
                for line in txt.splitlines():
                    p = line.split(',')
                    if not p[0].isdigit():
                        continue  # заголовок
                    kl_rows.append((int(p[0]), p[1], p[2], p[3], p[4], p[5]))
            txt, st = fetch_zip(f'{BASE}/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip')
            receipt.append((sym, 'funding', mo, st))
            if txt:
                for line in txt.splitlines():
                    p = line.split(',')
                    if not p[0].isdigit():
                        continue
                    fr_rows.append((int(p[0]), p[2]))
        kl_rows = sorted(set(kl_rows))
        with open(os.path.join(a.out, f'{sym}_1h.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['open_time', 'open', 'high', 'low', 'close', 'volume'])
            w.writerows(kl_rows)
        with open(os.path.join(a.out, f'{sym}_funding.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['calc_time', 'rate'])
            w.writerows(sorted(set(fr_rows)))
        print(f'{sym}: {len(kl_rows)} 1h bars, {len(fr_rows)} funding', flush=True)
    with open(os.path.join(a.out, 'receipt.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol', 'kind', 'month', 'status'])
        w.writerows(receipt)
    bad = [r for r in receipt if r[3] not in ('ok', 'missing')]
    print(f'files: {len(receipt)}, without checksum: {len(bad)}')


if __name__ == '__main__':
    sys.exit(main())
