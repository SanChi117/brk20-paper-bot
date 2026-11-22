#!/usr/bin/env python3
"""«Немного, но часто»: F1 отскок после выноса, F2 откат перегретой свечи, F3 растяжение от VWAP. См. freq/PREREG.md."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mme'))
import label_study as ls  # noqa: E402
H, HOLD = ls.H, ls.HOLD
FT, FM, SLP = 0.0005, 0.0002, 0.0003
START = int(np.datetime64('2022-02-01', 'ms').astype('int64'))


def trade(d, i, s, stop, tp, maxh):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    j0 = i + 1
    if j0 + maxh >= len(c):
        return None
    en = o[j0]; risk = abs(en - stop)
    if risk <= 0 or s * (en - stop) <= 0 or s * (tp - en) <= 0:
        return None
    for j in range(j0, j0 + maxh):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            px = min(o[j], stop) if s == 1 else max(o[j], stop)
            return (s * (px - en) - en * (FT + SLP) - px * (FT + SLP)) / risk, int(t[j0]), risk / en
        if (s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp):
            return (s * (tp - en) - en * (FT + SLP) - tp * FM) / risk, int(t[j0]), risk / en
    px = c[j0 + maxh - 1]
    return (s * (px - en) - en * (FT + SLP) - px * (FT + SLP)) / risk, int(t[j0]), risk / en


def signals(d, f, reg):
    o, h, l, c, t, qv = d['o'], d['h'], d['l'], d['c'], d['t'], d['qv']
    a = f['atr']; n = len(c); out = {'F1': [], 'F2': [], 'F3': []}
    day = t // (24 * H); vw = np.full(n, np.nan); num = den = 0.0; cur = None
    for i in range(n):
        if day[i] != cur:
            cur = day[i]; num = den = 0.0
        tp_ = (h[i] + l[i] + c[i]) / 3; num += tp_ * qv[i]; den += qv[i]
        vw[i] = num / den if den > 0 else np.nan
    last = {'F1': -99, 'F2': -99, 'F3': -99}
    for i in range(1500, n - 15):
        if t[i] < START or not a[i] > 0:
            continue
        rg = reg.get(int((t[i] + H) // (24 * H)), 'na')
        dp, z = f['dp4'][i], f['zoi'][i]
        if i - last['F1'] >= 12 and dp == dp and z == z and z <= -1.5:
            if rg == 'up' and dp <= -2:
                out['F1'].append((i, 1, c[i] - 2 * a[i], c[i] + a[i], 12)); last['F1'] = i
            elif rg == 'down' and dp >= 2:
                out['F1'].append((i, -1, c[i] + 2 * a[i], c[i] - a[i], 12)); last['F1'] = i
        rng = h[i] - l[i]
        if i - last['F2'] >= 8 and rng >= 3 * a[i] and rg in ('up', 'down'):
            cp = (c[i] - l[i]) / rng
            if rg == 'up' and c[i] < o[i] and cp <= 1 / 3:     # большая медвежья свеча против роста → лонг
                out['F2'].append((i, 1, l[i] - 0.2 * a[i], c[i] + a[i], 8)); last['F2'] = i
            elif rg == 'down' and c[i] > o[i] and cp >= 2 / 3:
                out['F2'].append((i, -1, h[i] + 0.2 * a[i], c[i] - a[i], 8)); last['F2'] = i
        if i - last['F3'] >= 8 and vw[i] == vw[i]:
            dist = c[i] - vw[i]
            if abs(dist) >= 3 * a[i]:
                s = -1 if dist > 0 else 1
                out['F3'].append((i, s, c[i] - s * abs(dist), c[i] + s * abs(dist) / 2, 8)); last['F3'] = i
    return out


def stats(R):
    if len(R) == 0:
        return '| 0 | – | – | – | – |'
    R = np.array(R); g = np.random.default_rng(1); bm = np.sort([g.choice(R, len(R)).mean() for _ in range(1000)])
    w = R[R > 0].sum(); lo = -R[R < 0].sum()
    return f'| {len(R)} | {(R>0).mean()*100:.0f}% | {R.mean():+.3f} [{bm[25]:+.3f}; {bm[975]:+.3f}] | {w/lo if lo else 0:.2f} | {R.sum():+.0f} |'


def main():
    d1, d2 = sys.argv[1], sys.argv[2]
    ls.DATA = d1; reg = ls.btc_regime(ls.load('BTCUSDT'))
    T = {k: [] for k in ('F1', 'F2', 'F3')}
    for dd in (d1, d2):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            f = ls.features(d)
            for k, lst in signals(d, f, reg).items():
                for i, sd, stop, tp, mh in lst:
                    r = trade(d, i, sd, stop, tp, mh)
                    if r:
                        T[k].append(dict(R=r[0], t=r[1], sp=r[2], dev=(dd == d1)))
    names = {'F1': 'F1 отскок после выноса', 'F2': 'F2 откат перегретой свечи', 'F3': 'F3 растяжение от VWAP'}
    days = (HOLD - START) / (24 * H)
    L = ['# «Немного, но часто» — результаты', '', '| идея | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR |', '|---|---|---|---|---|---|---|']
    for k, tr in T.items():
        for lab, sub in (('24 монеты, до июня 2026', [x for x in tr if x['dev'] and x['t'] < HOLD]),
                         ('30 новых, до июня 2026', [x for x in tr if not x['dev'] and x['t'] < HOLD]),
                         ('54, июнь–окт 2026', [x for x in tr if x['t'] >= HOLD]),
                         ('↳ только стоп ≤ 3.5% (20x), до июня', [x for x in tr if x['t'] < HOLD and x['sp'] <= 0.035])):
            L.append(f'| {names[k]} | {lab} ' + stats([x['R'] for x in sub]))
        dev = [x for x in tr if x['t'] < HOLD]
        if dev:
            L.append(f'| ↳ | сделок в день по 54 монетам: {len(dev)/days:.1f}; медиана стопа {np.median([x["sp"] for x in dev])*100:.1f}% цены | | | | | |')
    os.makedirs('freq_out', exist_ok=True); open('freq_out/REPORT.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
