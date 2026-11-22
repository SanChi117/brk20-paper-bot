#!/usr/bin/env python3
"""Схема сопровождения как у канала + механические входы. См. xt/PREREG.md."""
import os, random, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mme'))
import label_study as ls  # noqa: E402

H = ls.H; HOLD = ls.HOLD
SL, TP = 0.055, (0.01, 0.025, 0.067); PART = (0.5, 0.25, 0.25)
FT, FM, SLIP = 0.0005, 0.0002, 0.0003


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0]); up = np.where(d > 0, d, 0); dn = np.where(d < 0, -d, 0)
    au = np.zeros_like(c); ad = np.zeros_like(c); au[n] = up[1:n+1].mean(); ad[n] = dn[1:n+1].mean()
    for i in range(n + 1, len(c)):
        au[i] = (au[i-1] * (n-1) + up[i]) / n; ad[i] = (ad[i-1] * (n-1) + dn[i]) / n
    r = 100 - 100 / (1 + au / np.where(ad > 0, ad, 1e-12)); r[:n+1] = 50
    return r


def sma(x, n):
    cs = np.cumsum(np.r_[0, x]); out = np.full(len(x), np.nan); out[n-1:] = (cs[n:] - cs[:-n]) / n
    return out


def trade(d, i, s):
    """вход на открытии бара i+1; возвращает (доход в долях номинала, t_entry, t_exit)"""
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    n = len(c); j0 = i + 1
    if j0 + 72 >= n:
        return None
    e = o[j0]; stop = e * (1 - s * SL); tps = [e * (1 + s * x) for x in TP]
    rem = 1.0; pnl = -FT; stage = 0; be = e * (1 + s * (FT + FM))
    for j in range(j0, j0 + 72):
        hi, lo = h[j], l[j]
        adverse = lo if s == 1 else hi
        fav = hi if s == 1 else lo
        cur_stop = stop if stage == 0 else be
        if (s == 1 and adverse <= cur_stop) or (s == -1 and adverse >= cur_stop):
            px = cur_stop if j > j0 else (min(o[j], cur_stop) if s == 1 else max(o[j], cur_stop))
            pnl += rem * (s * (px - e) / e - FT - SLIP)
            return pnl, int(t[j0]), int(t[j] + H), stage, stage == 0
        for k in range(stage, 3):
            if (s == 1 and fav >= tps[k]) or (s == -1 and fav <= tps[k]):
                pnl += PART[k] * (TP[k] - FM); rem -= PART[k]; stage = k + 1
            else:
                break
        if stage == 3:
            return pnl, int(t[j0]), int(t[j] + H), 3, False
        if stage >= 1 and ((s == 1 and lo <= be) or (s == -1 and hi >= be)):
            # консервативно: в баре, где задет вход после TP1, остаток уходит в BE
            pnl += rem * (s * (be - e) / e - FT - SLIP)
            return pnl, int(t[j0]), int(t[j] + H), stage, False
    px = c[j0 + 71]
    pnl += rem * (s * (px - e) / e - FT - SLIP)
    return pnl, int(t[j0]), int(t[j0 + 71] + H), stage, False


def funding(d, s, te, tx):
    return -s * sum(r for t, r in d['fund'] if te <= t < tx)


