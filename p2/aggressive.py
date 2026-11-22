#!/usr/bin/env python3
"""Агрессивный разгон на v1.2: фаза 1 с риском r1 до $5k, затем 0.3%. Старт $650. Монте-Карло блоками по 3 месяца
из реальных месячных доходностей портфеля (54 монеты, до 8 позиций). Издержки: как в тесте и ×2."""
import glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
import robust_ema as RE  # noqa: E402
import stress as S  # noqa: E402
ls = B.ls; H = B.H


def trades(U, cm):
    tr = []
    for s, d, em, ev in U:
        for e in ev:
            r = S.run(d, e, em, cm, True, True)
            if r:
                tr.append(dict(R=r[0], t=r[1], tx=r[2], sym=s))
    return tr


def monthly(tr, risk):
    return RE.equity(tr, risk, 8)[3]


def paths(mr1, mr2, n=10000, horizon=36, start=650.0, p1=5000.0, goal=30000.0, ruin=65.0, block=3, seed=3):
    rng = np.random.default_rng(seed); M = len(mr1); res = []
    for _ in range(n):
        eq = start; phase = 1; t5 = t30 = None; ruined = False; m = 0
        while m < horizon:
            b0 = rng.integers(0, M - block + 1)
            for k in range(block):
                if m >= horizon:
                    break
                r = (mr1 if phase == 1 else mr2)[b0 + k]
                eq *= (1 + r); m += 1
                if eq <= ruin:
                    ruined = True; break
                if phase == 1 and eq >= p1:
                    phase = 2; t5 = m
                if t30 is None and eq >= goal:
                    t30 = m
            if ruined:
                break
        res.append((t5, t30, ruined, eq))
    return res


def summarize(res):
    t5 = [r[0] for r in res if r[0] is not None]; t30 = [r[1] for r in res if r[1] is not None]
    n = len(res)
    return (f"{len(t5)/n*100:.0f}% | {np.median(t5) if t5 else float('nan'):.0f} мес | {sum(1 for r in res if r[2] and r[0] is None)/n*100:.0f}% | "
            f"{len(t30)/n*100:.0f}% | {np.median(t30) if t30 else float('nan'):.0f} мес | ${np.median([r[3] for r in res]):,.0f}")


def main():
    d1, d2 = sys.argv[1], sys.argv[2]
    ls.DATA = d1; reg = ls.btc_regime(ls.load('BTCUSDT')); U = []
    for dd in (d1, d2):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            f = ls.features(d); em = X.ema(d['c'], 20)
            ev = [e for e in B.events(d, f, reg, True) if e['sc'] >= 5 and pd.Timestamp(int(d['t'][e['i']]) + H, unit='ms').dayofweek < 5]
            U.append((s, d, em, ev))
    L = ['# Агрессивный разгон на v1.2: $650 → $5k (фаза 1) → $30k', '',
         'Фаза 1: риск r1 на первую часть сделки до $5 000, затем 0.3%. Слив = счёт ниже $65 (−90%). Горизонт 36 мес., 10 000 путей,',
         'месячные доходности портфеля 2022–окт 2026 (до 8 позиций), перемешанные блоками по 3 месяца. Учтены фандинг и выход на следующем открытии.', '']
    for cm, title in ((1, 'Издержки как в тесте (taker 0.05% + 0.03%)'), (2, 'Издержки ×2 (плохое исполнение)')):
        tr = trades(U, cm); mr2 = monthly(tr, 0.003)
        L += [f'## {title}', '', '| риск в фазе 1 | дошли до $5k | медиана срока | слили до $5k | дошли до $30k за 3 года | медиана срока до $30k | медиана счёта через 3 года |',
              '|---|---|---|---|---|---|---|']
        for r1 in (0.003, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05):
            mr1 = monthly(tr, r1)
            n = min(len(mr1), len(mr2))
            L.append(f'| {r1*100:.1f}% | ' + summarize(paths(mr1[:n], mr2[:n])) + ' |')
        L.append('')
    os.makedirs('p2_out', exist_ok=True); open('p2_out/AGGRESSIVE.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
