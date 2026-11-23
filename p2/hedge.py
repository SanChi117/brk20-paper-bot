#!/usr/bin/env python3
"""v1.2 + хеджи на откатах внутри трендовой сделки (54 монеты).
Основная позиция = v1.2 как есть (будни, балл 5–6, добор +1R, выход за EMA20 на след. открытии, фандинг, издержки).
Хедж: пока основная открыта, если 1h закрылась дальше K·ATR14 от EMA20 по ходу тренда (и RSI14 перегрет, если задано),
открываем встречную позицию на следующем открытии размером hs × (число частей основной). Тейк хеджа — касание EMA20
(уровень прошлого бара), стоп — вход хеджа + HS·ATR против. Хедж закрывается и вместе с основной. Можно хеджировать
повторно после закрытия предыдущего. Внутри бара: сначала стопы, потом тейк (консервативно). Фандинг хеджа не учтён.
R = в долях риска первой части основной позиции."""
import glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
import robust_ema as RE  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
K_ = 0.0005 + 0.0003


def atr(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    a = np.empty_like(tr); a[0] = tr[0]
    for i in range(1, len(tr)):
        a[i] = a[i - 1] + (tr[i] - a[i - 1]) / n
    return a


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0]); up = np.maximum(d, 0); dn = np.maximum(-d, 0)
    au = np.empty_like(c); ad = np.empty_like(c); au[0] = up[0]; ad[0] = dn[0]
    for i in range(1, len(c)):
        au[i] = au[i - 1] + (up[i] - au[i - 1]) / n; ad[i] = ad[i - 1] + (dn[i] - ad[i - 1]) / n
    return 100 - 100 / (1 + au / np.maximum(ad, 1e-12))


