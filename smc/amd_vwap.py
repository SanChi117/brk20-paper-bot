#!/usr/bin/env python3
"""AMD по сессиям и откат к VWAP (см. smc/AMD_VWAP_PREREG.md). 15m данные."""
import glob, os, sys, collections
import numpy as np

M15 = 900_000; DAY = 86_400_000
HOLD = 1780272000000  # 2026-06-01 UTC
FT, FM, SLP = 0.0005, 0.0002, 0.0003


def load(p):
    a = np.loadtxt(p, delimiter=',', skiprows=1); a = a[np.argsort(a[:, 0])]
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]


def days_index(t):
    d = t // DAY; keys, start, cnt = np.unique(d, return_index=True, return_counts=True)
    return keys, start, cnt


def close_trade(s, en, ex, risk, entry_fee, why):
    fee = en * entry_fee + ex * (FM if why == 'TP' else FT + SLP)
    return (s * (ex - en) - fee) / risk


def manage(o, h, l, c, j0, jend, s, en, stop, tp, entry_fee, exit_rule=None, aux=None):
    """от бара j0 (бар входа) до jend включительно"""
    risk = abs(en - stop)
    if risk <= 0:
        return None
    for j in range(j0, jend + 1):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            ex = min(o[j], stop) if s == 1 and j > j0 else (max(o[j], stop) if j > j0 else stop)
            return close_trade(s, en, ex, risk, entry_fee, 'SL')
        if j > j0 and tp is not None and ((s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp)):
            return close_trade(s, en, tp, risk, entry_fee, 'TP')
        if exit_rule == 'vwap' and j > j0 and ((s == 1 and c[j] < aux[j]) or (s == -1 and c[j] > aux[j])):
            return close_trade(s, en, c[j], risk, entry_fee, 'X')
    return close_trade(s, en, c[jend], risk, entry_fee, 'T')


def amd(t, o, h, l, c, variant):
    keys, start, cnt = days_index(t); out = []
    dr = np.array([h[s:s + n].max() - l[s:s + n].min() for s, n in zip(start, cnt)])
    dc = np.array([c[s + n - 1] for s, n in zip(start, cnt)])
    for k in range(21, len(keys)):
        s0, n = start[k], cnt[k]
        if n != 96:
            continue
        a, b = s0, s0 + 28          # 00:00–07:00
        AH, AL = h[a:b].max(), l[a:b].min(); W = AH - AL
        if W <= 0 or W > 0.6 * dr[k - 20:k].mean():
            continue
        trend = 1 if dc[k - 1] > dc[k - 21:k - 1].mean() else -1
        swept_lo = swept_hi = False; ext_lo, ext_hi = np.inf, -np.inf
        for j in range(b, s0 + 52):  # 07:00–13:00
            if l[j] < AL:
                swept_lo = True
            if h[j] > AH:
                swept_hi = True
            ext_lo = min(ext_lo, l[j]); ext_hi = max(ext_hi, h[j])
            sd = 0
            if swept_lo and c[j] > AL and l[j:j+1].min() >= -1:
                sd = 1
            elif swept_hi and c[j] < AH:
                sd = -1
            if sd == 0:
                continue
            if variant == 'trend' and sd != trend:
                break
            j0 = j + 1; en = o[j0]
            stop = ext_lo * (1 - 0.0005) if sd == 1 else ext_hi * (1 + 0.0005)
            if (sd == 1 and en <= stop) or (sd == -1 and en >= stop):
                break
            if variant == 'time':
                tp = None
            elif variant == 'ext':
                tp = AH + W if sd == 1 else AL - W
            else:
                tp = AH if sd == 1 else AL
            if tp is not None and ((sd == 1 and tp <= en) or (sd == -1 and tp >= en)):
                break
            jend = s0 + 84 - 1        # до 21:00
            r = manage(o, h, l, c, j0, jend, sd, en, stop, tp, FT + SLP)
            if r is not None:
                out.append((r, int(t[j0])))
            break
    return out


