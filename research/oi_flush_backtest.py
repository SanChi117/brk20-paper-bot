#!/usr/bin/env python3
"""Кандидат B — «OI flush reversal» (предрегистрация: research/PREREGISTRATION.md, раздел B).

Идея: резкое падение цены одновременно с резким сбросом открытого интереса = принудительные
закрытия (ликвидации/стопы). Такие движения часто частично откатываются. Вход в откат,
выход на возврате к 24-часовой средней или по времени.

Все расчёты — только по закрытым часовым барам; вход на открытии следующего бара.
"""
import argparse
import csv
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brk20_backtest import load, btc_regime, ymd, H  # noqa: E402

SYMS = ['SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'AVAXUSDT', 'NEARUSDT', 'LINKUSDT', 'DOGEUSDT',
        'AAVEUSDT', 'INJUSDT', 'OPUSDT', 'ARBUSDT']
Z = 2.5          # порог z-score (не подбирался)
LOOK = 4         # окно движения, часов
NORM = 720       # окно нормировки, часов (30 дней)
MAXHOLD = 24
MAXPOS = 2
RISK = 0.01
EQ0 = 1000.0
FEE = 0.0005
SLIP = 0.0005


def load_metrics(d, sym):
    p = os.path.join(d, f'{sym}_metrics_1h.csv')
    out = {}
    if not os.path.exists(p):
        return out
    with open(p) as f:
        for r in csv.DictReader(f):
            if r['oi']:
                out[int(r['open_time'])] = float(r['oi'])
    return out


def prep(rows, oi):
    t = [r[0] for r in rows]
    o = [r[1] for r in rows]; h = [r[2] for r in rows]; l = [r[3] for r in rows]; c = [r[4] for r in rows]
    n = len(rows)
    atr = [None] * n
    for i in range(1, n):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        atr[i] = tr if atr[i - 1] is None else atr[i - 1] * 13 / 14 + tr / 14
    r4 = [None] * n; d4 = [None] * n
    for i in range(LOOK, n):
        if t[i] - t[i - LOOK] != LOOK * H:
            continue
        r4[i] = c[i] / c[i - LOOK] - 1
        a, b = oi.get(t[i]), oi.get(t[i - LOOK])
        if a and b:
            d4[i] = a / b - 1
    zr = [None] * n; zo = [None] * n
    # скользящие std по прошлым значениям (без текущего)
    for arr, z in ((r4, zr), (d4, zo)):
        s = s2 = 0.0; k = 0; win = []
        for i in range(n):
            if k >= 200 and arr[i] is not None:
                m = s / k; var = s2 / k - m * m
                if var > 0:
                    z[i] = arr[i] / math.sqrt(var)
            v = arr[i]
            win.append(v)
            if v is not None:
                s += v; s2 += v * v; k += 1
            if len(win) > NORM:
                old = win.pop(0)
                if old is not None:
                    s -= old; s2 -= old * old; k -= 1
    sma24 = [None] * n
    acc = 0.0
    for i in range(n):
        acc += c[i]
        if i >= 24:
            acc -= c[i - 24]
        if i >= 23:
            sma24[i] = acc / 24
    return dict(t=t, o=o, h=h, l=l, c=c, atr=atr, zr=zr, zo=zo, sma=sma24, idx={x: i for i, x in enumerate(t)})


def run(S, fund, side, regime, use_regime):
    long = side == 'long'
    hours = sorted({x for s in S.values() for x in s['t']})
    eq, peak, maxdd = EQ0, EQ0, 0.0
    pos, pend, trades = [], [], []

    def close(p, px, t, why):
        nonlocal eq
        rm = ((px - p['entry']) if long else (p['entry'] - px)) / p['entry']
        pnl = rm * p['notional'] - FEE * p['notional'] + p['fund']
        eq += pnl
        trades.append(dict(sym=p['sym'], entry_t=p['t'], exit_t=t, entry=p['entry'], exit=px, out=why,
                           pnl=pnl - FEE * p['notional'], r=(pnl - FEE * p['notional']) / p['r_usd'], fund=p['fund']))
        pos.remove(p)

    for T in hours:
        # 1) исполнение отложенных входов на открытии бара T
        for q in list(pend):
            s = S[q['sym']]; i = s['idx'].get(T)
            pend.remove(q)
            if i is None or len(pos) >= MAXPOS or any(p['sym'] == q['sym'] for p in pos):
                continue
            px = s['o'][i] * (1 + SLIP if long else 1 - SLIP)
            dist = (px - q['stop']) / px if long else (q['stop'] - px) / px
            if dist <= 0 or dist > 0.10:
                continue
            r_usd = eq * RISK
            notional = min(r_usd / dist, eq * 2)
            eq -= FEE * notional
            pos.append(dict(sym=q['sym'], entry=px, stop=q['stop'], notional=notional, r_usd=r_usd,
                            t=T, i0=i, fund=0.0, exit_next=False))
        # 2) ведение позиций баром T
        for p in list(pos):
            s = S[p['sym']]; i = s['idx'].get(T)
            if i is None:
                continue
            if p['exit_next']:
                close(p, s['o'][i] * (1 - SLIP if long else 1 + SLIP), T, p['exit_next'])
                continue
            fr = fund.get(p['sym'], {}).get(T)
            if fr is not None:
                p['fund'] += (-fr if long else fr) * p['notional']
            if (long and s['l'][i] <= p['stop']) or (not long and s['h'][i] >= p['stop']):
                px = min(s['o'][i], p['stop']) * (1 - SLIP) if long else max(s['o'][i], p['stop']) * (1 + SLIP)
                close(p, px, T, 'stop')
                continue
            if s['sma'][i] is not None and ((long and s['c'][i] >= s['sma'][i]) or (not long and s['c'][i] <= s['sma'][i])):
                p['exit_next'] = 'target'
            elif i - p['i0'] + 1 >= MAXHOLD:
                p['exit_next'] = 'time'
        # 3) сигналы на закрытии бара T (момент T+H) -> вход на открытии T+H
        day = ((T + H) // (24 * H)) * 24 * H
        bull = regime.get(day) if regime else None
        if use_regime and (bull is None or bull != long):
            pass
        else:
            cands = []
            for sym, s in S.items():
                i = s['idx'].get(T)
                if i is None or s['zr'][i] is None or s['zo'][i] is None or s['atr'][i] is None:
                    continue
                if any(p['sym'] == sym for p in pos):
                    continue
                zr, zo = s['zr'][i], s['zo'][i]
                if zo <= -Z and ((long and zr <= -Z) or (not long and zr >= Z)):
                    lo = min(s['l'][max(0, i - 3):i + 1]); hi = max(s['h'][max(0, i - 3):i + 1])
                    stop = lo - 0.5 * s['atr'][i] if long else hi + 0.5 * s['atr'][i]
                    cands.append((abs(zr), dict(sym=sym, stop=stop)))
            for _, q in sorted(cands, key=lambda x: -x[0]):
                pend.append(q)
        if (T + H) % (24 * H) == 0:
            peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak)
    return trades, eq, maxdd, hours


