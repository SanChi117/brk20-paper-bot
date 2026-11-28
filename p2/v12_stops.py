#!/usr/bin/env python3
"""v1.2: узкие стопы и издержки (предрегистрация 2026-11-28, см. PREREG.md).
Варианты: минимальная ширина стопа 0.8% / 1.2% (стоп отодвигается, размер по риску); пропуск стопа < 0.6%;
устойчивость C4: пропуск стопа > 3% / > 4%. Плюс разрез средн. R и издержек по ширине стопа."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v12_improve as V  # noqa: E402


def main():
    V.DIRS = sys.argv[1:3]; V.ls.DATA = V.DIRS[0]; V.REG = V.ls.btc_regime(V.ls.load('BTCUSDT'))
    one = lambda x, op, rs, R: 1.0
    VAR = [('База v1.2', 0.0, one), ('S1 мин. стоп 0.8%', 0.008, one), ('S2 мин. стоп 1.2%', 0.012, one),
           ('S3 пропуск стопа < 0.6%', 0.0, lambda x, op, rs, R: 0.0 if x['sp'] < 0.006 else 1.0),
           ('C4a пропуск стопа > 3%', 0.0, lambda x, op, rs, R: 0.0 if x['sp'] > 0.03 else 1.0),
           ('C4b пропуск стопа > 4%', 0.0, lambda x, op, rs, R: 0.0 if x['sp'] > 0.04 else 1.0)]
    L = ['# v1.2: ширина стопа', '']
    base = {}
    for cm in (1, 2):
        cache = {}
        L += [f'## Издержки ×{cm}', '', '| вариант | A | B | C |', '|---|---|---|---|']
        for name, ms, mult in VAR:
            if ms not in cache:
                cache[ms] = V.collect(V.DIRS, cm, 1.0, ms)
            tr, rets = cache[ms]; cells = []
            for g, sel in (('A', lambda x: x['grp'] == 'A' and x['t'] < V.HOLD), ('B', lambda x: x['grp'] == 'B' and x['t'] < V.HOLD),
                           ('C', lambda x: x['t'] >= V.HOLD)):
                sub = [x for x in tr if sel(x)]
                eq, dd, n, _, _ = V.equity(sub, mult, rets)
                yrs = 4.33 if g != 'C' else 5 / 12
                mar = (eq ** (1 / yrs) - 1) / dd if dd > 0 else 0
                cells.append(f'×{eq:.2f} / {dd*100:.0f}% / MAR {mar:.2f} ({n})')
            L.append(f'| {name} | ' + ' | '.join(cells) + ' |'); print(L[-1], flush=True)
        if cm == 1:
            tr = cache[0.0][0]
            L += ['', '| ширина стопа | сделок | средн. R | издержки на сделку, R (оценка) |', '|---|---|---|---|']
            for lo, hi in ((0, .006), (.006, .01), (.01, .015), (.015, .025), (.025, .035), (.035, 1)):
                R = np.array([x['R'] for x in tr if lo <= x['sp'] < hi and x['t'] < V.HOLD])
                if len(R):
                    L.append(f'| {lo*100:.1f}–{hi*100:.1f}% | {len(R)} | {R.mean():+.3f} | ≈{2*(V.FT0+V.SLP0)/((lo+min(hi,.05))/2):.2f} |')
        L.append('')
    os.makedirs('p2_out', exist_ok=True); open('p2_out/V12_STOPS.md', 'w').write('\n'.join(L)); print('\n'.join(L))


if __name__ == '__main__':
    main()
