#!/usr/bin/env python3
"""Пробой азиатского диапазона по направлению (перевёрнутая AMD). 15m, 24 монеты.
Азия 00–07 UTC; первое 15m закрытие за границей в 07–13 UTC → вход по направлению на открытии следующей свечи.
Стоп — середина азиатского диапазона. Выход: A) в 21:00 UTC; B) закрытие 15m за EMA20 (до 21:00); C) тейк 2R (до 21:00)."""
import glob, os, sys, collections
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amd_vwap import load, days_index, manage, stats, quarters, HOLD, FT, SLP  # noqa: E402


def ema(x, n):
    a = 2 / (n + 1); out = np.empty_like(x); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def run(t, o, h, l, c, mode, width_filter):
    keys, start, cnt = days_index(t); out = []
    dr = np.array([h[s:s + n].max() - l[s:s + n].min() for s, n in zip(start, cnt)])
    em = ema(c, 20)
    for k in range(21, len(keys)):
        s0, n = start[k], cnt[k]
        if n != 96:
            continue
        AH, AL = h[s0:s0 + 28].max(), l[s0:s0 + 28].min(); W = AH - AL
        if W <= 0 or (width_filter and W > 0.6 * dr[k - 20:k].mean()):
            continue
        mid = (AH + AL) / 2
        for j in range(s0 + 28, s0 + 52):
            sd = 1 if c[j] > AH else (-1 if c[j] < AL else 0)
            if not sd:
                continue
            j0 = j + 1; en = o[j0]; stop = mid
            if sd * (en - stop) <= 0:
                break
            risk = abs(en - stop)
            tp = en + sd * 2 * risk if mode == 'C' else None
            r = manage(o, h, l, c, j0, s0 + 84 - 1, sd, en, stop, tp, FT + SLP, 'vwap' if mode == 'B' else None, em)
            if r is not None:
                out.append((r, int(t[j0])))
            break
    return out


def main():
    data = [load(p) for p in sorted(glob.glob(f'{sys.argv[1]}/*_15m.csv'))]
    L = ['# Пробой Азии по направлению (перевёрнутая AMD) — результаты', '',
         '| вариант | выборка | сделок | WR | средн. R [ДИ] | PF | ΣR | кварталов в плюсе |', '|---|---|---|---|---|---|---|---|']
    for name, mode, wf in [('A: выход в 21:00, узкая Азия', 'A', True), ('B: выход за EMA20 (15m), узкая Азия', 'B', True),
                           ('C: тейк 2R, узкая Азия', 'C', True), ('A без фильтра ширины', 'A', False), ('B без фильтра ширины', 'B', False)]:
        tr = [x for d in data for x in run(*d[:5], mode, wf)]
        dev = [x for x in tr if x[1] < HOLD]; ho = [x for x in tr if x[1] >= HOLD]
        p, n = quarters(dev)
        L.append(f'| {name} | dev ' + stats([r for r, _ in dev]) + f' {p} из {n} |')
        L.append(f'| ↳ | hold ' + stats([r for r, _ in ho]) + ' – |')
    os.makedirs('smc_out', exist_ok=True); open('smc_out/ASIA.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
