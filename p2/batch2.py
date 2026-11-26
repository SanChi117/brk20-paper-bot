#!/usr/bin/env python3
"""Пакет из 4 идей (54 монеты, 1h + фандинг). См. p2/PREREG.md, раздел «Пакет 2».
A. Арбитраж фандинга (лонг спот + шорт перп): держим монету, пока средний фандинг за 24 ч > порога.
B. Время суток: часы UTC со значимо положительной средней доходностью корзины на dev (до 2024-06) -> лонг корзины в эти часы.
C. Дневной откат: каждый день 00:00 UTC лонг 5 самых упавших за 24 ч, шорт 5 самых выросших, держим 24 ч.
D. После выплаты фандинга: при |ставке| >= порога входим против знака ставки на 4 ч (шорт после высокой, лонг после отрицательной)."""
import glob, os, sys, csv
import numpy as np
H = 3600 * 1000
DEV_END = int(np.datetime64('2024-06-01T00:00:00', 'ms').astype('int64'))
HOLD = int(np.datetime64('2026-06-01T00:00:00', 'ms').astype('int64'))


def load_all(dirs):
    U = {}
    for dd, grp in dirs:
        for p in glob.glob(f'{dd}/*_1h.csv'):
            s = os.path.basename(p)[:-7]
            if s in U:
                continue
            t, c = [], []
            with open(p) as f:
                for r in csv.DictReader(f):
                    t.append(int(r['t'])); c.append(float(r['c']))
            fu = []
            fp = f'{dd}/{s}_funding.csv'
            if os.path.exists(fp):
                with open(fp) as f:
                    for r in csv.DictReader(f):
                        fu.append((int(r['t']), float(r['rate'])))
            U[s] = dict(grp=grp, t=np.array(t, dtype=np.int64), c=np.array(c), f=sorted(fu))
    return U


