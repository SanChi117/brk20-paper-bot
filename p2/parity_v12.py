#!/usr/bin/env python3
"""Сверка бумажного бота v1.2 (brk20v12.py из ветки feat/terminal-v2) с бэктестом stress.run на 54 монетах.
Бот считает индикаторы и сделки своим кодом (чистый Python) — сравниваем сигналы и R каждой сделки."""
import glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
import stress as S  # noqa: E402
sys.path.insert(0, sys.argv[3])
import brk20v12 as bot  # noqa: E402
ls = B.ls; H = B.H


def main():
    d1, d2 = sys.argv[1], sys.argv[2]
    ls.DATA = d1; reg = ls.btc_regime(ls.load('BTCUSDT'))
    rows_out = []; nb = nk = 0; diffs = []; only_b = []; only_k = []
    for dd in (d1, d2):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            f = ls.features(d); em = X.ema(d['c'], 20)
            bt = {}
            for e in B.events(d, f, reg, True):
                if e['sc'] >= 5 and pd.Timestamp(int(d['t'][e['i']]) + H, unit='ms').dayofweek < 5:
                    r = S.run(d, e, em, 1, True, True)
                    if r:
                        bt[int(d['t'][e['i']])] = r[0]
            rows = list(zip(d['t'].tolist(), d['o'].tolist(), d['h'].tolist(), d['l'].tolist(), d['c'].tolist(),
                            d['qv'].tolist(), d['tb'].tolist()))
            oi = {int(t): float(v) for t, v in zip(d['t'], d['oi']) if v == v}
            Sr = bot.Series(rows, oi)
            st0 = next(i for i in range(1500, len(Sr.t)) if Sr.t[i] >= B.START)
            ft = [x[0] for x in d['fund']]; fr = [x[1] for x in d['fund']]
            kb = {}
            bars = [r[:5] for r in rows]
            for i, sgn, lvl in bot.breakouts(Sr, start=st0):
                if i >= len(Sr.t) - 30:
                    continue
                sc, _ = Sr.score(i, sgn, lvl)
                if sc < 5 or not bot.weekday_ok(Sr.t[i]):
                    continue
                j0 = i + 1
                if j0 + bot.MAXH + 1 >= len(bars):
                    continue
                e1 = Sr.o[j0]; stop = lvl - sgn * 0.5 * Sr.atr[i]; risk = abs(e1 - stop)
                if risk <= 0 or sgn * (e1 - stop) <= 0:
                    continue
                r = bot.replay(bars, Sr.ema, j0, sgn, e1, stop, lvl, risk)
                t0, t1 = Sr.t[j0], Sr.t[r['j_exit']] + H
                fs = sum(v for t, v in zip(ft, fr) if t0 <= t < t1)
                kb[Sr.t[i]] = bot.pnl_of(sgn, r['units'], 1.0, r['px'], fs) / risk
            nb += len(bt); nk += len(kb)
            for t in set(bt) | set(kb):
                if t in bt and t in kb:
                    diffs.append(abs(bt[t] - kb[t]))
                elif t in bt:
                    only_b.append((s, t))
                else:
                    only_k.append((s, t))
            rows_out.append(f'| {s} | {len(bt)} | {len(kb)} | {sum(bt.values()):+.1f} | {sum(kb.values()):+.1f} |')
            print(s, len(bt), len(kb), flush=True)
    dif = np.array(diffs) if diffs else np.array([0.0])
    L = ['# Сверка бота v1.2 с бэктестом', '', f'Сделок: бэктест {nb}, бот {nk}; совпали по времени сигнала {len(diffs)}; '
         f'только в бэктесте {len(only_b)}, только у бота {len(only_k)}.',
         f'Расхождение R в совпавших: максимум {dif.max():.6f}, сделок с |ΔR| > 0.001: {(dif > 1e-3).sum()}.', '',
         'Примеры расхождений по сигналам: ' + ', '.join(f'{s}@{t}' for s, t in (only_b + only_k)[:10]), '',
         '| монета | сделок бэктест | бот | ΣR бэктест | ΣR бот |', '|---|---|---|---|---|'] + rows_out
    os.makedirs('p2_out', exist_ok=True); open('p2_out/PARITY.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L[:6]))


if __name__ == '__main__':
    main()
