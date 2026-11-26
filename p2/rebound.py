#!/usr/bin/env python3
"""Отскок после глубокого падения (54 монеты, 1h). См. p2/PREREG.md, раздел «Отскок после обвала».
На закрытии часа i: минимум последних 72 ч ниже 20-дневного максимума (480 ч до этого минимума) на ≥ D;
разворот: 1h закрылся выше максимума предыдущих 24 ч, объём часа ≥ V × средний объём 24 ч.
Вход на открытии i+1. Стоп = минимум падения (72 ч) − 0.5 ATR; пропуск, если стоп дальше 15% или ближе 1%.
Выход: трейлинг-стоп «максимум с входа − T·ATR» (по закрытым барам) или тейк k·R, либо через N часов.
Издержки 0.08% на сторону. Пауза по монете 72 ч после выхода."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import robust_ema as RE  # noqa: E402
from hedge import atr  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
COST = 0.0008


def run(d, AT, V):
    o, h, l, c, t, qv = d['o'], d['h'], d['l'], d['c'], d['t'], d['qv']
    D, VOL, T, TP, N, CTRL = V['D'], V.get('vol', 2.0), V.get('trail', 3.0), V.get('tp'), V.get('N', 168), V.get('ctrl', False)
    n = len(c); out = []; nxt = 560
    for i in range(560, n - 2):
        if i < nxt or t[i] - t[i - 1] != H or not np.isfinite(AT[i]) or AT[i] <= 0:
            continue
        if not c[i] > h[i - 24:i].max():
            continue
        mq = qv[i - 24:i].mean()
        if VOL and not (mq > 0 and qv[i] >= VOL * mq):
            continue
        k = i - 72 + int(np.argmin(l[i - 72:i + 1])); L = l[k]
        hmax = h[max(0, k - 480):k].max() if k > 0 else np.nan
        if not CTRL and not (L <= hmax * (1 - D)):
            continue
        j0 = i + 1; en = o[j0]; st = L - 0.5 * AT[i]; risk = en - st
        if not (0.01 * en <= risk <= 0.15 * en):
            continue
        tp = en + TP * risk if TP else None
        hh = en; x = None; j = j0
        for j in range(j0, min(n, j0 + N)):
            if l[j] <= st:
                x = min(o[j], st); break
            if tp is not None and h[j] >= tp:
                x = max(o[j], tp); break
            hh = max(hh, h[j])
            if T:
                st = max(st, hh - T * AT[j])
        else:
            x = c[j]
        out.append(dict(R=(x - en - COST * (en + x)) / risk, t=int(t[j0]), tx=int(t[j] + H)))
        nxt = j + 72
    return out


def main():
    U = []
    for dd, dev in ((sys.argv[1], True), (sys.argv[2], False)):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            U.append((s, dev, d, atr(d['h'], d['l'], d['c'])))
    VS = [('ОСНОВНОЙ: падение ≥30%, разворот с объёмом ×2, трейлинг 3 ATR, до 7 дней', dict(D=0.30)),
          ('контроль: тот же разворот без условия падения', dict(D=0.30, ctrl=True)),
          ('падение ≥20%', dict(D=0.20)),
          ('падение ≥40%', dict(D=0.40)),
          ('без фильтра объёма', dict(D=0.30, vol=0)),
          ('трейлинг 5 ATR', dict(D=0.30, trail=5.0)),
          ('тейк 3R без трейлинга', dict(D=0.30, trail=0, tp=3.0)),
          ('тейк 2R без трейлинга', dict(D=0.30, trail=0, tp=2.0)),
          ('держать 3 дня, трейлинг 3 ATR', dict(D=0.30, N=72))]
    L = ['# Отскок после обвала (54 монеты, 1h, лонг)', '', 'Издержки 0.08% на сторону. R = в долях риска до стопа.', '',
         '| вариант | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    caps, years = [], []
    for name, V in VS:
        tr = []
        for s, dev, d, AT in U:
            for x in run(d, AT, V):
                x.update(sym=s, dev=dev); tr.append(x)
        for lab, sub in (('24 монеты, до июня 2026', [x for x in tr if x['dev'] and x['t'] < HOLD]),
                         ('30 новых, до июня 2026', [x for x in tr if not x['dev'] and x['t'] < HOLD]),
                         ('все, июнь–окт 2026', [x for x in tr if x['t'] >= HOLD])):
            L.append(f'| {name} | {lab} ' + B.stats([x['R'] for x in sub]))
        a = RE.equity([x for x in tr if x['t'] < HOLD], 0.01, 6)
        caps.append(f'- {name}: риск 1%, до 6 позиций, до июня 2026 ×{a[0]:.2f}, просадка {a[1] * 100:.0f}%')
        import datetime as dt
        yr = {}
        for x in tr:
            y = dt.datetime.utcfromtimestamp(x['t'] / 1000).year; yr.setdefault(y, []).append(x['R'])
        years.append(f'- {name}: ' + ', '.join(f'{y}: {len(v)} сд, {np.mean(v):+.2f}R' for y, v in sorted(yr.items())))
    L += ['', '## Капитал'] + caps + ['', '## По годам'] + years
    os.makedirs('p2_out', exist_ok=True); open('p2_out/REBOUND.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
