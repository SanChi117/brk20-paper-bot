#!/usr/bin/env python3
"""P2: ловушка слабого пробоя (A) и быстрый тейк сильного пробоя (B). См. p2/PREREG.md."""
import datetime as dt, glob, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mme'))
import label_study as ls  # noqa: E402

H, HOLD = ls.H, ls.HOLD
FT, FM, SL_ = 0.0005, 0.0002, 0.0003
START = int(np.datetime64('2022-02-01', 'ms').astype('int64'))


def sim(d, k, s, stop, tpR, maxh=24):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    j0 = k + 1
    if j0 + maxh >= len(c):
        return None
    e = o[j0]; risk = abs(e - stop)
    if risk <= 0 or (s == 1 and stop >= e) or (s == -1 and stop <= e):
        return None
    tp = e + s * tpR * risk
    for j in range(j0, j0 + maxh):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            px = min(o[j], stop) if s == 1 else max(o[j], stop)
            return (s * (px - e) - e * (FT + SL_) - px * (FT + SL_)) / risk, int(t[j0])
        if (s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp):
            return (s * (tp - e) - e * (FT + SL_) - tp * FM) / risk, int(t[j0])
    px = c[j0 + maxh - 1]
    return (s * (px - e) - e * (FT + SL_) - px * (FT + SL_)) / risk, int(t[j0])


def events(d, f, reg, need_comp):
    t, c, o, hh, ll = d['t'], d['c'], d['o'], d['h'], d['l']
    out = []; last = -10**9
    for i in range(1500, len(t) - 30):
        if t[i] < START:
            continue
        if i - last < 24:
            continue
        if need_comp and not (f['bpct'][i - 1] == f['bpct'][i - 1] and f['bpct'][i - 1] < 0.30):
            continue
        up = c[i] > f['hi36'][i]; dn = c[i] < f['lo36'][i]
        if not (up or dn):
            continue
        last = i; s = 1 if up else -1
        lvl = f['hi36'][i] if up else f['lo36'][i]
        a = f['atr'][i]; rng = f['rng'][i] or 1e-12; z = f['zoi'][i]; tk = f['tak1'][i]
        body = abs(c[i] - o[i]) / rng
        cpos = (c[i] - ll[i]) / rng if up else (hh[i] - c[i]) / rng
        wick = (hh[i] - max(o[i], c[i])) / rng if up else (min(o[i], c[i]) - ll[i]) / rng
        sc = sum(bool(x) for x in [s * (c[i] - lvl) >= 0.2 * a, wick < 0.25, body >= 0.5 and cpos >= 0.7,
                                   (f['rvol'][i] or 0) >= 1.5, (tk >= 0.55) if up else (tk <= 0.45),
                                   (not np.isnan(z)) and z > 0.5])
        rg = reg.get(int((t[i] + H) // (24 * H)), 'na')
        fail = None
        for j in (i + 1, i + 2):
            if s * (c[j] - lvl) < 0:
                fail = j; break
        out.append(dict(i=i, s=s, lvl=lvl, a=a, sc=sc, rg=rg, fail=fail))
    return out


def stats(R):
    if len(R) == 0:
        return '| 0 | – | – | – | – | – | – |'
    R = np.array(R); g = np.random.default_rng(1); bm = np.sort([g.choice(R, len(R)).mean() for _ in range(1000)])
    w = R[R > 0].sum(); ls_ = -R[R < 0].sum()
    return (f'| {len(R)} | {(R>0).mean()*100:.0f}% | {R.mean():+.3f} [{bm[25]:+.3f}; {bm[975]:+.3f}] | '
            f'{w/ls_ if ls_ else 0:.2f} | {R.sum():+.0f} | {R.sum()-np.sort(R)[::-1][:5].sum():+.0f} |')


def main():
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{ls.DATA}/*_1h.csv'))
    D = {s: ls.load(s) for s in syms}; reg = ls.btc_regime(D['BTCUSDT'])
    F = {s: ls.features(D[s]) for s in syms if len(D[s]['c']) >= 2000}
    EV = {comp: {s: events(D[s], F[s], reg, comp) for s in F} for comp in (True, False)}
    V = {}
    def add(name, comp, pick, mk):
        tr = []
        for s in F:
            for e in EV[comp][s]:
                if not pick(e):
                    continue
                r = mk(D[s], e)
                if r:
                    tr.append(r)
        V[name] = tr
    def fade(tpR):
        def mk(d, e):
            k = e['fail']; s = -e['s']
            seg = d['h'][e['i']:k + 1] if e['s'] == 1 else d['l'][e['i']:k + 1]
            ext = seg.max() if e['s'] == 1 else seg.min()
            return sim(d, k, s, ext + e['s'] * 0.2 * e['a'], tpR)
        return mk
    def strong(tpR):
        return lambda d, e: sim(d, e['i'], e['s'], e['lvl'] - e['s'] * 0.5 * e['a'], tpR)
    add('A ловушка, оценка 0–2, 1.5R (основной)', True, lambda e: e['sc'] <= 2 and e['fail'], fade(1.5))
    add('A, тейк 1R', True, lambda e: e['sc'] <= 2 and e['fail'], fade(1.0))
    add('A, тейк 2R', True, lambda e: e['sc'] <= 2 and e['fail'], fade(2.0))
    add('A, любая оценка', True, lambda e: bool(e['fail']), fade(1.5))
    add('A, без сжатия', False, lambda e: e['sc'] <= 2 and e['fail'], fade(1.5))
    add('A, против режима BTC', True, lambda e: e['sc'] <= 2 and e['fail'] and ((e['rg'] == 'up' and e['s'] == -1) or (e['rg'] == 'down' and e['s'] == 1)), fade(1.5))
    add('B сильный, оценка 5–6, 1.5R (основной)', True, lambda e: e['sc'] >= 5, strong(1.5))
    add('B, тейк 1R', True, lambda e: e['sc'] >= 5, strong(1.0))
    add('B, тейк 2R', True, lambda e: e['sc'] >= 5, strong(2.0))
    add('B, без сжатия', False, lambda e: e['sc'] >= 5, strong(1.5))
    add('B, по режиму BTC', True, lambda e: e['sc'] >= 5 and ((e['rg'] == 'up' and e['s'] == 1) or (e['rg'] == 'down' and e['s'] == -1)), strong(1.5))
    add('B, оценка ≥4', True, lambda e: e['sc'] >= 4, strong(1.5))
    L = ['# P2 — монетизация оценки пробоя', '', 'R после издержек; [95% ДИ среднего].', '',
         '| вариант | выборка | сделок | WR | средн. R [ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    for k, tr in V.items():
        L.append(f'| {k} | dev ' + stats([r for r, te in tr if te < HOLD]))
        L.append(f'| {k} | hold ' + stats([r for r, te in tr if te >= HOLD]))
    L += ['', '## По годам (основные)', '| вариант | год | сделок | WR | средн. R [ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    for k in [x for x in V if 'основной' in x]:
        for y in range(2022, 2027):
            a = int(np.datetime64(f'{y}-01-01', 'ms').astype('int64')); b = int(np.datetime64(f'{y+1}-01-01', 'ms').astype('int64'))
            L.append(f'| {k[:1]} | {y} ' + stats([r for r, te in V[k] if a <= te < b]))
    os.makedirs('p2_out', exist_ok=True); open('p2_out/REPORT.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
