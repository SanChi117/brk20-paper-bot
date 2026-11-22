#!/usr/bin/env python3
"""Бэктест «Трендовый портфель» v1 (правила: trend/PREREG.md)."""
import csv, math, os, sys
import numpy as np

DAY = 86400000
FEE, SLIP = 0.0005, 0.0005
EQ0 = 10000.0
HOLD = int(np.datetime64('2026-06-01', 'ms').astype('int64'))
START = int(np.datetime64('2021-01-01', 'ms').astype('int64'))
DATA = sys.argv[1] if len(sys.argv) > 1 else 'trend_data'
P = dict(maxpos=8, risk=0.006, gross=3.0, trail=3.0, top=0.2, minvol=20e6, minage=90)


def load():
    S = {}
    for fn in os.listdir(DATA):
        if not fn.endswith('_1d.csv'):
            continue
        s = fn[:-7]
        rows = list(csv.DictReader(open(f'{DATA}/{fn}')))
        if len(rows) < 120:
            continue
        t = np.array([int(r['t']) for r in rows], dtype=np.int64)
        d = {k: np.array([float(r[k]) for r in rows]) for k in ('o', 'h', 'l', 'c', 'qv')}
        d['t'] = t; d['idx'] = {int(x): i for i, x in enumerate(t)}
        fund = {}
        for r in csv.DictReader(open(f'{DATA}/{s}_funding.csv')):
            k = (int(r['t']) // DAY) * DAY; fund[k] = fund.get(k, 0.0) + float(r['rate'])
        d['fund'] = fund
        c = d['c']; n = len(c)
        lr = np.r_[np.nan, np.diff(np.log(c))]
        tr = np.maximum(d['h'] - d['l'], np.maximum(abs(d['h'] - np.r_[c[0], c[:-1]]), abs(d['l'] - np.r_[c[0], c[:-1]])))
        def roll(x, w, fn):
            out = np.full(n, np.nan)
            for i in range(w - 1, n):
                out[i] = fn(x[i - w + 1:i + 1])
            return out
        d['sma50'] = roll(c, 50, np.mean); d['sma100'] = roll(c, 100, np.mean)
        d['vol20'] = roll(lr, 20, np.std); d['vol30'] = roll(lr, 30, np.std)
        d['atr20'] = roll(tr, 20, np.mean); d['qv30'] = roll(d['qv'], 30, np.mean)
        d['ret30'] = np.r_[np.full(30, np.nan), c[30:] / c[:-30] - 1]
        S[s] = d
    return S


def run(S):
    btc = S['BTCUSDT']
    days = sorted({int(x) for d in S.values() for x in d['t'] if x >= START})
    eq = EQ0; pos = {}; trades = []; curve = []; pending_exit = set()

    def val(s, i):
        return S[s]['c'][i]

    def close(s, px, D, why):
        nonlocal eq
        p = pos.pop(s); px = px * (1 - SLIP * p['dir'])
        pnl = p['dir'] * (px - p['entry']) * p['qty'] - FEE * px * p['qty'] + p['fund']
        eq += pnl  # комиссия входа уже списана при входе
        trades.append(dict(s=s, t=p['t'], exit=D, pnl=pnl - p['efee'], why=why, days=(D - p['t']) // DAY))
    for D in days:
        # выходы по смене режима, назначенные вчера
        for s in list(pending_exit):
            i = S[s]['idx'].get(D)
            if s in pos and i is not None:
                close(s, S[s]['o'][i], D, 'режим')
        pending_exit.clear()
        dt = np.datetime64(D, 'ms').astype(object)
        if dt.weekday() == 0:
            bi = btc['idx'].get(D - DAY)
            if bi is not None and not np.isnan(btc['sma100'][bi]):
                regime = 1 if btc['c'][bi] > btc['sma100'][bi] else -1
                elig = []
                for s, d in S.items():
                    i = d['idx'].get(D - DAY)
                    if i is None or i < P['minage'] or D not in d['idx']:
                        continue
                    if not (d['qv30'][i] >= P['minvol']) or np.isnan(d['ret30'][i]) or np.isnan(d['vol30'][i]) or d['vol30'][i] <= 0:
                        continue
                    elig.append((s, i))
                if len(elig) >= 10:
                    rets = np.array([S[s]['ret30'][i] for s, i in elig])
                    lo_q, hi_q = np.quantile(rets, P['top']), np.quantile(rets, 1 - P['top'])
                    cand = {}
                    for s, i in elig:
                        d = S[s]; r = d['ret30'][i]
                        if regime > 0 and d['c'][i] > d['sma50'][i] and r > 0 and r >= hi_q:
                            cand[s] = abs(r) / d['vol30'][i]
                        if regime < 0 and d['c'][i] < d['sma50'][i] and r < 0 and r <= lo_q:
                            cand[s] = abs(r) / d['vol30'][i]
                    for s in list(pos):
                        if s not in cand or pos[s]['dir'] != regime:
                            i = S[s]['idx'].get(D)
                            if i is not None:
                                close(s, S[s]['o'][i], D, 'ребаланс')
                    gross = sum(p['qty'] * S[s]['o'][S[s]['idx'][D]] for s, p in pos.items() if D in S[s]['idx'])
                    for s in sorted(cand, key=lambda x: -cand[x]):
                        if len(pos) >= P['maxpos']:
                            break
                        if s in pos:
                            continue
                        d = S[s]; i = d['idx'][D]; ip = i - 1
                        px = d['o'][i] * (1 + SLIP * regime)
                        notional = eq * P['risk'] / d['vol20'][ip] if d['vol20'][ip] > 0 else 0
                        notional = min(notional, max(0.0, P['gross'] * eq - gross))
                        if notional < 10:
                            continue
                        qty = notional / px; efee = FEE * px * qty; eq -= efee; gross += notional
                        pos[s] = dict(dir=regime, entry=px, qty=qty, t=D, best=d['c'][ip],
                                      stop=px - regime * P['trail'] * d['atr20'][ip], fund=0.0, efee=efee)
        # ведение днём D
        unreal = 0.0
        for s in list(pos):
            d = S[s]; i = d['idx'].get(D)
            if i is None:
                continue
            p = pos[s]; o, h, l, c = d['o'][i], d['h'][i], d['l'][i], d['c'][i]
            p['fund'] -= p['dir'] * d['fund'].get(D, 0.0) * p['qty'] * o
            if (p['dir'] > 0 and l <= p['stop']) or (p['dir'] < 0 and h >= p['stop']):
                close(s, min(o, p['stop']) if p['dir'] > 0 else max(o, p['stop']), D, 'трейлинг'); continue
            p['best'] = max(p['best'], c) if p['dir'] > 0 else min(p['best'], c)
            ns = p['best'] - p['dir'] * P['trail'] * d['atr20'][i]
            p['stop'] = max(p['stop'], ns) if p['dir'] > 0 else min(p['stop'], ns)
            unreal += p['dir'] * (c - p['entry']) * p['qty'] + p['fund']
        bi = btc['idx'].get(D)
        if bi is not None and not np.isnan(btc['sma100'][bi]):
            rg = 1 if btc['c'][bi] > btc['sma100'][bi] else -1
            for s, p in pos.items():
                if p['dir'] != rg:
                    pending_exit.add(s)
        curve.append((D, eq + unreal))
    return trades, curve


def stats(trades, curve):
    eqs = np.array([v for _, v in curve]); ts = [t for t, _ in curve]
    peak = np.maximum.accumulate(eqs); dd = ((peak - eqs) / peak).max()
    years = (ts[-1] - ts[0]) / (365.25 * DAY)
    cagr = (eqs[-1] / EQ0) ** (1 / years) - 1
    pnl = [x['pnl'] for x in trades]
    g = sum(x for x in pnl if x > 0); l = -sum(x for x in pnl if x < 0)
    yr = {}
    for t, v in curve:
        y = str(np.datetime64(t, 'ms'))[:4]; yr.setdefault(y, [v, v]); yr[y][1] = v
    prev = EQ0; yrs = []
    for y in sorted(yr):
        yrs.append((y, yr[y][1] / prev - 1)); prev = yr[y][1]
    hv = [v for t, v in curve if t >= HOLD]; hb = [v for t, v in curve if t < HOLD]
    hold = hv[-1] / hb[-1] - 1 if hv and hb else float('nan')
    best = sorted(pnl, reverse=True)
    why = {}
    for x in trades:
        why[x['why']] = why.get(x['why'], 0) + 1
    return dict(final=eqs[-1], cagr=cagr, dd=dd, pf=g / l if l else float('inf'), n=len(trades), win=np.mean([p > 0 for p in pnl]),
                yrs=yrs, hold=hold, wo5=sum(best[5:]), days=np.mean([x['days'] for x in trades]), why=why)


def main():
    S = load(); print('монет с историей:', len(S), flush=True)
    tr, cv = run(S); s = stats(tr, cv)
    btc = S['BTCUSDT']; b0 = btc['c'][btc['idx'][START]]; b1 = btc['c'][-1]
    L = ['# «Трендовый портфель» v1 — бэктест', '',
         f'Монет в архиве (включая делистинг): {len(S)}. Счёт $10 000, издержки 0,1% на сторону + фандинг.', '',
         '| Итог | CAGR | Макс. просадка | PF сделок | Сделок | Выигрышных | Ср. удержание | Без 5 лучших | Отложенная 06–10.2026 |',
         '|---|---|---|---|---|---|---|---|---|',
         f"| ${s['final']:,.0f} | {100*s['cagr']:+.0f}% | {100*s['dd']:.0f}% | {s['pf']:.2f} | {s['n']} | {100*s['win']:.0f}% | {s['days']:.0f} дн | {s['wo5']:+,.0f}$ | {100*s['hold']:+.1f}% |",
         '', 'По годам: ' + ', '.join(f'{y}: {100*r:+.0f}%' for y, r in s['yrs']), '',
         'Выходы: ' + ', '.join(f'{k} {v}' for k, v in s['why'].items()), '',
         f'Для сравнения: BTC купил и держи за тот же период {100*(b1/b0-1):+.0f}%.']
    rep = '\n'.join(L); os.makedirs('trend_out', exist_ok=True)
    open('trend_out/REPORT.md', 'w').write(rep + '\n'); print(rep)


if __name__ == '__main__':
    main()