def main():
    import glob
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{ls.DATA}/*_1h.csv'))
    D = {s: ls.load(s) for s in syms}
    F = {s: ls.features(D[s]) for s in syms}
    reg = ls.btc_regime(D['BTCUSDT'])
    bt = D['BTCUSDT']; bsma = sma(bt['c'], 200); bmap = {int(t): int(c > m) - int(c < m) if m == m else 0 for t, c, m in zip(bt['t'], bt['c'], bsma)}
    # 24ч доходности по всем монетам для R4
    r24 = {}
    for s in syms:
        c = D[s]['c']; t = D[s]['t']
        for i in range(24, len(c)):
            r24.setdefault(int(t[i]), []).append((c[i] / c[i-24] - 1, s))
    rank = {}
    for t, lst in r24.items():
        if len(lst) >= 12:
            lst.sort(); rank[t] = ({x[1] for x in lst[-3:]}, {x[1] for x in lst[:3]})
    rnd = random.Random(7)
    sig = {k: [] for k in ('R0', 'R1', 'R2', 'R3', 'R4')}
    for s in syms:
        d, f = D[s], F[s]; c, t, h, l = d['c'], d['t'], d['h'], d['l']
        rs = rsi(c); m200 = sma(c, 200)
        n = len(c)
        for i in range(250, n - 74):
            T = int(t[i]); rg = reg.get(int((T + H) // (24 * H)))
            if rnd.random() < 0.02:
                sig['R0'].append((s, i, rnd.choice((1, -1))))
            bb = bmap.get(T, 0)
            if bb == 1 and c[i] > m200[i] and rs[i] < 30:
                sig['R1'].append((s, i, 1))
            elif bb == -1 and c[i] < m200[i] and rs[i] > 70:
                sig['R1'].append((s, i, -1))
            dp, z = f['dp4'][i], f['zoi'][i]
            if dp == dp and z == z:
                if rg == 'up' and dp < -1.5 and z < -1.5:
                    sig['R2'].append((s, i, 1))
                elif rg == 'down' and dp > 1.5 and z < -1.5:
                    sig['R2'].append((s, i, -1))
            a = f['atr'][i]
            if a == a and f['hi36'][i] == f['hi36'][i] and rg in ('up', 'down'):
                sd = 1 if rg == 'up' else -1
                brk = c[i] > f['hi36'][i] if sd == 1 else c[i] < f['lo36'][i]
                if brk:
                    rngv = h[i] - l[i]
                    if rngv > 0:
                        body = abs(c[i] - d['o'][i]) / rngv
                        cpos = (c[i] - l[i]) / rngv if sd == 1 else (h[i] - c[i]) / rngv
                        wick = ((h[i] - max(c[i], d['o'][i])) if sd == 1 else (min(c[i], d['o'][i]) - l[i])) / rngv
                        tk = f['tak1'][i]; rv = f['rvol'][i]
                        sc = (body >= 0.6) + (cpos >= 0.8) + (wick <= 0.2) + (rv == rv and rv >= 1.5) + \
                             (tk == tk and ((tk >= 0.55) if sd == 1 else (tk <= 0.45))) + (z == z and z > 0.5)
                        if sc >= 5:
                            sig['R3'].append((s, i, sd))
            rk = rank.get(T)
            if rk:
                if rg == 'up' and s in rk[0]:
                    sig['R4'].append((s, i, 1))
                elif rg == 'down' and s in rk[1]:
                    sig['R4'].append((s, i, -1))
    L = ['# Схема «как у канала» + механические входы', '',
         'Доход сделки — % от номинала позиции после издержек и фандинга. WR — доля сделок в плюсе. TP1% — доля дошедших до TP1.', '',
         '| вход | выборка | сделок | WR | TP1% | стопов | средн. % | 95% ДИ | ×кап. риск 2% | DD | ×кап. риск 13% | DD |',
         '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for k, lst in sig.items():
        lst.sort(key=lambda x: D[x[0]]['t'][x[1]])
        busy = {}; open_ = []; tr = []
        for s, i, sd in lst:
            T = int(D[s]['t'][i])
            if busy.get(s, 0) > T:
                continue
            open_ = [x for x in open_ if x > T]
            if len(open_) >= 3:
                continue
            r = trade(D[s], i, sd)
            if not r:
                continue
            pnl, te, tx, stg, stopped = r; pnl += funding(D[s], sd, te, tx)
            tr.append(dict(p=pnl, te=te, tx=tx, tp1=stg >= 1, stop=stopped))
            busy[s] = max(tx, T + 24 * H); open_.append(tx)
        for lab, sub in (('dev', [x for x in tr if x['te'] < HOLD]), ('hold', [x for x in tr if x['te'] >= HOLD])):
            if not sub:
                L.append(f'| {k} | {lab} | 0 |'); continue
            p = np.array([x['p'] for x in sub])
            g = np.random.default_rng(1); bm = np.sort([g.choice(p, len(p)).mean() for _ in range(1000)])
            eqs = []
            for risk in (0.02, 0.13):
                eq, pk, dd = 1.0, 1.0, 0.0
                for x in sorted(sub, key=lambda x: x['tx']):
                    eq *= max(1 + x['p'] * risk / SL, 0.0); pk = max(pk, eq); dd = max(dd, 1 - eq / pk if pk else 1)
                eqs += [eq, dd]
            L.append(f"| {k} | {lab} | {len(p)} | {(p>0).mean()*100:.0f}% | {np.mean([x['tp1'] for x in sub])*100:.0f}% | "
                     f"{np.mean([x['stop'] for x in sub])*100:.0f}% | {p.mean()*100:+.2f}% | [{bm[25]*100:+.2f}; {bm[975]*100:+.2f}] | "
                     f"×{eqs[0]:.2f} | {eqs[1]*100:.0f}% | ×{eqs[2]:.2f} | {eqs[3]*100:.0f}% |")
    L += ['', 'Риск 13% ≈ как у автора (маржа $250 × 25x, стоп 5.5% при счёте ~$2 650).']
    os.makedirs('xt_out', exist_ok=True); open('xt_out/REPORT.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