def vwap_strat(t, o, h, l, c, qv, variant):
    keys, start, cnt = days_index(t); out = []
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    atr = np.convolve(tr, np.ones(14) / 14)[:len(tr)]
    for k in range(1, len(keys)):
        s0, n = start[k], cnt[k]
        if n != 96:
            continue
        sl = slice(s0, s0 + 96)
        tp_ = (h[sl] + l[sl] + c[sl]) / 3; w = qv[sl]
        vw = np.cumsum(tp_ * w) / np.maximum(np.cumsum(w), 1e-12)
        V = np.full(len(c), np.nan); V[sl] = vw
        done = False
        for j in range(s0 + 24 + 16, s0 + 72):   # с 06:00+4ч истории до 18:00
            if done:
                break
            prev = slice(j - 16, j)
            up = np.all(c[prev] > V[prev]) and V[j - 1] > V[j - 17]
            dn = np.all(c[prev] < V[prev]) and V[j - 1] < V[j - 17]
            if not (up or dn):
                continue
            sd = 1 if up else -1; lvl = V[j - 1]   # лимит по VWAP, известному на закрытии прошлого бара
            if (sd == 1 and l[j] <= lvl) or (sd == -1 and h[j] >= lvl):
                en = min(o[j], lvl) if sd == 1 else max(o[j], lvl)
                stop = en - sd * 1.5 * atr[j - 1]
                risk = abs(en - stop)
                tp = None if variant in ('time', 'vexit') else en + sd * (1.0 if variant == 'tp1' else 2.0) * risk
                r = manage(o, h, l, c, j, s0 + 95, sd, en, stop, tp, FM, 'vwap' if variant == 'vexit' else None, V)
                if r is not None:
                    out.append((r, int(t[j])))
                done = True
    return out


def stats(R):
    if len(R) == 0:
        return '| 0 | – | – | – | – |'
    R = np.array(R); g = np.random.default_rng(1); bm = np.sort([g.choice(R, len(R)).mean() for _ in range(1000)])
    w = R[R > 0].sum(); lo = -R[R < 0].sum()
    return f'| {len(R)} | {(R>0).mean()*100:.0f}% | {R.mean():+.3f} [{bm[25]:+.3f}; {bm[975]:+.3f}] | {w/lo if lo else 0:.2f} | {R.sum():+.0f} |'


def quarters(tr):
    q = collections.defaultdict(float)
    for r, te in tr:
        m = np.datetime64(te, 'ms').astype('datetime64[M]').astype(str); y, mo = m.split('-')
        q[f'{y}Q{(int(mo)-1)//3+1}'] += r
    return sum(v > 0 for v in q.values()), len(q)


def main():
    files = sorted(glob.glob(f'{sys.argv[1]}/*_15m.csv'))
    data = [load(p) for p in files]
    V = [('AMD: тейк у противоположной границы (основной)', lambda d: amd(*d[:5], 'base')),
         ('AMD: тейк граница + ширина', lambda d: amd(*d[:5], 'ext')),
         ('AMD: выход только по времени (21:00)', lambda d: amd(*d[:5], 'time')),
         ('AMD: только по дневному тренду', lambda d: amd(*d[:5], 'trend')),
         ('VWAP: стоп 1.5 ATR, тейк 2R (основной)', lambda d: vwap_strat(*d, 'base')),
         ('VWAP: тейк 1R', lambda d: vwap_strat(*d, 'tp1')),
         ('VWAP: выход по закрытию за VWAP', lambda d: vwap_strat(*d, 'vexit')),
         ('VWAP: выход только по времени', lambda d: vwap_strat(*d, 'time'))]
    L = ['# AMD по сессиям и откат к VWAP — результаты', '', 'R после издержек; [95% ДИ среднего].', '',
         '| вариант | выборка | сделок | WR | средн. R [ДИ] | PF | ΣR | кварталов в плюсе |', '|---|---|---|---|---|---|---|---|']
    for name, fn in V:
        tr = [x for d in data for x in fn(d)]
        dev = [x for x in tr if x[1] < HOLD]; ho = [x for x in tr if x[1] >= HOLD]
        p, n = quarters(dev)
        L.append(f'| {name} | dev ' + stats([r for r, _ in dev]) + f' {p} из {n} |')
        L.append(f'| ↳ | hold ' + stats([r for r, _ in ho]) + ' – |')
        print(name, len(tr), flush=True)
    os.makedirs('smc_out', exist_ok=True); open('smc_out/AMD_VWAP.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
