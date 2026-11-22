#!/usr/bin/env python3
"""Стресс-тест v1.2 (сильный пробой, будни, добор +1R, выход за EMA20) на 54 монетах:
издержки ×2, фандинг, выход на открытии следующей свечи, Монте-Карло по месяцам, проверка под плечо 20x."""
import glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
import robust_ema as RE  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
FT0, SLP0 = 0.0005, 0.0003


def run(d, e, em, cm, next_open, fund):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    s, a = e['s'], e['a']; j0 = e['i'] + 1; maxh = 72
    if j0 + maxh + 1 >= len(c):
        return None
    k = (FT0 + SLP0) * cm
    e1 = o[j0]; stop = e['lvl'] - s * 0.5 * a; risk = abs(e1 - stop)
    if risk <= 0 or s * (e1 - stop) <= 0:
        return None
    units = [e1]; cost = e1 * k; added = False; ft = np.array([x[0] for x in d['fund']]); fr = np.array([x[1] for x in d['fund']])
    def fin(px, j_exit):
        pnl = sum(s * (px - u) for u in units) - cost - len(units) * px * k
        if fund and len(ft):
            a_, b_ = np.searchsorted(ft, t[j0]), np.searchsorted(ft, t[j_exit] + H)
            pnl -= s * fr[a_:b_].sum() * sum(units)        # лонг платит положительный фандинг
        return pnl / risk, int(t[j0]), int(t[j_exit] + H), risk / e1
    for j in range(j0, j0 + maxh):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            return fin(min(o[j], stop) if s == 1 else max(o[j], stop), j)
        if not added:
            lvl = e1 + s * risk
            if (s == 1 and h[j] >= lvl) or (s == -1 and l[j] <= lvl):
                px = max(o[j], lvl) if s == 1 else min(o[j], lvl); units.append(px); cost += px * k; added = True
        if (s == 1 and c[j] < em[j]) or (s == -1 and c[j] > em[j]):
            return fin(o[j + 1], j + 1) if next_open else fin(c[j], j)
    return fin(c[j0 + maxh - 1], j0 + maxh - 1)


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
            ev = [e for e in B.events(d, f, reg, True)
                  if e['sc'] >= 5 and pd.Timestamp(int(d['t'][e['i']]) + H, unit='ms').dayofweek < 5]
            U.append((s, d, em, ev))
    V = [('база (как в v1.2)', 1, False, False), ('+ фандинг', 1, False, True), ('+ выход на открытии следующей свечи', 1, True, False),
         ('издержки ×2', 2, False, False), ('ВСЁ ВМЕСТЕ: издержки ×2 + фандинг + выход на след. открытии', 2, True, True),
         ('издержки ×3 + фандинг + след. открытие', 3, True, True)]
    L = ['# Стресс-тест v1.2 (54 монеты)', '', '| условия | сделок | средн. R [95% ДИ] | 2022–май 2026: капитал / год / просадка | июнь–окт 2026: капитал / просадка |', '|---|---|---|---|---|']
    keep = {}
    for name, cm, no, fu in V:
        tr = []
        for s, d, em, ev in U:
            for e in ev:
                r = run(d, e, em, cm, no, fu)
                if r:
                    tr.append(dict(R=r[0], t=r[1], tx=r[2], sym=s, sp=r[3]))
        keep[name] = tr
        dev = [x for x in tr if x['t'] < HOLD]; ho = [x for x in tr if x['t'] >= HOLD]
        R = np.array([x['R'] for x in dev]); g = np.random.default_rng(1); bm = np.sort([g.choice(R, len(R)).mean() for _ in range(1000)])
        a = RE.equity(dev, 0.003, 8); b = RE.equity(ho, 0.003, 8)
        L.append(f'| {name} | {len(dev)} | {R.mean():+.3f} [{bm[25]:+.3f}; {bm[975]:+.3f}] | ×{a[0]:.2f} / {(a[0]**(1/4.33)-1)*100:+.0f}% / {a[1]*100:.0f}% | ×{b[0]:.2f} / {b[1]*100:.0f}% |')
    # Монте-Карло по месяцам (жёсткий вариант)
    hard = keep[V[4][0]]
    eq, dd, nt, mr = RE.equity([x for x in hard if x['t'] < HOLD], 0.003, 8)
    rng = np.random.default_rng(7); M = len(mr); paths = []
    for _ in range(5000):
        smp = rng.choice(mr, M); v = np.cumprod(1 + smp); pk = np.maximum.accumulate(np.r_[1, v])[1:]
        paths.append((v[-1], (1 - v / pk).max(), (v[11] if M > 11 else v[-1])))
    fin = np.array([p[0] for p in paths]); mdd = np.array([p[1] for p in paths]); y1 = np.array([p[2] for p in paths])
    L += ['', f'## Монте-Карло (жёсткий вариант, 5000 перестановок месяцев, {M} мес., риск 0.3%)', '',
          f'- Капитал за весь срок: медиана ×{np.median(fin):.1f}; худшие 5% путей ×{np.percentile(fin, 5):.2f}; доля путей в убытке {np.mean(fin < 1)*100:.1f}%.',
          f'- Первый год: медиана ×{np.median(y1):.2f}; худшие 5% ×{np.percentile(y1, 5):.2f}; доля лет в убытке {np.mean(y1 < 1)*100:.0f}%.',
          f'- Макс. просадка: медиана {np.median(mdd)*100:.0f}%; в 1 из 20 путей хуже {np.percentile(mdd, 95)*100:.0f}%; в 1 из 100 хуже {np.percentile(mdd, 99)*100:.0f}%.']
    # плечо
    sp = np.array([x['sp'] for x in keep[V[0][0]]])
    notional = 0.003 / sp
    L += ['', '## Под плечо 20x', '',
          f'- Расстояние до стопа: медиана {np.median(sp)*100:.2f}% цены, 95-й процентиль {np.percentile(sp, 95)*100:.2f}%, максимум {sp.max()*100:.2f}%.',
          f'- Сделок со стопом дальше 3.5% (близко к ликвидации на 20x, ~4.5%): {np.mean(sp > 0.035)*100:.1f}% — бот их пропускает или берёт с меньшим плечом.',
          f'- Размер позиции при риске 0.3%: медиана {np.median(notional)*100:.0f}% счёта (с добором — до {np.median(notional)*200:.0f}%). '
          f'Маржа на 20x: ~{np.median(notional)*100/20:.1f}% счёта на позицию; 8 позиций с доборами ≈ {np.median(notional)*1600/20:.0f}% счёта.']
    os.makedirs('p2_out', exist_ok=True); open('p2_out/STRESS.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
