#!/usr/bin/env python3
"""RSI(14) × EMA(9) от RSI — идея из рекламы (Instagram), предрегистрация 2026-11-28 в PREREG.md.
Часть 1 — отдельная стратегия на 1h и 4h (4h собран из 1h). Часть 2 — фильтр к v1.2."""
import glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v12_improve as V  # noqa: E402
ls = V.ls; HOLD = V.HOLD
COST = 0.0008            # на сторону: taker + проскальзывание, как в v1.2


def bars4h(d):
    df = pd.DataFrame({'o': d['o'], 'h': d['h'], 'l': d['l'], 'c': d['c']}, index=pd.to_datetime(d['t'], unit='ms'))
    g = df.resample('4h').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    return g.index.asi8 // 10 ** 6, g['o'].values, g['c'].values


def trades(t, o, c, mode):
    """Сигнал на закрытии бара, вход на открытии следующего. mode: flip — всегда в рынке (лонг/шорт по пересечению);
    long — только лонги (вход при пересечении вверх, выход при пересечении вниз); zone — как flip, но вход только если
    пересечение вверх ниже 50 (лонг) / вниз выше 50 (шорт), выход на обратном пересечении."""
    r = V.rsi(c); e = pd.Series(r).ewm(span=9, adjust=False).mean().values
    up = (r > e) & (np.roll(r, 1) <= np.roll(e, 1)); dn = (r < e) & (np.roll(r, 1) >= np.roll(e, 1))
    up[:60] = dn[:60] = False
    out = []; pos = 0; ent = 0.0; t0 = 0
    for i in range(60, len(c) - 1):
        sig = 1 if up[i] else (-1 if dn[i] else 0)
        if not sig:
            continue
        px = o[i + 1]
        if pos and sig == -pos:
            out.append((t0, int(t[i + 1]), pos * (px / ent - 1) - 2 * COST)); pos = 0
        if not pos:
            if mode == 'long' and sig != 1:
                continue
            if mode == 'zone' and not ((sig == 1 and r[i] < 50) or (sig == -1 and r[i] > 50)):
                continue
            pos, ent, t0 = sig, px, int(t[i + 1])
    return out


def main():
    dirs = sys.argv[1:3]
    L = ['# RSI(14) × EMA9(RSI)', '', '## Часть 1: отдельная стратегия (издержки 0.08% на сторону, без плеча)', '',
         '| ТФ | режим | выборка | сделок | сделок в день на монету | средн. сделка | PF | доля прибыльных | средн. без издержек |',
         '|---|---|---|---|---|---|---|---|---|']
    for tf in ('1h', '4h'):
        for mode in ('flip', 'long', 'zone'):
            res = {'A': [], 'B': [], 'C': []}; days = {'A': 0, 'B': 0, 'C': 0}
            for k, dd in enumerate(dirs):
                ls.DATA = dd
                for p in sorted(glob.glob(f'{dd}/*_1h.csv')):
                    d = ls.load(os.path.basename(p)[:-7])
                    if d is None or len(d['c']) < 2000:
                        continue
                    t, o, c = (d['t'].astype(np.int64), d['o'], d['c']) if tf == '1h' else bars4h(d)
                    for a, b, r in trades(t, o, c, mode):
                        res['C' if a >= HOLD else 'AB'[k]].append(r)
                    tt = t.astype(np.int64)
                    days['AB'[k]] += max(0, (min(tt[-1], HOLD) - tt[0]) / 864e5); days['C'] += max(0, (tt[-1] - max(tt[0], HOLD)) / 864e5)
            for g in 'ABC':
                x = np.array(res[g])
                if not len(x):
                    continue
                pf = x[x > 0].sum() / -x[x < 0].sum() if (x < 0).any() else float('inf')
                L.append(f'| {tf} | {mode} | {g} | {len(x)} | {len(x)/max(days[g],1):.2f} | {x.mean()*100:+.3f}% | {pf:.2f} | '
                         f'{(x > 0).mean()*100:.0f}% | {(x.mean()+2*COST)*100:+.3f}% |')
                print(L[-1], flush=True)
    # Часть 2: фильтр к v1.2
    V.DIRS = dirs; ls.DATA = dirs[0]; V.REG = ls.btc_regime(ls.load('BTCUSDT'))
    L += ['', '## Часть 2: фильтр к v1.2 (портфель риск 0.3%, до 8 позиций)', '']
    tr, rets = V.collect(dirs, 1, 1.0)
    al = np.array([x['rsi_d'] > 0 for x in tr]); L.append(f'RSI выше своей EMA9 в сторону пробоя на сигнальном баре: {al.mean()*100:.0f}% сигналов.\n')
    one = lambda x, op, rs, R: 1.0
    VAR = [('База v1.2', one),
           ('F1 RSI против EMA9 → пропуск', lambda x, op, rs, R: 0.0 if x['rsi_d'] <= 0 else 1.0),
           ('F2 RSI перегрет (>75 лонг / <25 шорт) → пропуск', lambda x, op, rs, R: 0.0 if (x['rsi'] > 75 if x['s'] > 0 else x['rsi'] < 25) else 1.0),
           ('F3 RSI слабый (<60 лонг / >40 шорт) → пропуск', lambda x, op, rs, R: 0.0 if (x['rsi'] < 60 if x['s'] > 0 else x['rsi'] > 40) else 1.0)]
    for cm in (1, 2):
        if cm == 2:
            tr, rets = V.collect(dirs, 2, 1.0)
        L += [f'### Издержки ×{cm}', '', '| вариант | A | B | C |', '|---|---|---|---|']
        for name, mult in VAR:
            cells = []
            for g, sel in (('A', lambda x: x['grp'] == 'A' and x['t'] < HOLD), ('B', lambda x: x['grp'] == 'B' and x['t'] < HOLD),
                           ('C', lambda x: x['t'] >= HOLD)):
                eq, dd, n, _, _ = V.equity([x for x in tr if sel(x)], mult, rets)
                yrs = 4.33 if g != 'C' else 5 / 12
                cells.append(f'×{eq:.2f} / {dd*100:.0f}% / MAR {(eq ** (1 / yrs) - 1) / dd:.2f} ({n})')
            L.append(f'| {name} | ' + ' | '.join(cells) + ' |'); print(L[-1], flush=True)
        L.append('')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
