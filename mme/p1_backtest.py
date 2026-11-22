#!/usr/bin/env python3
"""Бэктест П1 «пробой после сжатия». Правила: mme/PREREG.md (раздел П1)."""
import os, random, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from label_study import load, features, btc_regime, H, HOLD  # noqa: E402

FEE, SLIP, RISK, EQ0 = 0.0005, 0.0005, 0.01, 10000.0
DATA = sys.argv[1] if len(sys.argv) > 1 else 'mme_data'
START = int(np.datetime64('2022-02-01', 'ms').astype('int64'))


def signals(d, f, reg, min_score):
    c, o, h, l, t = d['c'], d['o'], d['h'], d['l'], d['t']
    out = {}
    for i in range(1500, len(c) - 1):
        if t[i] < START or np.isnan(f['bpct'][i - 1]) or f['bpct'][i - 1] >= 0.30:
            continue
        up, dn = c[i] > f['hi36'][i], c[i] < f['lo36'][i]
        if not (up or dn):
            continue
        dirn = 1 if up else -1
        rg = reg.get(int((t[i] + H) // (24 * H)))
        if rg is None or (rg == 'up' and dirn < 0) or (rg == 'down' and dirn > 0):
            continue
        lvl = f['hi36'][i] if up else f['lo36'][i]
        a = f['atr'][i]; rng = f['rng'][i] or 1e-12; z = f['zoi'][i]
        if np.isnan(z) or z < -0.5 or dirn * (c[i] - lvl) > 1.5 * a:
            continue
        body = abs(c[i] - o[i]) / rng
        cpos = (c[i] - l[i]) / rng if up else (h[i] - c[i]) / rng
        wick = (h[i] - max(o[i], c[i])) / rng if up else (min(o[i], c[i]) - l[i]) / rng
        tk = f['tak1'][i]
        sc = sum(bool(x) for x in [dirn * (c[i] - lvl) >= 0.2 * a, wick < 0.25, body >= 0.5 and cpos >= 0.7,
                                    (f['rvol'][i] or 0) >= 1.5, (tk >= 0.55) if up else (tk <= 0.45), z > 0.5])
        if sc < min_score:
            continue
        lo6, hi6 = l[i - 5:i + 1].min(), h[i - 5:i + 1].max()
        out[t[i] + H] = dict(i=i, dir=dirn, lvl=lvl, atr=a, stop_ref=(lo6 - 0.3 * a) if up else (hi6 + 0.3 * a))
    return out


def run(D, F, S, mode='market', be=True, partial=True):
    times = sorted({x for s in D for x in D[s]['t'] if x >= START})
    idx = {s: {x: k for k, x in enumerate(D[s]['t'])} for s in D}
    fund = {s: dict(D[s]['fund']) for s in D}
    eq, peak, mdd = EQ0, EQ0, 0.0
    pos, pend, trades, cool = [], [], [], {}

    def close_part(p, px, frac, T, why):
        nonlocal eq
        q = p['qty'] * frac
        pnl = p['dir'] * (px - p['entry']) * q - FEE * px * q
        eq += pnl; p['pnl'] += pnl; p['qty'] -= q
        if p['qty'] <= 1e-12:
            p['pnl'] += p['fund']; eq += p['fund']
            trades.append(dict(sym=p['sym'], t=p['t'], exit_t=T, pnl=p['pnl'], r=p['pnl'] / p['risk'], why=why))
            pos.remove(p)
            if why == 'stop':
                cool[p['sym']] = T + 4 * H

    for T in times:
        # исполнение отложенных входов на открытии свечи T
        for q in list(pend):
            s = q['sym']; k = idx[s].get(T)
            if k is None:
                continue
            d = D[s]; dirn = q['dir']
            fill = None
            if q['mode'] == 'market':
                fill = d['o'][k]; pend.remove(q)
            else:
                lim = q['lvl'] + dirn * 0.1 * q['atr']
                if (dirn > 0 and d['l'][k] <= lim) or (dirn < 0 and d['h'][k] >= lim):
                    fill = min(d['o'][k], lim) if dirn > 0 else max(d['o'][k], lim); pend.remove(q)
                elif T >= q['exp']:
                    pend.remove(q); continue
                else:
                    continue
            if fill is None or any(p['sym'] == s for p in pos) or sum(p['dir'] == dirn for p in pos) >= 3:
                continue
            fill *= (1 + SLIP * dirn)
            stop = q['stop_ref']; a = q['atr']
            dist = dirn * (fill - stop)
            if dist <= 0 or dist > 3 * a:
                continue
            if dist < 0.8 * a:
                stop = fill - dirn * 0.8 * a; dist = 0.8 * a
            risk = eq * RISK; qty = risk / dist
            eq -= FEE * fill * qty
            p = dict(sym=s, dir=dirn, entry=fill, stop=stop, qty=qty, risk=risk, R=dist, t=T, k0=k, pnl=-FEE * fill * qty,
                     fund=0.0, be=False, part=False, atr=a, exit_next=None)
            pos.append(p)
        # ведение
        for p in list(pos):
            s = p['sym']; k = idx[s].get(T)
            if k is None:
                continue
            d, f = D[s], F[s]; dirn = p['dir']
            o, h, l, c = d['o'][k], d['h'][k], d['l'][k], d['c'][k]
            if p['exit_next']:
                close_part(p, o * (1 - SLIP * dirn), 1.0, T, p['exit_next']); continue
            fr = fund[s].get(T)
            if fr is not None:
                p['fund'] -= dirn * fr * p['qty'] * o
            # стоп раньше цели
            if (dirn > 0 and l <= p['stop']) or (dirn < 0 and h >= p['stop']):
                px = (min(o, p['stop']) if dirn > 0 else max(o, p['stop'])) * (1 - SLIP * dirn)
                close_part(p, px, 1.0, T, 'be' if p['be'] and not p['part'] else ('trail' if p['part'] else 'stop')); continue
            best = (h - p['entry']) / p['R'] if dirn > 0 else (p['entry'] - l) / p['R']
            if partial and not p['part'] and best >= 2.0:
                px = p['entry'] + dirn * 2.0 * p['R']
                close_part(p, px, 0.4, T, 'tp'); p['part'] = True
                if p not in pos:
                    continue
            if be and not p['be'] and best >= 1.5:
                p['stop'] = p['entry'] * (1 + dirn * 2 * (FEE + SLIP)); p['be'] = True
            if (p['part'] or not partial) and best >= 2.0:
                lo6, hi6 = d['l'][k - 5:k + 1].min(), d['h'][k - 5:k + 1].max()
                tr = lo6 - 0.3 * p['atr'] if dirn > 0 else hi6 + 0.3 * p['atr']
                p['stop'] = max(p['stop'], tr) if dirn > 0 else min(p['stop'], tr)
            z = f['zoi'][k]
            if not np.isnan(z) and z < -0.5 and dirn * (c - d['c'][k - 4]) <= 0:
                p['exit_next'] = 'flow'
            elif k - p['k0'] >= 71:
                p['exit_next'] = 'time'
        # новые сигналы на закрытии свечи T-1 -> вход на T+... (сигналы ключом = время открытия следующей свечи)
        for s in S:
            q = S[s].get(T + H)
            if q is None or cool.get(s, 0) > T or any(p['sym'] == s for p in pos):
                continue
            pend.append(dict(sym=s, mode=mode, exp=T + H + 6 * H, **q))
        if (T + H) % (24 * H) == 0:
            peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    return trades, eq, mdd


def pf(v):
    g = sum(x for x in v if x > 0); l = -sum(x for x in v if x < 0)
    return g / l if l else float('inf')


def report(name, tr, eq, mdd):
    if not tr:
        return [f'**{name}**: сделок нет', '']
    pnl = [x['pnl'] for x in tr]; rnd = random.Random(3); bs = []
    for _ in range(2000):
        s = [pnl[rnd.randrange(len(pnl))] for _ in pnl]; bs.append(min(pf(s), 20))
    bs.sort()
    best = sorted(pnl, reverse=True)
    by = {}
    for x in tr:
        y = str(np.datetime64(int(x['t']), 'ms'))[:4]; by.setdefault(y, []).append(x['pnl'])
    dev = [x['pnl'] for x in tr if x['t'] < HOLD]; hold = [x['pnl'] for x in tr if x['t'] >= HOLD]
    months = (max(x['t'] for x in tr) - min(x['t'] for x in tr)) / (30.44 * 24 * H)
    why = {}
    for x in tr:
        why[x['why']] = why.get(x['why'], 0) + 1
    L = [f'**{name}**', '',
         '| Сделок | В месяц | PF | PF 90% ДИ | Выигрышных | Ср. R | Доходность | Макс. просадка | Без 5 лучших |', '|---|---|---|---|---|---|---|---|---|',
         f"| {len(tr)} | {len(tr)/months:.1f} | {pf(pnl):.2f} | {bs[100]:.2f}–{bs[1900]:.2f} | {100*np.mean([p>0 for p in pnl]):.0f}% | {np.mean([x['r'] for x in tr]):+.2f} | "
         f"{100*(eq/EQ0-1):+.0f}% | {100*mdd:.0f}% | {sum(best[5:]):+.0f}$ |", '',
         f"Разработка: {len(dev)} сделок, PF {pf(dev):.2f}, {sum(dev):+.0f}$ · Отложенная: {len(hold)} сделок, PF {pf(hold):.2f}, {sum(hold):+.0f}$", '',
         'По годам: ' + ', '.join(f'{y}: {len(v)} сд., PF {pf(v):.2f}, {sum(v):+.0f}$' for y, v in sorted(by.items())), '',
         'Выходы: ' + ', '.join(f'{k} {v}' for k, v in sorted(why.items())), '']
    return L


def main():
    syms = sorted({fn.split('_')[0] for fn in os.listdir(DATA) if fn.endswith('_1h.csv')})
    D = {s: load(s) for s in syms}
    D = {s: d for s, d in D.items() if d is not None and len(d['c']) > 2000}
    reg = btc_regime(D['BTCUSDT'])
    F = {s: features(D[s]) for s in D}
    S5 = {s: signals(D[s], F[s], reg, 5) for s in D}
    S3 = {s: signals(D[s], F[s], reg, 3) for s in D}
    out = ['# П1 «Пробой после сжатия» — бэктест', '', f'Монет: {len(D)}, депозит {EQ0:.0f}$, риск 1% на сделку, издержки 0,1% на сторону + фандинг.', '']
    out += report('ОСНОВНОЙ: вход по рынку, безубыток, частичная фиксация', *run(D, F, S5))
    out += ['## Диагностика (решение по ней не принимается)', '']
    out += report('Вход лимитом на ретесте', *run(D, F, S5, mode='retest'))
    out += report('Без безубытка', *run(D, F, S5, be=False))
    out += report('Без частичной фиксации (весь объём на трейлинге)', *run(D, F, S5, partial=False))
    out += report('Оценка ≥ 3 вместо ≥ 5', *run(D, F, S3))
    rep = '\n'.join(out)
    os.makedirs('mme_out', exist_ok=True)
    open('mme_out/P1_REPORT.md', 'w').write(rep + '\n')
    print(rep)


if __name__ == '__main__':
    main()