def pf(v):
    g = sum(x for x in v if x > 0); l = -sum(x for x in v if x < 0)
    return round(g / l, 3) if l else None


def ci(tr, n=2000):
    if len(tr) < 10:
        return None
    r = random.Random(7); v = [t['pnl'] for t in tr]; out = []
    for _ in range(n):
        s = [v[r.randrange(len(v))] for _ in v]
        out.append(min(pf(s) or 20, 20))
    out.sort()
    return [round(out[int(.05 * n)], 3), round(out[int(.95 * n)], 3)]


def summ(tr, eq, dd, hours):
    months = (hours[-1] - hours[0]) / (30.44 * 24 * H) if hours else 1
    by = {}
    for t in tr:
        by.setdefault(ymd(t['entry_t']).year, []).append(t)
    best = sorted((t['pnl'] for t in tr), reverse=True)
    return dict(trades=len(tr), per_month=round(len(tr) / months, 2), pf=pf([t['pnl'] for t in tr]), pf_ci90=ci(tr),
                winrate=round(100 * sum(t['pnl'] > 0 for t in tr) / len(tr), 1) if tr else 0,
                avg_r=round(sum(t['r'] for t in tr) / len(tr), 3) if tr else 0,
                ret_pct=round(100 * (eq / EQ0 - 1), 1), max_dd_pct=round(100 * dd, 1),
                net_wo_top5=round(sum(best) - sum(best[:5]), 2),
                exits={k: sum(t['out'] == k for t in tr) for k in ('stop', 'target', 'time')},
                by_year={y: dict(n=len(v), pf=pf([t['pnl'] for t in v]), usd=round(sum(t['pnl'] for t in v), 2)) for y, v in sorted(by.items())})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data'); ap.add_argument('--metrics', default='data_metrics')
    ap.add_argument('--btc', default='data_btc'); ap.add_argument('--out', default='results_oi')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    S, fund = {}, {}
    for sym in SYMS:
        rows, f = load(a.data, sym)
        oi = load_metrics(a.metrics, sym)
        if rows and oi:
            S[sym] = prep(rows, oi); fund[sym] = f
            print(sym, len(rows), 'bars', len(oi), 'oi-hours', flush=True)
    brows, _ = load(a.btc, 'BTCUSDT')
    regime = btc_regime(brows) if brows else None
    res = {}
    lines = ['# Кандидат B — OI flush reversal', '',
             '| Вариант | Сделок | В месяц | WR % | PF | PF 90% | Ср. R | Доходн. % | Просадка % | Без топ-5 $ | Выходы |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for side, use_reg, label in (('long', True, 'B1 лонг + режим BTC (ОСНОВНОЙ)'), ('short', True, 'B2 шорт + режим BTC'),
                                 ('long', False, 'диагностика: лонг без режима'), ('short', False, 'диагностика: шорт без режима')):
        tr, eq, dd, hours = run(S, fund, side, regime, use_reg)
        s = summ(tr, eq, dd, hours); res[label] = s
        print(label, json.dumps(s, ensure_ascii=False), flush=True)
        lines.append(f"| {label} | {s['trades']} | {s['per_month']} | {s['winrate']} | {s['pf']} | {s['pf_ci90']} | {s['avg_r']} | "
                     f"{s['ret_pct']} | {s['max_dd_pct']} | {s['net_wo_top5']} | {s['exits']} |")
    lines += ['', '## По годам', '']
    for k, s in res.items():
        lines.append(f"**{k}**: " + ', '.join(f"{y}: {v['n']} сд., PF {v['pf']}, {v['usd']}$" for y, v in s['by_year'].items()))
        lines.append('')
    open(os.path.join(a.out, 'REPORT.md'), 'w').write('\n'.join(lines) + '\n')
    json.dump(res, open(os.path.join(a.out, 'report.json'), 'w'), ensure_ascii=False, indent=1, default=str)
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
