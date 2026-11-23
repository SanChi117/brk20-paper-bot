#!/usr/bin/env python3
"""Сверка бумажного бота листингов (brk20l.py, ветка feat/terminal-v2) с исследованием hourly.py."""
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = [sys.argv[0], 'listings_out/listings.csv', 'listings_1h', sys.argv[1]]
import hourly as Hh  # noqa: E402
sys.path.insert(0, sys.argv[3])
import brk20l as bot  # noqa: E402

rows = list(csv.DictReader(open('listings_out/listings.csv')))
n = m = 0; worst = 0.0; miss = []
for r in rows:
    _, k = Hh.get(r)
    if len(k) < 48:
        continue
    en = Hh.entries(k, 'под открытие')
    ref = None
    if en and en[0] < len(k):
        i, st = en; sd = st / k[i][1] - 1
        if 0.01 < sd <= 0.4:
            ref = Hh.sim(k, i, st, 0.25)[0]
    sig = bot.find_signal(k)
    got = None
    if sig not in (None, 'wait') and sig[0] < len(k):
        i, st = sig; sd = st / k[i][1] - 1
        if 0.01 < sd <= 0.4:
            got = bot.simulate(k, i, st)
            got = got['R'] if got['done'] else 'open'
    if ref is None and got is None:
        continue
    n += 1
    if ref is not None and isinstance(got, float):
        m += 1; worst = max(worst, abs(ref - got))
    else:
        miss.append((r['sym'], ref, got))
print(f'PARITY listings: сделок {n}, совпали {m}, макс |dR| {worst:.6f}, расхождения {len(miss)}: {miss[:10]}')
