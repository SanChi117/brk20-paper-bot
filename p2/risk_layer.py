#!/usr/bin/env python3
"""Слой риска для системы «сильный пробой + выход за EMA20» на 54 монетах (24 разработки + 30 новых).
Сигнал не меняется; меняется только портфель: риск на сделку, лимит позиций, лимит в одну сторону,
снижение риска в просадке, фильтр режима BTC."""
import glob, os, sys, itertools
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import robust_ema as RE  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD


def collect(d1, d2):
    ls.DATA = d1; btc = ls.load('BTCUSDT'); reg = ls.btc_regime(btc); out = []
    for dd in (d1, d2):
        ls.DATA = dd
        syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv'))
        D = {s: ls.load(s) for s in syms}
        F = {s: ls.features(D[s]) for s in syms if D[s] is not None and len(D[s]['c']) >= 2000}
        EV = {s: [e for e in B.events(D[s], F[s], reg, True) if e['sc'] >= 5] for s in F}
        out += RE.exit_time(D, F, EV, 20, 72)
    return out


def sim(tr, risk, maxpos, maxdir, throttle, btcf):
    ev = sorted(tr, key=lambda x: x['t']); eq = pk = 1.0; dd = 0.0; op = []; months = {}; n = 0
    def close_until(T):
        nonlocal eq, pk, dd
        op.sort(key=lambda y: y['tx'])
        while op and op[0]['tx'] <= T:
            y = op.pop(0); eq += y['st'] * y['R']; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
            months[np.datetime64(int(y['tx']), 'ms').astype('datetime64[M]')] = eq
    for x in ev:
        close_until(x['t'])
        if btcf and not ((x['rg'] == 'up' and x['s'] == 1) or (x['rg'] == 'down' and x['s'] == -1)):
            continue
        if len(op) >= maxpos or any(y['sym'] == x['sym'] for y in op):
            continue
        if sum(y['s'] == x['s'] for y in op) >= maxdir:
            continue
        r = risk * (0.5 if throttle and eq < pk * 0.8 else 1.0)
        op.append(dict(tx=x['tx'], st=eq * r, R=x['R'], sym=x['sym'], s=x['s'])); n += 1
    close_until(2**62)
    ks = sorted(months); v = np.array([months[k] for k in ks]); mr = np.diff(np.r_[1.0, v]) / np.r_[1.0, v[:-1]]
    yrs = (ev[-1]['t'] - ev[0]['t']) / (365 * 24 * H) if ev else 1
    cagr = eq ** (1 / yrs) - 1 if eq > 0 else -1
    return eq, dd, cagr, n, (mr > 0).mean() if len(mr) else 0, mr.min() if len(mr) else 0


def main():
    tr = collect(sys.argv[1], sys.argv[2])
    print('trades', len(tr), flush=True)
    rows = []
    for risk, mp, md, th, bf in itertools.product((0.005, 0.01, 0.015), (3, 5, 8), (2, 3, 99), (False, True), (False, True)):
        if md != 99 and md >= mp:
            continue
        a = sim([x for x in tr if x['t'] < HOLD], risk, mp, md, th, bf)
        b = sim([x for x in tr if x['t'] >= HOLD], risk, mp, md, th, bf)
        rows.append(((risk, mp, md, th, bf), a, b))
    rows.sort(key=lambda r: -(r[1][2] / max(r[1][1], 0.05)))
    L = ['# Слой риска: 54 монеты, сигнал неизменен', '', f'Сделок-кандидатов: {len(tr)}. Сортировка по доходность/просадка на 2022–май 2026.', '',
         '| риск | макс. поз. | макс. в одну сторону | снижение в просадке | фильтр BTC | 2022–май 2026: год. / просадка / капитал | мес. в плюсе / худший мес. | июнь–окт 2026: капитал / просадка |',
         '|---|---|---|---|---|---|---|---|']
    for (risk, mp, md, th, bf), a, b in rows[:20]:
        L.append(f"| {risk*100:.1f}% | {mp} | {'—' if md == 99 else md} | {'да' if th else 'нет'} | {'да' if bf else 'нет'} | "
                 f"{a[2]*100:+.0f}% / {a[1]*100:.0f}% / ×{a[0]:.2f} | {a[4]*100:.0f}% / {a[5]*100:+.0f}% | ×{b[0]:.2f} / {b[1]*100:.0f}% |")
    L += ['', 'Худшие 5 конфигураций (для понимания разброса):']
    for (risk, mp, md, th, bf), a, b in rows[-5:]:
        L.append(f"- риск {risk*100:.1f}%, поз {mp}, сторона {'—' if md == 99 else md}, снижение {'да' if th else 'нет'}, BTC {'да' if bf else 'нет'}: "
                 f"{a[2]*100:+.0f}%/год, просадка {a[1]*100:.0f}%")
    os.makedirs('p2_out', exist_ok=True); open('p2_out/RISK.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
