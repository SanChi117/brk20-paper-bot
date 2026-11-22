#!/usr/bin/env python3
"""Проверка меток «Механизм рынка»: цена × OI и качество пробоя. См. mme/PREREG.md."""
import csv, math, os, random, sys
import numpy as np

H = 3600000
HOLD = int(np.datetime64('2026-06-01T00:00:00', 'ms').astype('int64'))
DATA = sys.argv[1] if len(sys.argv) > 1 else 'mme_data'


def load(sym):
    p = f'{DATA}/{sym}_1h.csv'
    if not os.path.exists(p):
        return None
    t, o, h, l, c, qv, tb, oi = [], [], [], [], [], [], [], []
    with open(p) as f:
        for r in csv.DictReader(f):
            t.append(int(r['t'])); o.append(float(r['o'])); h.append(float(r['h'])); l.append(float(r['l'])); c.append(float(r['c']))
            qv.append(float(r['qv'])); tb.append(float(r['tbq'])); oi.append(float(r['oi']) if r['oi'] else np.nan)
    d = {k: np.array(v, dtype=float) for k, v in dict(o=o, h=h, l=l, c=c, qv=qv, tb=tb, oi=oi).items()}
    d['t'] = np.array(t, dtype=np.int64)
    fund = []
    with open(f'{DATA}/{sym}_funding.csv') as f:
        for r in csv.DictReader(f):
            fund.append((int(r['t']), float(r['rate'])))
    d['fund'] = fund
    return d


def rolling_mean_std_past(x, win, minn):
    """mean/std по прошлым значениям (без текущего), NaN-устойчиво."""
    n = len(x); m = np.full(n, np.nan); s = np.full(n, np.nan)
    v = np.where(np.isnan(x), 0.0, x); k = (~np.isnan(x)).astype(float)
    cs, cs2, ck = np.concatenate([[0], np.cumsum(v)]), np.concatenate([[0], np.cumsum(v * v)]), np.concatenate([[0], np.cumsum(k)])
    for i in range(n):
        a = max(0, i - win)
        cnt = ck[i] - ck[a]
        if cnt >= minn:
            mu = (cs[i] - cs[a]) / cnt; var = (cs2[i] - cs2[a]) / cnt - mu * mu
            m[i] = mu; s[i] = math.sqrt(var) if var > 0 else np.nan
    return m, s


def features(d):
    c, h, l, o, qv, tb, oi, t = d['c'], d['h'], d['l'], d['o'], d['qv'], d['tb'], d['oi'], d['t']
    n = len(c)
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1)))); tr[0] = h[0] - l[0]
    atr = np.full(n, np.nan); a = tr[:14].mean()
    for i in range(14, n):
        a = a * 13 / 14 + tr[i] / 14; atr[i] = a
    gap4 = np.r_[np.full(4, False), (t[4:] - t[:-4]) == 4 * H]
    dp4 = np.full(n, np.nan); dp4[4:] = (c[4:] - c[:-4]) / atr[4:]; dp4[~gap4] = np.nan
    oi = np.where(oi > 0, oi, np.nan)
    doi4 = np.full(n, np.nan); doi4[4:] = oi[4:] / oi[:-4] - 1; doi4[~gap4] = np.nan
    doi4[~np.isfinite(doi4) | (np.abs(doi4) > 0.5)] = np.nan  # сбойные снимки OI
    mu, sd = rolling_mean_std_past(doi4, 720, 300)
    zoi = doi4 / sd
    qv4 = np.convolve(qv, np.ones(4), 'full')[:n]; tb4 = np.convolve(tb, np.ones(4), 'full')[:n]
    tak4 = np.where(qv4 > 0, tb4 / qv4, np.nan)
    tak1 = np.where(qv > 0, tb / qv, np.nan)
    # RVOL: объём против того же часа за прошлые 20 дней
    rvol = np.full(n, np.nan)
    for i in range(480, n):
        past = qv[i - 480:i:24]
        if len(past) == 20 and past.mean() > 0:
            rvol[i] = qv[i] / past.mean()
    # funding z по последней выплате до закрытия часа
    ft = np.array([x[0] for x in d['fund']], dtype=np.int64); fv = np.array([x[1] for x in d['fund']])
    fz = np.full(n, np.nan)
    if len(ft) > 300:
        fmu, fsd = rolling_mean_std_past(fv, 270, 90)
        fzz = (fv - fmu) / fsd
        idx = np.searchsorted(ft, t + H, side='right') - 1
        ok = idx >= 0
        fz[ok] = fzz[idx[ok]]
    # Боллинджер ширина и перцентиль за 60 дней
    bbw = np.full(n, np.nan)
    for i in range(19, n):
        w = c[i - 19:i + 1]; bbw[i] = 4 * w.std() / w.mean()
    bpct = np.full(n, np.nan)
    for i in range(1460, n):
        win = bbw[i - 1440:i]
        bpct[i] = (win < bbw[i]).mean()
    hi36 = np.full(n, np.nan); lo36 = np.full(n, np.nan)
    for i in range(36, n):
        hi36[i] = h[i - 36:i].max(); lo36[i] = l[i - 36:i].min()
    rng = h - l
    return dict(atr=atr, dp4=dp4, zoi=zoi, tak4=tak4, tak1=tak1, rvol=rvol, fz=fz, bpct=bpct, hi36=hi36, lo36=lo36, rng=rng)


