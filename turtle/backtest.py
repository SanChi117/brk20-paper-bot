#!/usr/bin/env python3
"""Пробой дневного канала (система «черепах»). См. turtle/PREREG.md. Сигнал на закрытии дня, вход на открытии
следующего; стоп 2·ATR20 внутри дня; выход по обратному каналу на открытии следующего дня; издержки и фандинг."""
import csv, datetime as dt, os, sys
import numpy as np

D = 86_400_000
HOLD = int(dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
FEE, SLIP = 0.0005, 0.0005
CORE = ['BTCUSDT', 'ETHUSDT']
TOP = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'LINKUSDT', 'LTCUSDT', 'BCHUSDT']


def load(data, s):
    a = np.loadtxt(f'{data}/{s}_1d.csv', delimiter=',', skiprows=1)
    f = np.loadtxt(f'{data}/{s}_funding.csv', delimiter=',', skiprows=1, ndmin=2)
    return a, f


def run(a, f, n_in, n_out, longs=True, shorts=True, regime=None, t_reg=None):
    t, o, h, l, c = a.T
    t = t.astype(np.int64); n = len(t)
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    atr = np.convolve(tr, np.ones(20) / 20)[:n]
    ft, fr = (f[:, 0].astype(np.int64), f[:, 1]) if len(f) else (np.array([], np.int64), np.array([]))
    trades = []; pos = None
    for i in range(max(n_in, 21), n - 1):
        hi_in = h[i - n_in:i].max(); lo_in = l[i - n_in:i].min()
        hi_out = h[i - n_out:i].max(); lo_out = l[i - n_out:i].min()
        if pos:
            s, ent, stop, ti = pos['s'], pos['px'], pos['stop'], pos['i']
            # стоп внутри дня i (с дня после входа; день входа тоже — вход на открытии)
            if (s == 1 and l[i] <= stop) or (s == -1 and h[i] >= stop):
                ex = min(o[i], stop) if s == 1 else max(o[i], stop)
                trades.append(close(pos, ex, t[i] + D, ft, fr)); pos = None
            elif (s == 1 and c[i] < lo_out) or (s == -1 and c[i] > hi_out):
                trades.append(close(pos, o[i + 1], t[i + 1], ft, fr)); pos = None
                continue
        if pos is None:
            rg = 0
            if regime is not None:
                k = np.searchsorted(t_reg, t[i], side='right') - 1
                rg = regime[k] if k >= 0 else 0
            sig = 0
            if longs and c[i] > hi_in and (regime is None or rg == 1):
                sig = 1
            elif shorts and c[i] < lo_in and (regime is None or rg == -1):
                sig = -1
            if sig and atr[i] > 0:
                px = o[i + 1]; stop = px - sig * 2 * atr[i]
                pos = dict(s=sig, px=px, stop=stop, risk=2 * atr[i], i=i + 1, t=t[i + 1])
                # стоп в день входа
                j = i + 1
                if (sig == 1 and l[j] <= stop) or (sig == -1 and h[j] >= stop):
                    trades.append(close(pos, stop, t[j] + D, ft, fr)); pos = None
    return trades


def close(pos, ex, t_exit, ft, fr):
    s, px, risk = pos['s'], pos['px'], pos['risk']
    a, b = np.searchsorted(ft, pos['t']), np.searchsorted(ft, t_exit)
    fund = fr[a:b].sum() * px  # лонг платит положительный фандинг
    cost = (px + ex) * (FEE + SLIP)
    R = (s * (ex - px) - cost - s * fund) / risk
    return dict(t_entry=int(pos['t']), t_exit=int(t_exit), s=s, R=R, days=(t_exit - pos['t']) / D, risk_pct=risk / px)


