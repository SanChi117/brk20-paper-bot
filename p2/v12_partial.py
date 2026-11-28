#!/usr/bin/env python3
"""v1.2: частичная фиксация (идея пользователя 2026-11-28, предрегистрация в PREREG.md).
Закрыть долю первой части на +pR, остальное — по правилам v1.2 (стоп не трогать / в БУ). Плюс профиль: сколько
стопов перед этим были в плюсе."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v12_improve as V  # noqa: E402


def main():
    V.DIRS = sys.argv[1:3]; V.ls.DATA = V.DIRS[0]; V.REG = V.ls.btc_regime(V.ls.load('BTCUSDT'))
    one = lambda x, op, rs, R: 1.0
    VAR = [('База v1.2', {}), ('P1 25% на +0.5R', dict(pfrac=.25, pR=.5)), ('P2 25% на +1R', dict(pfrac=.25, pR=1.0)),
           ('P3 25% на +1.5R', dict(pfrac=.25, pR=1.5)), ('P4 50% на +1R', dict(pfrac=.5, pR=1.0)),
           ('P5 25% на +0.5R и стоп в БУ', dict(pfrac=.25, pR=.5, pbe=True))]
    L = ['# v1.2: частичная фиксация', '']
    for cm in (1, 2):
        L += [f'## Издержки ×{cm}', '', '| вариант | ср. R (A+B до 06.2026) | A | B | C |', '|---|---|---|---|---|']
        for name, kw in VAR:
            tr, rets = V.collect(V.DIRS, cm, 1.0, **kw); cells = []
            if cm == 1 and not kw:
                st = [x for x in tr if x['out'] == 'stop' and x['t'] < V.HOLD]
                prof = '; '.join(f'≥+{q}R: {np.mean([x["mfe"] >= q for x in st])*100:.0f}%' for q in (0.25, 0.5, 1.0))
                L.insert(2, f'Стопов до 06.2026: {len(st)}. Доля стопов, которые перед этим были в плюсе на {prof}.\n')
            Rm = np.mean([x['R'] for x in tr if x['t'] < V.HOLD])
            for g, sel in (('A', lambda x: x['grp'] == 'A' and x['t'] < V.HOLD), ('B', lambda x: x['grp'] == 'B' and x['t'] < V.HOLD),
                           ('C', lambda x: x['t'] >= V.HOLD)):
                sub = [x for x in tr if sel(x)]
                eq, dd, n, _, _ = V.equity(sub, one, rets)
                yrs = 4.33 if g != 'C' else 5 / 12
                mar = (eq ** (1 / yrs) - 1) / dd if dd > 0 else 0
                cells.append(f'×{eq:.2f} / {dd*100:.0f}% / MAR {mar:.2f}')
            L.append(f'| {name} | {Rm:+.3f} | ' + ' | '.join(cells) + ' |'); print(L[-1], flush=True)
        L.append('')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