def grid(U):
    t0 = min(int(v['t'][0]) for v in U.values()); t1 = max(int(v['t'][-1]) for v in U.values())
    T = np.arange(t0, t1 + H, H, dtype=np.int64); S = sorted(U)
    C = np.full((len(S), len(T)), np.nan)
    for k, s in enumerate(S):
        idx = ((U[s]['t'] - t0) // H).astype(int); C[k, idx] = U[s]['c']
    return S, T, C


def ci(x):
    x = np.asarray(x)
    if len(x) < 5:
        return 'n<5'
    bs = np.random.default_rng(1).choice(x, (2000, len(x))).mean(1); lo, hi = np.percentile(bs, [2.5, 97.5])
    return f'{x.mean() * 100:+.3f}% [{lo * 100:+.3f}; {hi * 100:+.3f}] n={len(x)}'


def ann(daily):
    d = np.asarray(daily)
    if len(d) == 0:
        return '—'
    eq = np.cumprod(1 + d); dd = 1 - eq / np.maximum.accumulate(eq)
    return f'{(eq[-1] ** (365 / len(d)) - 1) * 100:+.1f}%/год, просадка {dd.max() * 100:.0f}%'


def periods(T, vals):
    out = {}
    for lab, a, b in (('dev до 06.2024', None, DEV_END), ('проверка 06.2024–05.2026', DEV_END, HOLD), ('holdout 06–10.2026', HOLD, None)):
        m = np.ones(len(T), bool)
        if a: m &= T >= a
        if b: m &= T < b
        out[lab] = vals[m]
    return out


def idea_A(U, L):
    L += ['## A. Арбитраж фандинга (дельта-нейтрально)', '',
          'Доход = фандинг − издержки входа/выхода 0.30% (спот 0.1% + перп 0.05% на сторону). Капитал на позицию: спот 100% + маржа перпа 33%.',
          'Портфель: до 5 монет одновременно, равные доли, выбор — по наибольшему среднему фандингу за 24 ч.', '',
          '| порог входа (средн. ставка за 8ч) | выборка | годовых на капитал | дней в плюсе | средн. позиций |', '|---|---|---|---|---|']
    ev = {}
    for s, v in U.items():
        for t, r in v['f']:
            ev.setdefault(t // H * H, {})[s] = r
    ts = sorted(ev)
    for thr in (0.0001, 0.0002, 0.0003, 0.0005):
        for grpsel, lab in ((None, 'все монеты'), ('A', '24 монеты'), ('B', '30 монет')):
            hist = {}; held = set(); daily = {}; npos = []
            for t in ts:
                for s, r in ev[t].items():
                    if grpsel and U[s]['grp'] != grpsel:
                        continue
                    hist.setdefault(s, []).append(r)
                cand = sorted(((np.mean(h[-3:]), s) for s, h in hist.items() if len(h) >= 3 and s in ev[t]), reverse=True)
                want = {s for m, s in cand[:5] if m > thr}
                keep = {s for s in held if s in ev[t] and np.mean(hist[s][-3:]) > thr / 2}
                new = set(list(keep) + [s for m, s in cand if s in want and s not in keep][:5 - len(keep)])
                cost = 0.003 * (len(new - held) + len(held - new)) / 5 / 1.33
                inc = sum(ev[t][s] for s in new if s in ev[t]) / 5 / 1.33
                d = t // (24 * H)
                daily[d] = daily.get(d, 0) + inc - cost
                held = new; npos.append(len(held))
            days = np.array(sorted(daily)); vals = np.array([daily[k] for k in days])
            for pl, x in periods(days * 24 * H, vals).items():
                L.append(f'| {thr * 100:.2f}% | {lab}, {pl} | {ann(x)} | {(x > 0).mean() * 100:.0f}% | {np.mean(npos):.1f} |')
    L.append('')


def idea_B(S, T, C, L):
    R = np.diff(np.log(C), axis=1); Tr = T[1:]
    basket = np.nanmean(R, axis=0); hours = (Tr // H) % 24
    L += ['## B. Время суток (корзина 54 монет, лонг)', '', '| час UTC | dev средн. | проверка средн. | holdout средн. |', '|---|---|---|---|']
    sel = []
    for h in range(24):
        m = hours == h; row = []
        for lab, x in periods(Tr[m], basket[m]).items():
            x = x[np.isfinite(x)]; row.append(ci(x))
        dv = basket[m & (Tr < DEV_END)]; dv = dv[np.isfinite(dv)]
        t = dv.mean() / (dv.std() / np.sqrt(len(dv)))
        if t > 2: sel.append(h)
        L.append(f'| {h:02d} | ' + ' | '.join(row) + f' | t={t:+.1f}' if False else f'| {h:02d} | ' + ' | '.join(row) + ' |')
    L.append(f'\nЧасы, выбранные на dev (t > 2): {sel}')
    if sel:
        m = np.isin(hours, sel)
        day = Tr // (24 * H)
        for lab, (a, b) in {'dev': (None, DEV_END), 'проверка': (DEV_END, HOLD), 'holdout': (HOLD, None)}.items():
            mm = m.copy()
            if a: mm &= Tr >= a
            if b: mm &= Tr < b
            dd = {}
            for d, r in zip(day[mm], basket[mm]):
                if np.isfinite(r): dd[d] = dd.get(d, 0) + r
            vals = np.array([v - 0.0016 for v in dd.values()])        # вход/выход раз в день
            L.append(f'- стратегия «лонг корзины в выбранные часы», {lab}: {ann(np.expm1(vals))}, дней {len(vals)}')
    L.append('')


def idea_C(S, T, C, U, L):
    L += ['## C. Дневной откат (лонг 5 упавших / шорт 5 выросших за 24 ч, держать 24 ч)', '',
          'Издержки 0.08% на сторону, полная смена корзины каждый день.', '']
    idx = np.where((T % (24 * H)) == 0)[0]
    for grpsel, lab in ((None, 'все монеты'), ('A', '24 монеты'), ('B', '30 монет')):
        rows = [k for k, s in enumerate(S) if grpsel is None or U[s]['grp'] == grpsel]
        days, vals = [], []
        for i in idx:
            if i < 24 or i + 24 >= len(T):
                continue
            past = C[rows, i] / C[rows, i - 24] - 1; fut = C[rows, i + 24] / C[rows, i] - 1
            ok = np.isfinite(past) & np.isfinite(fut)
            if ok.sum() < 12:
                continue
            p, f = past[ok], fut[ok]; o = np.argsort(p)
            r = f[o[:5]].mean() - f[o[-5:]].mean()
            days.append(T[i]); vals.append(r / 2 - 4 * 0.0008)
        for pl, x in periods(np.array(days), np.array(vals)).items():
            L.append(f'- {lab}, {pl}: {ci(x)}; {ann(x)}')
    L.append('')


def idea_D(U, L):
    L += ['## D. Против знака крупной выплаты фандинга, держать 4 ч', '', 'Издержки 0.08% на сторону.', '',
          '| порог |ставки| | выборка | средн. сделка [95% ДИ] |', '|---|---|---|---|']
    for thr in (0.0005, 0.001, 0.002):
        for grpsel, lab in ((None, 'все'), ('A', '24 монеты'), ('B', '30 монет')):
            tt, rr = [], []
            for s, v in U.items():
                if grpsel and v['grp'] != grpsel:
                    continue
                pos = {int(t): c for t, c in zip(v['t'], v['c'])}
                for t, r in v['f']:
                    if abs(r) < thr:
                        continue
                    t0 = t // H * H
                    a, b = pos.get(t0), pos.get(t0 + 4 * H)
                    if a and b:
                        sd = -1 if r > 0 else 1
                        tt.append(t0); rr.append(sd * (b / a - 1) - 0.0016)
            tt, rr = np.array(tt), np.array(rr)
            for pl, x in periods(tt, rr).items():
                L.append(f'| {thr * 100:.2f}% | {lab}, {pl} | {ci(x)} |')
    L.append('')


def main():
    U = load_all([(sys.argv[1], 'A'), (sys.argv[2], 'B')])
    S, T, C = grid(U)
    L = ['# Пакет 2: четыре идеи (54 монеты, 1h)', '']
    for f in (lambda: idea_A(U, L), lambda: idea_B(S, T, C, L), lambda: idea_C(S, T, C, U, L), lambda: idea_D(U, L)):
        try:
            f()
        except Exception as e:
            import traceback; L.append('ОШИБКА: ' + traceback.format_exc())
    os.makedirs('p2_out', exist_ok=True); open('p2_out/BATCH2.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