def stats(tr):
    if not tr:
        return '| 0 | – | – | – | – | – | – |'
    R = np.array([x['R'] for x in tr]); w = R[R > 0].sum(); ls = -R[R < 0].sum()
    cum = np.cumsum(R[np.argsort([x['t_exit'] for x in tr])])
    dd = (np.maximum.accumulate(np.r_[0, cum]) - np.r_[0, cum]).max()
    pf = w / ls if ls else float('inf')
    return f"| {len(R)} | {(R>0).mean()*100:.0f}% | {R.mean():+.2f} | {pf:.2f} | {R.sum():+.1f} | {dd:.1f} | {R.sum()-np.sort(R)[::-1][:5].sum():+.1f} |"


def equity(tr, risk, cap_open=6):
    ev = sorted(tr, key=lambda x: x['t_entry']); eq = 1.0; peak = 1.0; mdd = 0; op = []
    for x in ev:
        op.sort()
        while op and op[0][0] <= x['t_entry']:
            _, st, R = op.pop(0); eq += st * R; peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
        if len(op) < cap_open:
            op.append((x['t_exit'], eq * risk, x['R']))
    for _, st, R in sorted(op):
        eq += st * R; peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
    return eq, mdd


def main():
    data = sys.argv[1]; os.makedirs('turtle_out', exist_ok=True)
    A = {s: load(data, s) for s in TOP}
    bt, bo, bh, bl, bc = A['BTCUSDT'][0].T
    sma = np.convolve(bc, np.ones(200) / 200)[:len(bc)]
    reg = np.where(np.arange(len(bc)) >= 199, np.where(bc > sma, 1, -1), 0)
    treg = bt.astype(np.int64) + D  # режим известен после закрытия дня
    V = {
        'A 20/10 BTC+ETH (основной)': (CORE, 20, 10, {}),
        'B 55/20 BTC+ETH': (CORE, 55, 20, {}),
        'C 20/10 BTC+ETH только лонг': (CORE, 20, 10, dict(shorts=False)),
        'D 20/10 топ-10': (TOP, 20, 10, {}),
        'E 20/10 топ-10 + режим BTC/SMA200': (TOP, 20, 10, dict(regime=reg, t_reg=treg)),
        'F 55/20 топ-10 + режим BTC/SMA200': (TOP, 55, 20, dict(regime=reg, t_reg=treg)),
    }
    L = ['# Пробой дневного канала — результаты', '', 'R после комиссий, проскальзывания и фандинга. dev = 2019-09…2026-05, hold = 2026-06…10.', '',
         '| вариант | выборка | сделок | WR | avgR | PF | ΣR | maxDD R | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|---|']
    res = {}
    for name, (syms, ni, no, kw) in V.items():
        tr = []
        for s in syms:
            for x in run(*A[s], ni, no, **kw):
                x['sym'] = s; tr.append(x)
        res[name] = tr
        L.append(f'| {name} | dev ' + stats([x for x in tr if x['t_entry'] < HOLD]))
        L.append(f'| {name} | hold ' + stats([x for x in tr if x['t_entry'] >= HOLD]))
    L += ['', '## По годам (A и E)', '| вариант | год | сделок | WR | avgR | PF | ΣR | maxDD R | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|---|']
    for name in ('A 20/10 BTC+ETH (основной)', 'E 20/10 топ-10 + режим BTC/SMA200'):
        for y in range(2019, 2027):
            a = int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000); b = int(dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
            L.append(f'| {name[:1]} | {y} ' + stats([x for x in res[name] if a <= x['t_entry'] < b]))
    L += ['', '## Капитал (сложный процент, риск на сделку, ≤6 позиций одновременно)']
    for name, tr in res.items():
        for r in (0.01, 0.02):
            eq, mdd = equity(tr, r)
            L.append(f'- {name}, риск {r*100:.0f}%: ×{eq:.2f}, maxDD {mdd*100:.0f}%')
    med = {k: (np.median([x['days'] for x in v]), np.median([x['risk_pct'] for x in v]) * 100) for k, v in res.items() if v}
    L += ['', 'Медиана удержания (дни) / стопа (% цены): ' + '; '.join(f'{k[:1]} {d:.0f}д/{r:.1f}%' for k, (d, r) in med.items())]
    open('turtle_out/REPORT.md', 'w').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
