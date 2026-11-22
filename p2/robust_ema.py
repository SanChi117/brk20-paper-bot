#!/usr/bin/env python3
"""Устойчивость выхода «закрытие за EMA» для сильных пробоев: соседние настройки, годы, контроль слабыми входами,
капитал с одновременными позициями."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD


def trades(D, F, EVs, n_ema, maxh):
    out = []
    for s in F:
        em = X.ema(D[s]['c'], n_ema)
        for e in EVs[s]:
            r = X.run_exit(D[s], F[s], e, ('ema',), em, maxh=maxh)
            if r:
                out.append(dict(R=r[0], t=r[1], sym=s, hz=maxh))
    return out


def exit_time(D, F, EVs, n_ema, maxh):
    """для капитала нужна длительность: пересчёт простым проходом (время выхода по правилу)"""
    res = []
    for s in F:
        d = D[s]; em = X.ema(d['c'], n_ema); o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
        for e in EVs[s]:
            r = X.run_exit(d, F[s], e, ('ema',), em, maxh=maxh)
            if not r:
                continue
            sd, j0 = e['s'], e['i'] + 1; stop = e['lvl'] - sd * 0.5 * e['a']; tx = t[min(j0 + maxh - 1, len(t) - 1)] + H
            for j in range(j0, min(j0 + maxh, len(c))):
                if (sd == 1 and l[j] <= stop) or (sd == -1 and h[j] >= stop) or (sd == 1 and c[j] < em[j]) or (sd == -1 and c[j] > em[j]):
                    tx = t[j] + H; break
            res.append(dict(R=r[0], t=r[1], tx=int(tx), sym=s))
    return res


def equity(tr, risk, maxpos=5):
    ev = sorted(tr, key=lambda x: x['t']); eq = 1.0; pk = 1.0; dd = 0.0; op = []; taken = 0; months = {}
    for x in ev:
        op.sort(key=lambda y: y[0])
        while op and op[0][0] <= x['t']:
            tx, st, R, _ = op.pop(0); eq += st * R; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
            months[np.datetime64(int(tx), 'ms').astype('datetime64[M]')] = eq
        if len(op) < maxpos and not any(y[3] == x['sym'] for y in op):
            op.append((x['tx'], eq * risk, x['R'], x['sym'])); taken += 1
    for tx, st, R, _ in sorted(op, key=lambda y: y[0]):
        eq += st * R; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
        months[np.datetime64(int(tx), 'ms').astype('datetime64[M]')] = eq
    ks = sorted(months); vals = [months[k] for k in ks]
    mret = np.diff(np.r_[1.0, vals]) / np.r_[1.0, vals[:-1]]
    return eq, dd, taken, mret


def main():
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{ls.DATA}/*_1h.csv'))
    D = {s: ls.load(s) for s in syms}; reg = ls.btc_regime(D['BTCUSDT'])
    F = {s: ls.features(D[s]) for s in syms if len(D[s]['c']) >= 2000}
    ALL = {s: B.events(D[s], F[s], reg, True) for s in F}
    ALLnc = {s: B.events(D[s], F[s], reg, False) for s in F}
    sel = lambda E, f: {s: [e for e in E[s] if f(e)] for s in E}
    strong = sel(ALL, lambda e: e['sc'] >= 5)
    L = ['# Устойчивость выхода «закрытие за EMA» (сильные пробои)', '',
         '| вариант | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    def row(name, tr):
        L.append(f'| {name} | dev ' + B.stats([x['R'] for x in tr if x['t'] < HOLD]))
        L.append(f'| ↳ | hold ' + B.stats([x['R'] for x in tr if x['t'] >= HOLD]))
    for ne in (10, 20, 30, 50):
        for mh in (72, 168):
            row(f'EMA{ne}, лимит {mh}ч', trades(D, F, strong, ne, mh))
    row('КОНТРОЛЬ: слабые пробои (оценка 0–2), EMA20', trades(D, F, sel(ALL, lambda e: e['sc'] <= 2), 20, 72))
    row('средние пробои (оценка 3–4), EMA20', trades(D, F, sel(ALL, lambda e: 3 <= e['sc'] <= 4), 20, 72))
    row('сильные без условия сжатия, EMA20', trades(D, F, sel(ALLnc, lambda e: e['sc'] >= 5), 20, 72))
    row('сильные по режиму BTC, EMA20', trades(D, F, sel(ALL, lambda e: e['sc'] >= 5 and ((e['rg'] == 'up' and e['s'] == 1) or (e['rg'] == 'down' and e['s'] == -1))), 20, 72))
    row('только лонги, EMA20', trades(D, F, sel(ALL, lambda e: e['sc'] >= 5 and e['s'] == 1), 20, 72))
    row('только шорты, EMA20', trades(D, F, sel(ALL, lambda e: e['sc'] >= 5 and e['s'] == -1), 20, 72))
    main_tr = exit_time(D, F, strong, 20, 72)
    L += ['', '## EMA20, лимит 72ч — по годам', '| год | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|']
    for y in range(2022, 2027):
        a = int(np.datetime64(f'{y}-01-01', 'ms').astype('int64')); b = int(np.datetime64(f'{y+1}-01-01', 'ms').astype('int64'))
        L.append(f'| {y} ' + B.stats([x['R'] for x in main_tr if a <= x['t'] < b]))
    L += ['', '## Капитал (сложный процент, ≤5 позиций одновременно, одна на монету)']
    for r in (0.01, 0.02, 0.03):
        for lab, sub in (('2022–май 2026', [x for x in main_tr if x['t'] < HOLD]), ('июнь–окт 2026', [x for x in main_tr if x['t'] >= HOLD])):
            eq, dd, nt, mr = equity(sub, r)
            L.append(f'- риск {r*100:.0f}%, {lab}: ×{eq:.2f}, макс. просадка {dd*100:.0f}%, сделок взято {nt}, '
                     (f'месяцев в плюсе {np.mean(mr > 0)*100:.0f}%, медиана месяца {np.median(mr)*100:+.1f}%, худший месяц {mr.min()*100:+.1f}%' if len(mr) else ''))
    os.makedirs('p2_out', exist_ok=True); open('p2_out/ROBUST_EMA.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
