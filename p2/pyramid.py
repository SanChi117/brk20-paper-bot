#!/usr/bin/env python3
"""Добор в прибыльную позицию для системы v1 (сильный пробой + выход за EMA20). 54 монеты.
R нормирован на риск первой части. Сигнал и выход не меняются."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
import robust_ema as RE  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
FT, SLP = B.FT, B.SL_


def run(d, f, e, em, add_at, n_add, stop_mode, maxh=72):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    s, a = e['s'], e['a']; j0 = e['i'] + 1
    if j0 + maxh >= len(c):
        return None
    e1 = o[j0]; stop = e['lvl'] - s * 0.5 * a; risk = abs(e1 - stop)
    if risk <= 0 or s * (e1 - stop) <= 0:
        return None
    units = [e1]; cost = e1 * (FT + SLP); added = 0
    for j in range(j0, j0 + maxh):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            px = min(o[j], stop) if s == 1 else max(o[j], stop)
            pnl = sum(s * (px - u) for u in units) - cost - len(units) * px * (FT + SLP)
            return pnl / risk, int(t[j0]), int(t[j] + H), len(units)
        # добор: цена дошла до e1 + k·R (k = add_at·(номер добора)), исполнение по уровню (стоп-ордер)
        while added < n_add:
            lvl = e1 + s * add_at * (added + 1) * risk
            if (s == 1 and h[j] >= lvl) or (s == -1 and l[j] <= lvl):
                px = max(o[j], lvl) if s == 1 else min(o[j], lvl)
                units.append(px); cost += px * (FT + SLP); added += 1
                if stop_mode == 'be1':
                    stop = max(stop, e1) if s == 1 else min(stop, e1)
                elif stop_mode == 'prev':
                    nb = units[-2]
                    stop = max(stop, nb) if s == 1 else min(stop, nb)
            else:
                break
        if (s == 1 and c[j] < em[j]) or (s == -1 and c[j] > em[j]):
            pnl = sum(s * (c[j] - u) for u in units) - cost - len(units) * c[j] * (FT + SLP)
            return pnl / risk, int(t[j0]), int(t[j] + H), len(units)
    px = c[j0 + maxh - 1]
    pnl = sum(s * (px - u) for u in units) - cost - len(units) * px * (FT + SLP)
    return pnl / risk, int(t[j0]), int(t[j0 + maxh - 1] + H), len(units)


def main():
    d1, d2 = sys.argv[1], sys.argv[2]
    ls.DATA = d1; reg = ls.btc_regime(ls.load('BTCUSDT'))
    U = []
    for dd in (d1, d2):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            f = ls.features(d); em = X.ema(d['c'], 20)
            U.append((s, dd, d, f, em, [e for e in B.events(d, f, reg, True) if e['sc'] >= 5]))
    V = [('v1 без добора (база)', 1.0, 0, 'none'),
         ('добор на +1R, стоп в точку 1-го входа', 1.0, 1, 'be1'),
         ('добор на +1R, стоп не трогать', 1.0, 1, 'none'),
         ('добор на +1.5R, стоп в точку 1-го входа', 1.5, 1, 'be1'),
         ('2 добора: +1R и +2R, стоп к предыдущему входу', 1.0, 2, 'prev'),
         ('2 добора: +1.5R и +3R, стоп к предыдущему входу', 1.5, 2, 'prev')]
    L = ['# Добор в прибыльную позицию (54 монеты)', '', 'R = в долях риска ПЕРВОЙ части. После издержек.', '',
         '| вариант | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    best = {}
    for name, aa, na, sm in V:
        tr = []
        for s, dd, d, f, em, EV in U:
            for e in EV:
                r = run(d, f, e, em, aa, na, sm)
                if r:
                    tr.append(dict(R=r[0], t=r[1], tx=r[2], sym=s, s=e['s'], rg=e['rg'], dev=(dd == d1)))
        best[name] = tr
        for lab, sub in (('24 монеты, до июня 2026', [x for x in tr if x['dev'] and x['t'] < HOLD]),
                         ('30 новых, до июня 2026', [x for x in tr if not x['dev'] and x['t'] < HOLD]),
                         ('все 54, июнь–окт 2026', [x for x in tr if x['t'] >= HOLD])):
            L.append(f'| {name} | {lab} ' + B.stats([x['R'] for x in sub]))
    L += ['', '## Капитал: 54 монеты, риск 0.5% на первую часть, до 8 позиций']
    for name in best:
        a = RE.equity([x for x in best[name] if x['t'] < HOLD], 0.005, 8)
        b = RE.equity([x for x in best[name] if x['t'] >= HOLD], 0.005, 8)
        L.append(f'- {name}: 2022–май 2026 ×{a[0]:.2f}, просадка {a[1]*100:.0f}%; июнь–окт 2026 ×{b[0]:.2f}, просадка {b[1]*100:.0f}%')
    os.makedirs('p2_out', exist_ok=True); open('p2_out/PYRAMID.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
