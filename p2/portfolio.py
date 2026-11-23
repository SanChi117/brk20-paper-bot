#!/usr/bin/env python3
"""Портфель: v1.2 (пробои, 54 монеты) + шорт новых листингов («под открытие», тейк −25%).
Сделки листингов — из listings/hourly.py (ветка auto/turtle), файл p2/listing_trades.csv.
Капитал общий, сложный процент; v1.2 — до 8 позиций и не больше одной на монету; листинги — без лимита.
Монте-Карло: месячные доходности 2022-01…2026-10, блоки по 3 месяца, старт $600."""
import csv, glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
import stress as S  # noqa: E402
ls = B.ls; H = B.H
T0 = int(np.datetime64('2022-01-01T00:00:00', 'ms').astype('int64'))


def v12(d1, d2, cm=1):
    ls.DATA = d1; reg = ls.btc_regime(ls.load('BTCUSDT')); tr = []
    for dd in (d1, d2):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            f = ls.features(d); em = X.ema(d['c'], 20)
            for e in B.events(d, f, reg, True):
                if e['sc'] >= 5 and pd.Timestamp(int(d['t'][e['i']]) + H, unit='ms').dayofweek < 5:
                    r = S.run(d, e, em, cm, True, True)
                    if r:
                        tr.append(dict(k='v', sym=s, t=r[1], tx=r[2], R=r[0]))
    return tr


def sim(tr, rv, rl):
    ev = sorted([x for x in tr if x['t'] >= T0], key=lambda x: x['t'])
    eq, pk, dd, op, mon = 1.0, 1.0, 0.0, [], {}
    def close_until(T):
        nonlocal eq, pk, dd
        op.sort(key=lambda y: y[0])
        while op and op[0][0] <= T:
            tx, st, R, k, s = op.pop(0); eq = max(eq + st * R, 0); pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
            mon[str(np.datetime64(int(tx), 'ms'))[:7]] = eq
    for x in ev:
        close_until(x['t'])
        if x['k'] == 'v':
            if rv == 0 or sum(1 for y in op if y[3] == 'v') >= 8 or any(y[4] == x['sym'] for y in op):
                continue
            op.append((x['tx'], eq * rv, x['R'], 'v', x['sym']))
        elif rl > 0:
            op.append((x['tx'], eq * rl, x['R'], 'l', x['sym']))
    close_until(10 ** 15)
    ks = pd.period_range('2022-01', '2026-10', freq='M').astype(str); v, last = [], 1.0
    for k in ks:
        last = mon.get(k, last); v.append(last)
    mr = np.diff(np.r_[1.0, v]) / np.r_[1.0, v][:-1]
    yr = {}
    for k, val in zip(ks, v):
        yr[k[:4]] = val
    prev, ys = 1.0, []
    for y in sorted(yr):
        ys.append(f"{y[2:]}:{(yr[y] / prev - 1) * 100:+.0f}%"); prev = yr[y]
    return eq, dd, mr, ' '.join(ys)


def mc(mr, months, n=20000, start=600.0, block=3, seed=5):
    g = np.random.default_rng(seed); M = len(mr); res = np.empty(n)
    for i in range(n):
        eq, m = start, 0
        while m < months:
            b0 = g.integers(0, M - block + 1)
            for k in range(block):
                if m >= months:
                    break
                eq *= 1 + mr[b0 + k]; m += 1
        res[i] = eq
    q = np.percentile(res, [10, 50, 90])
    return (f"${q[0]:,.0f} / ${q[1]:,.0f} / ${q[2]:,.0f} | {(res < 60).mean() * 100:.0f}% | "
            f"{(res >= 5000).mean() * 100:.1f}% | {(res >= 30000).mean() * 100:.1f}%")


def mc_add(mr, months, add, n=20000, start=600.0, block=3, seed=5):
    g = np.random.default_rng(seed); M = len(mr); res = np.empty(n)
    for i in range(n):
        eq, m = start, 0
        while m < months:
            b0 = g.integers(0, M - block + 1)
            for k in range(block):
                if m >= months:
                    break
                if m > 0:
                    eq += add
                eq *= 1 + mr[b0 + k]; m += 1
        res[i] = eq
    inv = start + add * (months - 1)
    q = np.percentile(res, [10, 25, 50, 75, 90])
    return (f"${inv:,.0f} | ${q[0]:,.0f} | ${q[1]:,.0f} | ${q[2]:,.0f} | ${q[3]:,.0f} | ${q[4]:,.0f} | "
            f"{(res < inv).mean() * 100:.0f}% | {(res >= 5000).mean() * 100:.0f}% | {(res >= 30000).mean() * 100:.1f}%")


def main():
    d1, d2 = sys.argv[1], sys.argv[2]
    lst = [dict(k='l', sym=r['sym'], t=int(r['t']), tx=int(r['tx']), R=float(r['R'])) for r in csv.DictReader(open('p2/listing_trades.csv'))]
    L = ['# Портфель v1.2 + шорт листингов', '', f'Листинги: {len([x for x in lst if x["t"] >= T0])} сделок с 2022 г.', '']
    for cm, title in ((1, 'Издержки как в тесте'), (2, 'Издержки v1.2 ×2 (плохое исполнение)')):
        tr = v12(d1, d2, cm) + lst
        L += [f'## {title}', '', '| риск v1.2 / листинги | 2022–окт 2026: × / просадка | по годам | $600 за 6 мес: 10% / медиана / 90% | слив | ≥$5k | ≥$30k | $600 за 12 мес: 10% / медиана / 90% | слив | ≥$5k | ≥$30k |',
              '|---|---|---|---|---|---|---|---|---|---|---|']
        for rv, rl in ((0.01, 0), (0, 0.1), (0.003, 0.02), (0.005, 0.05), (0.01, 0.05), (0.01, 0.1), (0.015, 0.1), (0.02, 0.1), (0.02, 0.15), (0.03, 0.15)):
            eq, dd, mr, ys = sim(tr, rv, rl)
            L.append(f'| {rv * 100:.1f}% / {rl * 100:.0f}% | ×{eq:,.1f} / {dd * 100:.0f}% | {ys} | ' + mc(mr, 6) + ' | ' + mc(mr, 12) + ' |')
        L.append('')
    L += ['## Пополнения каждый месяц (старт $600, взнос в начале месяцев 2…N)', '',
          '| издержки | риск v1.2 / листинги | взнос | срок | вложено | 10% | 25% | медиана | 75% | 90% | итог меньше вложенного | ≥$5k | ≥$30k |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for cm in (1, 2):
        tr = v12(d1, d2, cm) + lst
        for rv, rl in ((0.005, 0.05), (0.01, 0.1)):
            mr = sim(tr, rv, rl)[2]
            for add in (0, 100, 200):
                for mo in (6, 12):
                    L.append(f"| {'как в тесте' if cm == 1 else '×2'} | {rv * 100:.1f}% / {rl * 100:.0f}% | ${add} | {mo} мес | " + mc_add(mr, mo, add) + ' |')
    os.makedirs('p2_out', exist_ok=True); open('p2_out/PORTFOLIO.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
