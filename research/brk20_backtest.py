#!/usr/bin/env python3
"""Проверка BRK-20 / BRK-20S на истории: те же правила, что в brk20_paper.py / brk20s.py.

Прогоняет час за часом по всем 17 монетам с общим счётом, как бот на VPS:
сигналы на закрытии 4h, заявки, вход, стоп, безубыток +2R, трейл по пивотам 4h, max 2 позиции.

Сценарии исполнения:
  fixed  — логика v1.3: заявка действует после закрытия сигнальной свечи, вход по max(уровень, open),
           гэп-стопы по open, стоп в баре входа засчитывается.
  legacy — логика v1.2 (как работал бот до 4.11–22.11): вход по уровню на часах внутри сигнальной свечи.
Издержки:
  botfees   — как в боте: вход 0.02%, стоп 0.04%, проскальзывание 0.02%, без funding.
  realistic — тейкер 0.05% на вход и выход, проскальзывание 0.05%, funding по фактическим ставкам.

Упрощение (честно): трейл использует только ЗАКРЫТЫЕ 4h-бары (бот на VPS ещё учитывает
незакрытую 4h-свечу в ATR/пивотах); ATR считается от начала истории, а не от окна 720 часов.

python3 research/brk20_backtest.py --data data --out results
"""
import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

H = 3600 * 1000
H4 = 4 * H
SYMS = ['SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'AVAXUSDT', 'SUIUSDT', 'APTUSDT', 'NEARUSDT',
        'LINKUSDT', 'AAVEUSDT', 'ARBUSDT', 'OPUSDT', 'DOGEUSDT', 'TAOUSDT', 'ONDOUSDT', 'INJUSDT', 'SEIUSDT']
RISK, MAXPOS, EQ0, TTL = 0.01, 2, 1000.0, 24 * H
COSTS = {
    'botfees': dict(fee_in=0.0002, fee_out=0.0004, slip=0.0002, funding=False),
    'realistic': dict(fee_in=0.0005, fee_out=0.0005, slip=0.0005, funding=True),
}


def ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def load(data, sym):
    p = os.path.join(data, f'{sym}_1h.csv')
    if not os.path.exists(p):
        return [], {}
    rows = []
    with open(p) as f:
        for r in csv.DictReader(f):
            t = int(r['open_time'])
            if t > 10**14:
                t //= 1000
            rows.append((t, float(r['open']), float(r['high']), float(r['low']), float(r['close'])))
    fund = {}
    fp = os.path.join(data, f'{sym}_funding.csv')
    if os.path.exists(fp):
        with open(fp) as f:
            for r in csv.DictReader(f):
                t = int(r['calc_time'])
                if t > 10**14:
                    t //= 1000
                fund[(t // H) * H] = float(r['rate'])
    return rows, fund


class Sym:
    """4h-бары и индикаторы, рассчитанные строго по закрытым барам."""

    def __init__(self, rows1h, side):
        self.h1 = {r[0]: r for r in rows1h}
        agg = {}
        for t, o, h, l, c in rows1h:
            k = t // H4
            if k not in agg:
                agg[k] = [o, h, l, c, 1]
            else:
                a = agg[k]; a[1] = max(a[1], h); a[2] = min(a[2], l); a[3] = c; a[4] += 1
        self.b4 = [(k * H4, v[0], v[1], v[2], v[3]) for k, v in sorted(agg.items()) if v[4] == 4]
        n = len(self.b4)
        self.idx = {b[0]: i for i, b in enumerate(self.b4)}
        self.atr = [None] * n
        for i in range(1, n):
            b, p = self.b4[i], self.b4[i - 1]
            tr = max(b[2] - b[3], abs(b[2] - p[4]), abs(b[3] - p[4]))
            self.atr[i] = tr if self.atr[i - 1] is None else self.atr[i - 1] * 13 / 14 + tr / 14
        self.chan = [None] * n
        for i in range(120, n):
            win = self.b4[i - 120:i]
            self.chan[i] = max(x[2] for x in win) if side == 'long' else min(x[3] for x in win)
        # подтверждённые пивоты (лаг 3): индекс подтверждения -> (pivot_index, price)
        self.piv = []
        for i in range(3, n - 3):
            if side == 'long':
                v = self.b4[i][3]
                if all(v <= self.b4[j][3] for j in range(i - 3, i + 4)):
                    self.piv.append((i + 3, i, v))
            else:
                v = self.b4[i][2]
                if all(v >= self.b4[j][2] for j in range(i - 3, i + 4)):
                    self.piv.append((i + 3, i, v))


def run(data_syms, side, mode, cost, fund_all, start_ms=None, end_ms=None):
    c = COSTS[cost]
    long = side == 'long'
    syms = {s: Sym(r, side) for s, r in data_syms.items() if r}
    hours = sorted({t for s in syms.values() for t in s.h1})
    if start_ms:
        hours = [t for t in hours if t >= start_ms]
    if end_ms:
        hours = [t for t in hours if t < end_ms]
    eq = EQ0
    pos, pend, trades, curve = [], [], [], []
    peak, maxdd = EQ0, 0.0
    last_t = {}

    def close(p, px, t, out):
        nonlocal eq
        rm = ((px - p['entry']) if long else (p['entry'] - px)) / p['entry'] / p['dist']
        pnl = rm * p['r_usd'] - c['fee_out'] * p['notional'] + p['fund']
        eq += pnl
        trades.append({'sym': p['sym'], 'side': side, 'entry_t': p['entry_t'], 'exit_t': t,
                       'entry': p['entry'], 'exit': px, 'out': out,
                       'pnl': pnl - c['fee_in'] * p['notional'],  # полная сделка с комиссией входа
                       'r': (pnl - c['fee_in'] * p['notional']) / p['r_usd'],
                       'funding': p['fund'], 'chase': p['chase']})
        pos.remove(p)

    def try_fill(p, sym, t, o, h, l):
        nonlocal eq
        e = p['entry']
        if long:
            if h < e * (1 + c['slip']):
                return False
            fill = (max(e, o) if mode != 'legacy' else e) * (1 + c['slip'])
            sf = (fill - p['stop']) / fill
        else:
            if l > e * (1 - c['slip']):
                return False
            fill = (min(e, o) if mode != 'legacy' else e) * (1 - c['slip'])
            sf = (p['stop'] - fill) / fill
        pend.remove(p)
        if sf <= 0 or sf > 0.20:
            return True
        r_usd = eq * RISK
        notional = min(r_usd / sf, eq)
        eq -= c['fee_in'] * notional
        q = {'sym': sym, 'entry': fill, 'stop': p['stop'], 'dist': sf, 'notional': notional,
             'r_usd': r_usd, 'stage': 0, 'entry_t': t, 'entry_t4': t // H4 * H4, 'fund': 0.0,
             'be': fill * (1 + 2 * sf) if long else fill * (1 - 2 * sf),
             'chase': abs(fill / e - 1)}
        pos.append(q)
        if mode != 'legacy' and ((long and l <= q['stop']) or (not long and h >= q['stop'])):
            close(q, q['stop'] * (1 - c['slip'] if long else 1 + c['slip']), t, 'stop_fillbar')
        return True

    def process_bar(sym, t):
        if last_t.get(sym, -1) >= t:
            return
        last_t[sym] = t
        S = syms[sym]
        bar = S.h1.get(t)
        if not bar:
            return
        _, o, h, l, cl = bar
        for p in [p for p in pos if p['sym'] == sym]:
            if c['funding'] and t in fund_all.get(sym, {}):
                rate = fund_all[sym][t]
                p['fund'] += (-rate if long else rate) * p['notional']
            stop = p['stop']
            if (long and l <= stop) or (not long and h >= stop):
                if mode != 'legacy':
                    px = (min(o, stop) * (1 - c['slip'])) if long else (max(o, stop) * (1 + c['slip']))
                else:
                    px = stop * (1 - c['slip']) if long else stop * (1 + c['slip'])
                close(p, px, t, 'stop' if p['stage'] == 0 else 'trail')
                continue
            if p['stage'] == 0 and ((long and h >= p['be']) or (not long and l <= p['be'])):
                p['stop'] = p['entry'] * (1 + c['fee_in'] + c['fee_out']) if long else p['entry'] * (1 - c['fee_in'] - c['fee_out'])
                p['stage'] = 1
            if p['stage'] == 1 and t % H4 == 0:
                i4 = S.idx.get(t - H4)  # последний ЗАКРЫТЫЙ 4h-бар
                if i4 is not None and S.atr[i4]:
                    cands = [x for x in S.piv if x[0] <= i4
                             and (x[2] > p['entry'] if long else x[2] < p['entry'])
                             and S.b4[x[1]][0] > p['entry_t4']]
                    if cands:
                        ns = cands[-1][2] - 0.5 * S.atr[i4] if long else cands[-1][2] + 0.5 * S.atr[i4]
                        if (long and ns > p['stop']) or (not long and ns < p['stop']):
                            p['stop'] = ns
        for p in [p for p in pend if p['sym'] == sym]:
            if t > p['exp']:
                pend.remove(p); continue
            if t < p['active_from']:
                continue
            if any(q['sym'] == sym for q in pos):
                pend.remove(p); continue
            if len(pos) >= MAXPOS:
                continue
            try_fill(p, sym, t, o, h, l)

    for X in hours:  # X = момент ПОСЛЕ закрытия часа, начавшегося в X - H... используем t = X как открытие бара
        # 1) сигналы на закрытии 4h-бара, открывшегося в X - H4 (закрылся в X)
        if X % H4 == 0 and mode != 'stopentry':
            t4 = X - H4
            for sym, S in syms.items():
                i = S.idx.get(t4)
                if i is None or i < 130 or not S.atr[i] or S.chan[i] is None:
                    continue
                if any(p['sym'] == sym for p in pend) or any(p['sym'] == sym for p in pos):
                    continue
                close4 = S.b4[i][4]
                lvl = S.chan[i]
                if (long and close4 > lvl) or (not long and close4 < lvl):
                    stop = lvl - 1.75 * S.atr[i] if long else lvl + 1.75 * S.atr[i]
                    sf = (lvl - stop) / lvl if long else (stop - lvl) / lvl
                    if sf <= 0 or sf > 0.20:
                        continue
                    p = {'sym': sym, 'entry': lvl, 'stop': stop, 'exp': t4 + TTL,
                         'active_from': X if mode == 'fixed' else t4 + H}
                    pend.append(p)
                    if mode == 'legacy':  # бот v1.2 сразу прогонял 3 последних часа сигнальной свечи
                        for tt in (t4 + H, t4 + 2 * H, t4 + 3 * H):
                            if p in pend and not any(q['sym'] == sym for q in pos) and len(pos) < MAXPOS:
                                bar = S.h1.get(tt)
                                if bar:
                                    try_fill(p, sym, tt, bar[1], bar[2], bar[3])
                                last_t[sym] = tt
        # 2) закрытый часовой бар, открывшийся в X - H
        t = X - H
        for sym in SYMS:
            if sym in syms and (any(p['sym'] == sym for p in pos) or any(p['sym'] == sym for p in pend)):
                process_bar(sym, t)
        if X % H4 == 0 and mode == 'stopentry':
            # Заранее выставленный стоп-ордер на уровне канала для бара, открывающегося в X.
            # Уровень chan[j] считается только по барам < j, ATR — по последнему закрытому бару.
            for sym, S in syms.items():
                pend[:] = [p for p in pend if p['sym'] != sym]
                j = S.idx.get(X)
                if j is None or j < 131 or S.chan[j] is None or not S.atr[j - 1]:
                    continue
                if any(p['sym'] == sym for p in pos):
                    continue
                lvl = S.chan[j]
                stop = lvl - 1.75 * S.atr[j - 1] if long else lvl + 1.75 * S.atr[j - 1]
                pend.append({'sym': sym, 'entry': lvl, 'stop': stop, 'exp': X + H4 - H, 'active_from': X})
        if X % (24 * H) == 0:
            curve.append((X, eq))
            peak = max(peak, eq)
            maxdd = max(maxdd, (peak - eq) / peak)
    return trades, curve, maxdd, eq


def summarize(trades, eq_end=None, maxdd=None, months=None):
    n = len(trades)
    gw = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gl = -sum(t['pnl'] for t in trades if t['pnl'] < 0)
    out = {'trades': n,
           'winrate_pct': round(100 * sum(t['pnl'] > 0 for t in trades) / n, 1) if n else 0,
           'pf': round(gw / gl, 3) if gl else None,
           'avg_r': round(sum(t['r'] for t in trades) / n, 3) if n else 0,
           'net_usd': round(sum(t['pnl'] for t in trades), 2)}
    if months:
        out['trades_per_month'] = round(n / months, 2)
    if eq_end is not None:
        out['return_pct'] = round(100 * (eq_end / EQ0 - 1), 1)
    if maxdd is not None:
        out['max_dd_pct'] = round(100 * maxdd, 1)
    if n:
        out['avg_chase_pct'] = round(100 * sum(t['chase'] for t in trades) / n, 3)
        out['funding_usd'] = round(sum(t['funding'] for t in trades), 2)
    return out


def bootstrap_pf_ci(trades, n=2000, seed=7):
    import random
    rnd = random.Random(seed)
    pnls = [t['pnl'] for t in trades]
    if len(pnls) < 10:
        return None
    pfs = []
    for _ in range(n):
        s = [rnd.choice(pnls) for _ in pnls]
        gw = sum(x for x in s if x > 0); gl = -sum(x for x in s if x < 0)
        pfs.append(gw / gl if gl else 99)
    pfs.sort()
    return [round(pfs[int(0.05 * n)], 3), round(pfs[int(0.95 * n)], 3)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data')
    ap.add_argument('--out', default='results')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    raw, fund = {}, {}
    for s in SYMS:
        raw[s], fund[s] = load(a.data, s)
    cover = {s: (ymd(r[0][0]).strftime('%Y-%m'), ymd(r[-1][0]).strftime('%Y-%m'), len(r)) for s, r in raw.items() if r}
    report = {'coverage': cover, 'runs': {}}
    lines = ['# BRK-20 / BRK-20S — проверка на истории Binance USD-M Futures', '',
             'Данные: официальные месячные архивы data.binance.vision (1h + funding), SHA256 проверены.', '']
    for side in ('long', 'short'):
        for mode in ('legacy', 'fixed', 'stopentry'):
            for cost in ('botfees', 'realistic'):
                trades, curve, maxdd, eq = run(raw, side, mode, cost, fund)
                months = (curve[-1][0] - curve[0][0]) / (30.44 * 24 * H) if curve else None
                key = f'{side}/{mode}/{cost}'
                s = summarize(trades, eq, maxdd, months)
                s['pf_ci90'] = bootstrap_pf_ci(trades)
                by_year = defaultdict(list)
                for t in trades:
                    by_year[ymd(t['exit_t']).year].append(t)
                s['by_year'] = {y: summarize(v) for y, v in sorted(by_year.items())}
                by_sym = defaultdict(list)
                for t in trades:
                    by_sym[t['sym']].append(t)
                s['by_symbol'] = {k: summarize(v) for k, v in sorted(by_sym.items(), key=lambda kv: -sum(t['pnl'] for t in kv[1]))}
                outs = defaultdict(int)
                for t in trades:
                    outs[t['out']] += 1
                s['exits'] = dict(outs)
                if trades:
                    best = sorted(trades, key=lambda t: -t['pnl'])
                    top5 = sum(t['pnl'] for t in best[:5])
                    s['net_without_top5_usd'] = round(s['net_usd'] - top5, 2)
                report['runs'][key] = s
                with open(os.path.join(a.out, f'trades_{side}_{mode}_{cost}.csv'), 'w', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=list(trades[0].keys()) if trades else ['none'])
                    w.writeheader(); w.writerows(trades)
                with open(os.path.join(a.out, f'equity_{side}_{mode}_{cost}.csv'), 'w', newline='') as f:
                    w = csv.writer(f); w.writerow(['time', 'equity'])
                    w.writerows([(ymd(t).strftime('%Y-%m-%d'), round(e, 2)) for t, e in curve])
                print(key, json.dumps({k: v for k, v in s.items() if k not in ('by_symbol',)}, ensure_ascii=False), flush=True)
    lines.append('| Сценарий | Сделок | В месяц | WR % | PF | PF 90% | Ср. R | Доходность % | Макс. просадка % | Без топ-5 сделок $ |')
    lines.append('|---|---|---|---|---|---|---|---|---|---|')
    for k, s in report['runs'].items():
        lines.append(f"| {k} | {s['trades']} | {s.get('trades_per_month')} | {s['winrate_pct']} | {s['pf']} | {s['pf_ci90']} | "
                     f"{s['avg_r']} | {s.get('return_pct')} | {s.get('max_dd_pct')} | {s.get('net_without_top5_usd')} |")
    for md in ('fixed', 'stopentry'):
      lines += ['', f'## По годам ({md} / realistic)', '']
      for side in ('long', 'short'):
        lines.append(f'**{side}**')
        lines.append('')
        lines.append('| Год | Сделок | WR % | PF | Ср. R | $ |')
        lines.append('|---|---|---|---|---|---|')
        for y, s in report['runs'][f'{side}/{md}/realistic']['by_year'].items():
            lines.append(f"| {y} | {s['trades']} | {s['winrate_pct']} | {s['pf']} | {s['avg_r']} | {s['net_usd']} |")
        lines.append('')
    with open(os.path.join(a.out, 'report.json'), 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(a.out, 'REPORT.md'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
