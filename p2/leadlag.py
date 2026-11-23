#!/usr/bin/env python3
"""Альты догоняют BTC (54 монеты, 1h). См. p2/PREREG.md, раздел «Догонялки за BTC».
На закрытии часа i: BTC за час сдвинулся на ≥ K·ATR(BTC). Ожидаемое движение альта = β·движение BTC
(β — регрессия часовых доходностей альта на BTC за прошлые 30 дней, без текущего часа). Если альт отстал на ≥ G·ATR(альта),
входим в альт по направлению BTC на открытии i+1. Стоп SB·ATR, тейк = величина отставания (если задан), выход по времени N ч.
Издержки 0.08% на сторону. Пауза по монете N часов."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import robust_ema as RE  # noqa: E402
from hedge import atr  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
COST = 0.0008


def beta_past(x, y, w=720):
    n = len(x); b = np.full(n, np.nan)
    ok = np.isfinite(x) & np.isfinite(y); xx = np.where(ok, x, 0); yy = np.where(ok, y, 0); k = ok.astype(float)
    c = lambda v: np.concatenate([[0], np.cumsum(v)])
    sx, sy, sxy, sxx, sk = c(xx), c(yy), c(xx * yy), c(xx * xx), c(k)
    for i in range(w, n):
        a = i - w; m = sk[i] - sk[a]
        if m < w * 0.8:
            continue
        mx = (sx[i] - sx[a]) / m; my = (sy[i] - sy[a]) / m
        vx = (sxx[i] - sxx[a]) / m - mx * mx
        if vx > 0:
            b[i] = ((sxy[i] - sxy[a]) / m - mx * my) / vx
    return b


def run(d, br, bz, AT, beta, V):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    K, G, SB, TPg, N = V['K'], V['G'], V['SB'], V.get('tp'), V['N']
    ar = np.r_[np.nan, c[1:] / c[:-1] - 1]; atrp = AT / c
    out, last = [], -10 ** 9
    for i in range(800, len(c) - N - 2):
        if i - last < N or not np.isfinite(bz[i]) or abs(bz[i]) < K or not np.isfinite(beta[i]) or t[i] - t[i - 1] != H:
            continue
        s = 1 if br[i] > 0 else -1
        gap = s * (beta[i] * br[i] - ar[i])        # насколько альт недоделал движение, в долях цены
        if G is not None and gap < G * atrp[i]:
            continue
        j0 = i + 1; en = o[j0]; st = en - s * SB * AT[i]; risk = SB * AT[i]
        if not (risk > en * 0.002) or d['qv'][i] <= 0:   # застывшие котировки / нулевой ATR
            continue
        tp = en + s * gap * en if TPg else None
        x = None
        for j in range(j0, j0 + N):
            if (s == 1 and l[j] <= st) or (s == -1 and h[j] >= st):
                x = min(o[j], st) if s == 1 else max(o[j], st); break
            if tp is not None and ((s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp)):
                x = max(o[j], tp) if s == 1 else min(o[j], tp); break
        else:
            j = j0 + N - 1; x = c[j]
        out.append(dict(R=(s * (x - en) - COST * (en + x)) / risk, t=int(t[j0]), tx=int(t[j] + H))); last = i
    return out


def main():
    d1, d2 = sys.argv[1], sys.argv[2]
    ls.DATA = d1; bt = ls.load('BTCUSDT'); bidx = {int(x): k for k, x in enumerate(bt['t'])}
    batr = atr(bt['h'], bt['l'], bt['c']); bret = np.r_[np.nan, bt['c'][1:] / bt['c'][:-1] - 1]
    bzs = bret / (batr / bt['c'])
    U = []
    for dd in (d1, d2):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            if s in ('BTCUSDT',):
                continue
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            ix = np.array([bidx.get(int(x), -1) for x in d['t']])
            br = np.where(ix >= 0, bret[ix], np.nan); bz = np.where(ix >= 0, bzs[ix], np.nan)
            ar = np.r_[np.nan, d['c'][1:] / d['c'][:-1] - 1]
            U.append((s, dd == d1, d, br, bz, atr(d['h'], d['l'], d['c']), beta_past(br, ar)))
    VS = [('ОСНОВНОЙ: BTC ≥2 ATR, отставание ≥0.5 ATR, стоп 1 ATR, 2 ч', dict(K=2, G=0.5, SB=1.0, N=2)),
          ('контроль: BTC ≥2 ATR, без условия отставания', dict(K=2, G=None, SB=1.0, N=2)),
          ('BTC ≥2 ATR, отставание ≥1 ATR, 2 ч', dict(K=2, G=1.0, SB=1.0, N=2)),
          ('BTC ≥3 ATR, отставание ≥0.5 ATR, 2 ч', dict(K=3, G=0.5, SB=1.0, N=2)),
          ('BTC ≥2 ATR, отставание ≥0.5 ATR, 1 ч', dict(K=2, G=0.5, SB=1.0, N=1)),
          ('BTC ≥2 ATR, отставание ≥0.5 ATR, 4 ч', dict(K=2, G=0.5, SB=1.0, N=4)),
          ('BTC ≥2 ATR, отставание ≥0.5 ATR, тейк = отставание, 4 ч', dict(K=2, G=0.5, SB=1.0, N=4, tp=True)),
          ('BTC ≥1.5 ATR, отставание ≥0.5 ATR, стоп 1.5 ATR, 2 ч', dict(K=1.5, G=0.5, SB=1.5, N=2))]
    L = ['# Альты догоняют BTC (53 альта, 1h)', '', 'Издержки 0.08% на сторону. R = в долях риска до стопа.', '',
         '| вариант | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    caps = []
    for name, V in VS:
        tr = []
        for s, dev, d, br, bz, AT, be in U:
            for x in run(d, br, bz, AT, be, V):
                x.update(sym=s, dev=dev); tr.append(x)
        for lab, sub in (('23 монеты, до июня 2026', [x for x in tr if x['dev'] and x['t'] < HOLD]),
                         ('30 новых, до июня 2026', [x for x in tr if not x['dev'] and x['t'] < HOLD]),
                         ('все, июнь–окт 2026', [x for x in tr if x['t'] >= HOLD])):
            L.append(f'| {name} | {lab} ' + B.stats([x['R'] for x in sub]))
        a = RE.equity([x for x in tr if x['t'] < HOLD], 0.01, 8)
        caps.append(f'- {name}: риск 1%, до 8 позиций, 2022–май 2026 ×{a[0]:.2f}, просадка {a[1] * 100:.0f}%')
    L += ['', '## Капитал'] + caps
    os.makedirs('p2_out', exist_ok=True); open('p2_out/LEADLAG.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
