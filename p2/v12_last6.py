#!/usr/bin/env python3
# rerun 2026-11-28 14:16 UTC: данные за 27.11
"""Ход цены в последних бумажных сделках v1.2 (слот 3): максимум в плюс до выхода, в R. Публичные 1m свечи Binance."""
import io, zipfile, urllib.request, csv
from datetime import datetime, timezone
T = [  # sym, side, entry time UTC, entry, stop, exit time UTC
    ('ATOMUSDT', -1, '2026-11-25 12:00', 1.214, 1.23395, '2026-11-25 16:00'),
    ('TRXUSDT', -1, '2026-11-25 14:01', 0.30591, 0.307219, '2026-11-26 09:00'),
    ('TRXUSDT', -1, '2026-11-26 16:01', 0.30156, 0.302651, '2026-11-26 18:00'),
    ('SUIUSDT', 1, '2026-11-27 06:00', 0.6881, 0.683242, '2026-11-27 08:00'),
    ('THETAUSDT', 1, '2026-11-27 06:00', 0.1381, 0.136003, '2026-11-27 15:00'),
    ('TIAUSDT', -1, '2026-11-27 15:00', 0.332, 0.335436, '2026-11-27 16:01'),
]
def ms(s): return int(datetime.strptime(s, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).timestamp() * 1000)
cache = {}
def bars(sym, day):
    k = (sym, day)
    if k not in cache:
        u = f'https://data.binance.vision/data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{day}.zip'
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=60).read()))
        rows = [r for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0])))]
        cache[k] = [(int(r[0]), float(r[2]), float(r[3])) for r in rows if r[0].isdigit()]
    return cache[k]
for sym, s, t0, e, st, t1 in T:
    a, b = ms(t0), ms(t1); risk = abs(e - st); best = -9; tb = None
    for day in sorted({t0[:10], t1[:10]}):
        for t, h, l in bars(sym, day):
            if a <= t < b:
                m = ((h - e) if s == 1 else (e - l)) / risk
                if m > best: best, tb = m, t
    print(f'{sym} {"long" if s > 0 else "short"} {t0}: макс. ход в плюс {best:+.2f}R '
          f'({best*risk/e*100:.2f}% цены) в {datetime.fromtimestamp(tb/1000, timezone.utc):%H:%M}; стоп {risk/e*100:.2f}% цены')