def outcomes(d, f, i, direction):
    c, h, l, atr = d['c'], d['h'], d['l'], f['atr']
    n = len(c)
    if i + 72 >= n or not atr[i] > 0:
        return None
    r = {k: direction * (c[i + k] - c[i]) / atr[i] for k in (4, 24, 72)}
    up, dn = c[i] + direction * 1.5 * atr[i], c[i] - direction * 1.0 * atr[i]
    res = 0.0
    for j in range(i + 1, i + 73):
        hit_t = h[j] >= up if direction > 0 else l[j] <= up
        hit_s = l[j] <= dn if direction > 0 else h[j] >= dn
        if hit_s:
            res = -1.0; break
        if hit_t:
            res = 1.0; break
    r['path'] = res
    return r


def btc_regime(btc):
    D = 24 * H; days = {}
    for t, c in zip(btc['t'], btc['c']):
        days[int(t // D)] = c
    ks = sorted(days); cl = np.array([days[k] for k in ks]); out = {}
    for i in range(220, len(ks)):
        s200 = cl[i - 200:i].mean(); s50 = cl[i - 50:i].mean(); s50p = cl[i - 70:i - 20].mean()
        last = cl[i - 1]
        out[ks[i]] = 'up' if last > s200 and s50 > s50p else ('down' if last < s200 and s50 < s50p else 'range')
    return out


def summ(ev):
    if not ev:
        return None
    a24 = np.array([e['r'][24] for e in ev]); p = np.array([e['r']['path'] for e in ev])
    rnd = random.Random(1); bs = []
    for _ in range(1000):
        s = [a24[rnd.randrange(len(a24))] for _ in a24]; bs.append(np.mean(s))
    bs.sort()
    return dict(n=len(ev), r4=np.mean([e['r'][4] for e in ev]), r24=a24.mean(), r72=np.mean([e['r'][72] for e in ev]),
                ci=(bs[25], bs[975]), hit24=(a24 > 0).mean(), win=(p == 1).mean(), loss=(p == -1).mean())


def fmt(name, s):
    if not s:
        return f'| {name} | 0 | | | | | | |'
    return (f"| {name} | {s['n']} | {s['r4']:+.2f} | {s['r24']:+.2f} [{s['ci'][0]:+.2f}; {s['ci'][1]:+.2f}] | {s['r72']:+.2f} | "
            f"{100*s['hit24']:.0f}% | {100*s['win']:.0f}% / {100*s['loss']:.0f}% |")


HDR = ['| Метка | Событий | 4 ч | 24 ч [95% ДИ] | 72 ч | Доля в плюс 24 ч | +1,5 ATR раньше −1 ATR / наоборот |', '|---|---|---|---|---|---|---|']


def main():
    syms = sorted({fn.split('_')[0] for fn in os.listdir(DATA) if fn.endswith('_1h.csv')})
    D = {s: load(s) for s in syms}
    reg = btc_regime(D['BTCUSDT'])
    E = {}  # (period, label) -> list
    atrpct = []

    def add(per, lab, i, d, f, direction, extra=None):
        r = outcomes(d, f, i, direction)
        if r is None:
            return
        E.setdefault((per, lab), []).append(dict(r=r, **(extra or {})))

    for s in syms:
        d = D[s]
        if d is None or len(d['c']) < 2000:
            continue
        f = features(d)
        t = d['t']; n = len(t)
        atrpct.extend(list((f['atr'] / d['c'])[2000::50]))
        lastA = {}; lastB = -10**9
        for i in range(1500, n - 73):
            per = 'dev' if t[i] < HOLD else 'hold'
            if t[i] < int(np.datetime64('2022-02-01', 'ms').astype('int64')):
                continue
            rg = reg.get(int((t[i] + H) // (24 * H)), 'na')
            dp, z = f['dp4'][i], f['zoi'][i]
            # A. цена × OI
            if not np.isnan(dp) and abs(dp) >= 1.5:
                dirn = 1 if dp > 0 else -1
                key = 'up' if dirn > 0 else 'dn'
                if i - lastA.get(key, -10**9) >= 24:
                    lastA[key] = i
                    add(per, f'CTRL_{key}', i, d, f, dirn)
                    if not np.isnan(z) and abs(z) >= 1.0:
                        q = {(1, 1): 'Q1', (1, -1): 'Q2', (-1, 1): 'Q3', (-1, -1): 'Q4'}[(dirn, 1 if z > 0 else -1)]
                        add(per, q, i, d, f, dirn)
                        tk = f['tak4'][i]
                        if not np.isnan(tk):
                            conf = (tk >= 0.52) if dirn > 0 else (tk <= 0.48)
                            add(per, f'{q}_taker_{"да" if conf else "нет"}', i, d, f, dirn)
                        fz = f['fz'][i]
                        if not np.isnan(fz):
                            crowd = (fz > 2) if dirn > 0 else (fz < -2)
                            add(per, f'{q}_funding_{"перегрет" if crowd else "норма"}', i, d, f, dirn)
                        add(per, f'{q}_rezhim_{rg}', i, d, f, dirn)
            # B. пробой после сжатия
            if i - lastB >= 24 and not np.isnan(f['bpct'][i - 1]) and f['bpct'][i - 1] < 0.30:
                c = d['c'][i]; up = c > f['hi36'][i]; dn = c < f['lo36'][i]
                if up or dn:
                    lastB = i
                    dirn = 1 if up else -1
                    lvl = f['hi36'][i] if up else f['lo36'][i]
                    a = f['atr'][i]; rng = f['rng'][i] or 1e-12
                    o, hh, ll = d['o'][i], d['h'][i], d['l'][i]
                    body = abs(c - o) / rng
                    cpos = (c - ll) / rng if up else (hh - c) / rng
                    wick = (hh - max(o, c)) / rng if up else (min(o, c) - ll) / rng
                    tk = f['tak1'][i]
                    signs = [dirn * (c - lvl) >= 0.2 * a, wick < 0.25, body >= 0.5 and cpos >= 0.7,
                             (f['rvol'][i] or 0) >= 1.5, (tk >= 0.55) if up else (tk <= 0.45),
                             (not np.isnan(z)) and z > 0.5]
                    sc = sum(bool(x) for x in signs)
                    fb = any(dirn * (d['c'][j] - lvl) < 0 for j in (i + 1, i + 2))
                    ex = dict(fb=fb)
                    add(per, 'BRK_все', i, d, f, dirn, ex)
                    add(per, f'BRK_оценка_{"0-2" if sc <= 2 else ("3-4" if sc <= 4 else "5-6")}', i, d, f, dirn, ex)
                    if not np.isnan(z):
                        add(per, f'BRK_OI_{"растёт" if z > 0.5 else ("падает" if z < -0.5 else "ровно")}', i, d, f, dirn, ex)
    out = ['# Результат проверки меток «Механизм рынка»', '',
           f'Монет: {len(syms)}. Медианный ATR(1h) = {100*np.nanmedian(atrpct):.2f}% цены; комиссия+проскальзывание туда-обратно 0,2% ≈ {0.2/np.nanmedian(atrpct)/100:.2f} ATR.',
           'Доходность — в ATR в сторону движения (для Q4 «+» = продолжение падения, отскок = «−»).', '']
    for per, title in (('dev', 'Разработка 2022-02 … 2026-05'), ('hold', 'Отложенная проверка 2026-06 … 2026-10')):
        out += [f'## {title}', '', '### A. Цена × OI', ''] + HDR
        for lab in ['CTRL_up', 'Q1', 'Q2', 'CTRL_dn', 'Q3', 'Q4']:
            out.append(fmt(lab, summ(E.get((per, lab), []))))
        out += ['', 'Разрезы:', ''] + HDR
        for lab in sorted(k[1] for k in E if k[0] == per and '_' in k[1] and k[1][:2] in ('Q1', 'Q2', 'Q3', 'Q4')):
            out.append(fmt(lab, summ(E[(per, lab)])))
        out += ['', '### B. Пробой после сжатия', '', '| Метка | Событий | Ложный пробой (2 ч) | 24 ч [95% ДИ] | +1,5 ATR раньше −1 ATR |', '|---|---|---|---|---|']
        for lab in ['BRK_все', 'BRK_оценка_0-2', 'BRK_оценка_3-4', 'BRK_оценка_5-6', 'BRK_OI_растёт', 'BRK_OI_ровно', 'BRK_OI_падает']:
            ev = E.get((per, lab), []); s = summ(ev)
            if s:
                out.append(f"| {lab} | {s['n']} | {100*np.mean([e['fb'] for e in ev]):.0f}% | {s['r24']:+.2f} [{s['ci'][0]:+.2f}; {s['ci'][1]:+.2f}] | {100*s['win']:.0f}% |")
        out.append('')
    rep = '\n'.join(out)
    os.makedirs('mme_out', exist_ok=True)
    open('mme_out/REPORT.md', 'w').write(rep + '\n')
    print(rep)


if __name__ == '__main__':
    main()