def run(d, e, em, AT, RS, V):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    s, a = e['s'], e['a']; j0 = e['i'] + 1; maxh = 72
    if j0 + maxh + 1 >= len(c):
        return None
    e1 = o[j0]; stop = e['lvl'] - s * 0.5 * a; risk = abs(e1 - stop)
    if risk <= 0 or s * (e1 - stop) <= 0:
        return None
    units = [e1]; cost = e1 * K_; added = False
    ft = np.array([x[0] for x in d['fund']]); fr = np.array([x[1] for x in d['fund']])
    hg = None; hpnl = 0.0; hn = 0; hw = 0; pend = False

    def hclose(px):
        nonlocal hg, hpnl, hn, hw
        q, he = hg['q'], hg['e']
        p = -s * (px - he) * q - (he + px) * q * K_
        hpnl += p; hn += 1; hw += p > 0; hg = None

    def fin(px, j_exit):
        if hg:
            hclose(px)
        pnl = sum(s * (px - u) for u in units) - cost - len(units) * px * K_
        if len(ft):
            a_, b_ = np.searchsorted(ft, t[j0]), np.searchsorted(ft, t[j_exit] + H)
            pnl -= s * fr[a_:b_].sum() * sum(units)
        return dict(R=(pnl + hpnl) / risk, Rh=hpnl / risk, t=int(t[j0]), tx=int(t[j_exit] + H), hn=hn, hw=hw)

    for j in range(j0, j0 + maxh):
        if pend and V:
            hg = dict(e=o[j], q=V['hs'] * len(units), st=o[j] + s * V['HS'] * AT[j - 1]); pend = False
            cost_dummy = 0  # noqa: F841
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            return fin(min(o[j], stop) if s == 1 else max(o[j], stop), j)
        if hg:
            if (s == 1 and h[j] >= hg['st']) or (s == -1 and l[j] <= hg['st']):
                hclose(max(o[j], hg['st']) if s == 1 else min(o[j], hg['st']))
            elif (s == 1 and l[j] <= em[j - 1]) or (s == -1 and h[j] >= em[j - 1]):
                hclose(min(o[j], em[j - 1]) if s == 1 else max(o[j], em[j - 1]))
        if not added:
            lvl = e1 + s * risk
            if (s == 1 and h[j] >= lvl) or (s == -1 and l[j] <= lvl):
                px = max(o[j], lvl) if s == 1 else min(o[j], lvl); units.append(px); cost += px * K_; added = True
        if (s == 1 and c[j] < em[j]) or (s == -1 and c[j] > em[j]):
            return fin(o[j + 1], j + 1)
        if V and hg is None and s * (c[j] - em[j]) >= V['K'] * AT[j]:
            if V.get('rsi') is None or (s == 1 and RS[j] >= V['rsi']) or (s == -1 and RS[j] <= 100 - V['rsi']):
                pend = True
    return fin(c[j0 + maxh - 1], j0 + maxh - 1)


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
            U.append((s, dd == d1, d, em, atr(d['h'], d['l'], d['c']), rsi(d['c']), ev))
    VS = [('v1.2 без хеджей (база)', None),
          ('хедж 50%, отрыв 2 ATR, стоп 1 ATR', dict(K=2, hs=0.5, HS=1.0)),
          ('хедж 100%, отрыв 2 ATR, стоп 1 ATR', dict(K=2, hs=1.0, HS=1.0)),
          ('хедж 50%, отрыв 3 ATR, стоп 1.5 ATR', dict(K=3, hs=0.5, HS=1.5)),
          ('хедж 100%, отрыв 3 ATR, стоп 1.5 ATR', dict(K=3, hs=1.0, HS=1.5)),
          ('хедж 100%, отрыв 2 ATR + RSI≥75, стоп 1 ATR', dict(K=2, hs=1.0, HS=1.0, rsi=75)),
          ('хедж 50%, отрыв 4 ATR, стоп 2 ATR', dict(K=4, hs=0.5, HS=2.0))]
    L = ['# v1.2 + хеджи на откатах (54 монеты)', '', 'R в долях риска первой части. Издержки, фандинг основной, выход на след. открытии учтены.', '',
         '| вариант | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    ALL = {}
    for name, V in VS:
        tr = []
        for s, dev, d, em, AT, RS, ev in U:
            for e in ev:
                r = run(d, e, em, AT, RS, V)
                if r:
                    r.update(sym=s, dev=dev); tr.append(r)
        ALL[name] = tr
        for lab, sub in (('24 монеты, до июня 2026', [x for x in tr if x['dev'] and x['t'] < HOLD]),
                         ('30 новых, до июня 2026', [x for x in tr if not x['dev'] and x['t'] < HOLD]),
                         ('все 54, июнь–окт 2026', [x for x in tr if x['t'] >= HOLD])):
            L.append(f'| {name} | {lab} ' + B.stats([x['R'] for x in sub]))
    L += ['', '## Сами хеджи и что они сделали с лучшими трендами (все 54 монеты, вся история)', '',
          '| вариант | хеджей | хеджей в плюс | вклад хеджей ΣR | ΣR топ-10 сделок базы → с хеджами |', '|---|---|---|---|---|']
    base = {(x['sym'], x['t']): x['R'] for x in ALL[VS[0][0]]}
    top = sorted(base, key=base.get, reverse=True)[:10]
    for name, V in VS[1:]:
        tr = ALL[name]; m = {(x['sym'], x['t']): x['R'] for x in tr}
        hn = sum(x['hn'] for x in tr); hw = sum(x['hw'] for x in tr)
        L.append(f"| {name} | {hn} | {hw / hn * 100 if hn else 0:.0f}% | {sum(x['Rh'] for x in tr):+.0f} | "
                 f"{sum(base[k] for k in top):+.0f} → {sum(m.get(k, 0) for k in top):+.0f} |")
    L += ['', '## Капитал (54 монеты, до 8 позиций)', '', '| вариант | риск | 2022–май 2026: × / %год / просадка | июнь–окт 2026: × / просадка |', '|---|---|---|---|']
    for name, V in VS:
        for r in (0.003, 0.01):
            a = RE.equity([x for x in ALL[name] if x['t'] < HOLD], r, 8); b = RE.equity([x for x in ALL[name] if x['t'] >= HOLD], r, 8)
            L.append(f'| {name} | {r * 100:.1f}% | ×{a[0]:.2f} / {(a[0] ** (1 / 4.33) - 1) * 100:+.0f}% / {a[1] * 100:.0f}% | ×{b[0]:.2f} / {b[1] * 100:.0f}% |')
    os.makedirs('p2_out', exist_ok=True); open('p2_out/HEDGE.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
