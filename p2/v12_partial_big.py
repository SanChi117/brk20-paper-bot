#!/usr/bin/env python3
"""Идея пользователя: открыть позицию больше (×1/(1−доля)), зафиксировать долю, «ядро» = прежний размер.
Сравнение: тот же вариант против базы v1.2 с тем же увеличенным риском и против базы с прежним риском."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v12_improve as V  # noqa: E402


def main():
    V.DIRS = sys.argv[1:3]; V.ls.DATA = V.DIRS[0]; V.REG = V.ls.btc_regime(V.ls.load('BTCUSDT'))
    VAR = [('База v1.2, риск 0.3%', {}, 1.0), ('База v1.2, риск 0.4% (контроль)', {}, 4 / 3), ('База v1.2, риск 0.6% (контроль)', {}, 2.0),
           ('Позиция ×1.33, 25% на +0.5R', dict(pfrac=.25, pR=.5), 4 / 3), ('Позиция ×1.33, 25% на +1R', dict(pfrac=.25, pR=1.0), 4 / 3),
           ('Позиция ×1.33, 25% на +1.5R', dict(pfrac=.25, pR=1.5), 4 / 3), ('Позиция ×2, 50% на +1R', dict(pfrac=.5, pR=1.0), 2.0)]
    for cm in (1, 2):
        print(f'\n## Издержки ×{cm}\n| вариант | A 24 монеты | B 30 новых | C июнь–окт 2026 |\n|---|---|---|---|')
        cache = {}
        for name, kw, m in VAR:
            key = tuple(sorted(kw.items()))
            if key not in cache:
                cache[key] = V.collect(V.DIRS, cm, 1.0, **kw)
            tr, rets = cache[key]; cells = []
            for g, sel in (('A', lambda x: x['grp'] == 'A' and x['t'] < V.HOLD), ('B', lambda x: x['grp'] == 'B' and x['t'] < V.HOLD),
                           ('C', lambda x: x['t'] >= V.HOLD)):
                eq, dd, n, _, _ = V.equity([x for x in tr if sel(x)], lambda x, op, rs, R, m=m: m, rets)
                yrs = 4.33 if g != 'C' else 5 / 12
                cells.append(f'×{eq:.2f} / {dd*100:.0f}% / MAR {(eq ** (1 / yrs) - 1) / dd:.2f}')
            print(f'| {name} | ' + ' | '.join(cells) + ' |', flush=True)


if __name__ == '__main__':
    main()
