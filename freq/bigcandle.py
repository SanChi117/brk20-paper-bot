#!/usr/bin/env python3
"""Продолжение аномальной свечи (перевёрнутый F2). 1h, 54 монеты.
Свеча с диапазоном ≥ 3 ATR, закрытие в крайней трети по своему направлению → вход по направлению на открытии следующего
часа. Стоп — середина свечи (вариант: противоположный край). Выход: закрытие 1h за EMA20 (лимит 72ч) или тейк 2R."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mme'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'p2'))
import label_study as ls  # noqa: E402
import exits as X  # noqa: E402
H, HOLD = ls.H, ls.HOLD
FT, FM, SLP = 0.0005, 0.0002, 0.0003
START = int(np.datetime64('2022-02-01', 'ms').astype('int64'))


def trade(d, i, s, stop, em, mode, maxh=72):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    j0 = i + 1
    if j0 + maxh >= len(c):
        return None
    en = o[j0]; risk = abs(en - stop)
    if risk <= 0 or s * (en - stop) <= 0:
        return None
    tp = en + s * 2 * risk if mode == 'tp2' else None
    for j in range(j0, j0 + maxh):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            px = min(o[j], stop) if s == 1 else max(o[j], stop)
            return (s * (px - en) - (en + px) * (FT + SLP)) / risk, int(t[j0])
        if tp is not None and j > j0 and ((s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp)):
            return (s * (tp - en) - en * (FT + SLP) - tp * FM) / risk, int(t[j0])
        if mode == 'ema' and ((s == 1 and c[j] < em[j]) or (s == -1 and c[j] > em[j])):
            return (s * (c[j] - en) - (en + c[j]) * (FT + SLP)) / risk, int(t[j0])
    px = c[j0 + maxh - 1]
    return (s * (px - en) - (en + px) * (FT + SLP)) / risk, int(t[j0])


def stats(R):
    if len(R) == 0:
        return '| 0 | – | – | – | – |'
    R = np.array(R); g = np.random.default_rng(1); bm = np.sort([g.choice(R, len(R)).mean() for _ in range(1000)])
    w = R[R > 0].sum(); lo = -R[R < 0].sum()
    return f'| {len(R)} | {(R>0).mean()*100:.0f}% | {R.mean():+.3f} [{bm[25]:+.3f}; {bm[975]:+.3f}] | {w/lo if lo else 0:.2f} | {R.sum():+.0f} |'


def main():
    d1, d2 = sys.argv[1], sys.argv[2]
    ls.DATA = d1; reg = ls.btc_regime(ls.load('BTCUSDT'))
    V = {('mid', 'ema', 'any'): 'стоп середина свечи, выход EMA20, любой режим',
         ('mid', 'ema', 'with'): 'то же, только по режиму BTC',
         ('mid', 'tp2', 'any'): 'стоп середина, тейк 2R',
         ('ext', 'ema', 'any'): 'стоп за свечой, выход EMA20',
         ('mid', 'ema', 'against'): 'КАК В F2 (против режима BTC), но по направлению свечи'}
    T = {k: [] for k in V}
    for dd in (d1, d2):
        ls.DATA = dd
        for sname in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(sname)
            if d is None or len(d['c']) < 2000:
                continue
            f = ls.features(d); em = X.ema(d['c'], 20); o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
            last = -99
            for i in range(1500, len(c) - 75):
                a = f['atr'][i]
                if t[i] < START or not a > 0 or i - last < 8:
                    continue
                rng = h[i] - l[i]
                if rng < 3 * a:
                    continue
                cp = (c[i] - l[i]) / rng
                s = 1 if (c[i] > o[i] and cp >= 2 / 3) else (-1 if (c[i] < o[i] and cp <= 1 / 3) else 0)
                if not s:
                    continue
                last = i
                rg = reg.get(int((t[i] + H) // (24 * H)), 'na')
                aligned = (rg == 'up' and s == 1) or (rg == 'down' and s == -1)
                against = (rg == 'up' and s == -1) or (rg == 'down' and s == 1)
                for (sm, mode, rf) in V:
                    if (rf == 'with' and not aligned) or (rf == 'against' and not against):
                        continue
                    stop = (h[i] + l[i]) / 2 if sm == 'mid' else (l[i] - 0.2 * a if s == 1 else h[i] + 0.2 * a)
                    r = trade(d, i, s, stop, em, mode)
                    if r:
                        T[(sm, mode, rf)].append(dict(R=r[0], t=r[1], dev=(dd == d1)))
    L = ['# Продолжение аномальной свечи (перевёрнутый F2) — результаты', '',
         '| вариант | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR |', '|---|---|---|---|---|---|---|']
    for k, name in V.items():
        tr = T[k]
        for lab, sub in (('24 монеты, до июня 2026', [x for x in tr if x['dev'] and x['t'] < HOLD]),
                         ('30 новых, до июня 2026', [x for x in tr if not x['dev'] and x['t'] < HOLD]),
                         ('54, июнь–окт 2026', [x for x in tr if x['t'] >= HOLD])):
            L.append(f'| {name} | {lab} ' + stats([x['R'] for x in sub]))
    os.makedirs('freq_out', exist_ok=True); open('freq_out/BIGCANDLE.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
