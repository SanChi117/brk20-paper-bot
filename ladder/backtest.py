#!/usr/bin/env python3
"""«Лестница»: вход тремя частями в откат по тренду с общим стопом. См. ladder/PREREG.md."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mme'))
import label_study as ls  # noqa: E402

H, HOLD = ls.H, ls.HOLD
FT, FM, SLP = 0.0005, 0.0002, 0.0003
START = int(np.datetime64('2022-02-01', 'ms').astype('int64'))


def campaign(d, i, s, a, steps, w, stop_k, tp_k, maxh=72):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    j0 = i + 1
    if j0 + maxh >= len(c):
        return None
    e1 = o[j0]
    lv = [e1 - s * k * a for k in steps]              # цены частей (1-я = e1)
    stop = e1 - s * stop_k * a
    W = np.array(w, float) / sum(w)
    risk_full = sum(W[k] * abs(lv[k] - stop) for k in range(3))   # 1R в цене на единицу номинала
    filled = [0]; px = {0: e1}; cost = W[0] * e1 * (FT + SLP)
    for j in range(j0, j0 + maxh):
        new = False
        for k in (1, 2):
            if k not in filled and ((s == 1 and l[j] <= lv[k]) or (s == -1 and h[j] >= lv[k])):
                px[k] = min(o[j], lv[k]) if s == 1 else max(o[j], lv[k]); filled.append(k); cost += W[k] * px[k] * FM; new = True
        wq = sum(W[k] for k in filled); avg = sum(W[k] * px[k] for k in filled) / wq
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            ex = min(o[j], stop) if s == 1 else max(o[j], stop)
            pnl = sum(W[k] * s * (ex - px[k]) for k in filled) - cost - wq * ex * (FT + SLP)
            return pnl / risk_full, int(t[j0]), len(filled), 'SL'
        tp = avg + s * tp_k * a
        if not new and ((s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp)):
            pnl = sum(W[k] * s * (tp - px[k]) for k in filled) - cost - wq * tp * FM
            return pnl / risk_full, int(t[j0]), len(filled), 'TP'
    ex = c[j0 + maxh - 1]
    pnl = sum(W[k] * s * (ex - px[k]) for k in filled) - cost - sum(W[k] for k in filled) * ex * (FT + SLP)
    return pnl / risk_full, int(t[j0]), len(filled), 'TIME'


def run(D, F, reg, regime=True, steps=(0, 2, 4), w=(1, 1, 1), stop_k=6, tp_k=2, trig=3):
    out = []
    for s_, d in D.items():
        f = F[s_]; c, h, l, t = d['c'], d['h'], d['l'], d['t']
        sm = np.convolve(c, np.ones(1200) / 1200)[:len(c)]
        hi24 = np.full(len(c), np.nan); lo24 = np.full(len(c), np.nan)
        for i in range(24, len(c)):
            hi24[i] = h[i - 24:i + 1].max(); lo24[i] = l[i - 24:i + 1].min()
        nxt = 0
        for i in range(1300, len(c) - 80):
            if t[i] < START or i < nxt:
                continue
            a = f['atr'][i]
            if not a > 0:
                continue
            rg = reg.get(int((t[i] + H) // (24 * H)), 'na')
            sd = 0
            if (hi24[i] - c[i]) >= trig * a and (not regime or (rg == 'up' and c[i] > sm[i])):
                sd = 1
            elif (c[i] - lo24[i]) >= trig * a and (not regime or (rg == 'down' and c[i] < sm[i])):
                sd = -1
            if not sd:
                continue
            r = campaign(d, i, sd, a, steps, w, stop_k, tp_k)
            if r:
                R, te, nf, why = r
                pf = sum(x[1] for x in d['fund'] if te <= x[0] < te + 72 * H)  # грубо: фандинг до 72ч, малый
                out.append(dict(R=R, t=te, nf=nf, why=why, s=sd, sym=s_))
                nxt = i + 24
    return out


def stats(R):
    if len(R) == 0:
        return '| 0 | – | – | – | – |'
    R = np.array(R); g = np.random.default_rng(1); bm = np.sort([g.choice(R, len(R)).mean() for _ in range(1000)])
    w = R[R > 0].sum(); lo = -R[R < 0].sum()
    return f'| {len(R)} | {(R>0).mean()*100:.0f}% | {R.mean():+.3f} [{bm[25]:+.3f}; {bm[975]:+.3f}] | {w/lo if lo else 0:.2f} | {R.sum():+.0f} |'


def equity(tr, risk):
    eq, pk, dd = 1.0, 1.0, 0.0
    for x in sorted(tr, key=lambda x: x['t']):
        eq *= max(1 + risk * x['R'], 0); pk = max(pk, eq); dd = max(dd, 1 - eq / pk if pk else 1)
    return eq, dd


def main():
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{ls.DATA}/*_1h.csv'))
    D = {s: ls.load(s) for s in syms}; reg = ls.btc_regime(D['BTCUSDT'])
    F = {s: ls.features(D[s]) for s in syms}
    V = {'ОСНОВНОЙ: по тренду, шаги 0/2/4, стоп 6, тейк +2 ATR': {},
         'без фильтра режима (против тренда тоже)': dict(regime=False),
         'тейк +1 ATR': dict(tp_k=1),
         'части 1-2-3 (больше внизу)': dict(w=(1, 2, 3)),
         'шаги 0/1.5/3, стоп 4.5': dict(steps=(0, 1.5, 3), stop_k=4.5),
         'одна часть (без лестницы), стоп 6, тейк +2': dict(w=(1, 0.0001, 0.0001))}
    L = ['# «Лестница» — результаты', '', 'R: 1R = убыток при полном наборе трёх частей и стопе. После издержек.', '',
         '| вариант | выборка | кампаний | WR | средн. R [95% ДИ] | PF | ΣR |', '|---|---|---|---|---|---|---|']
    res = {}
    for k, v in V.items():
        tr = run(D, F, reg, **v); res[k] = tr
        L.append(f'| {k} | dev ' + stats([x['R'] for x in tr if x['t'] < HOLD]))
        L.append(f'| ↳ | hold ' + stats([x['R'] for x in tr if x['t'] >= HOLD]))
    m = res[list(V)[0]]
    L += ['', '## Основной: по кварталам', '| квартал | кампаний | средн. R | ΣR |', '|---|---|---|---|']
    import collections
    q = collections.defaultdict(list)
    for x in m:
        dtq = np.datetime64(int(x['t']), 'ms').astype('datetime64[M]').astype(str)
        y, mo = dtq.split('-'); q[f'{y}Q{(int(mo)-1)//3+1}'].append(x['R'])
    pos = 0
    for k in sorted(q):
        r = np.array(q[k]); pos += r.sum() > 0
        L.append(f'| {k} | {len(r)} | {r.mean():+.3f} | {r.sum():+.1f} |')
    L.append(f'\nКварталов в плюсе: {pos} из {len(q)}.')
    nf = collections.Counter(x['nf'] for x in m); wy = collections.Counter(x['why'] for x in m)
    L.append(f'Набрано частей: {dict(nf)}; выходы: {dict(wy)}.')
    for r in (0.02, 0.05):
        e1, d1 = equity([x for x in m if x['t'] < HOLD], r); e2, d2 = equity([x for x in m if x['t'] >= HOLD], r)
        L.append(f'Капитал при риске {r*100:.0f}% на кампанию: dev ×{e1:.2f} (просадка {d1*100:.0f}%), holdout ×{e2:.2f} (просадка {d2*100:.0f}%).')
    os.makedirs('ladder_out', exist_ok=True); open('ladder_out/REPORT.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
