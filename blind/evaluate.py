#!/usr/bin/env python3
"""Оценка сетапов слепого теста по будущим свечам 1h (закрытый ключ из кэша). Лимитный вход в зоне, срок действия,
стоп и тейк; внутри свечи стоп раньше тейка; издержки 0.05%+0.03% на сторону."""
import json, sys
import numpy as np
H = 3600_000
DATA, SEAL, SET = sys.argv[1], sys.argv[2], sys.argv[3]
FT = 0.0008


def main():
    key = {k['id']: k for k in json.load(open(f'{SEAL}/key.json'))}
    setups = json.load(open(SET)); rows = []
    for s in setups:
        k = key[s['id']]
        if s.get('side') in (None, 'none'):
            rows.append((s['id'], k['sym'], 'нет сетапа', None, None)); continue
        a = np.loadtxt(f"{DATA}/{k['sym']}_1h.csv", delimiter=',', skiprows=1); a = a[np.argsort(a[:, 0])]
        f = a[a[:, 0] >= k['T']]; sc = k['k']
        side = 1 if s['side'] == 'long' else -1
        e = s['entry'] / sc; st = s['stop'] / sc; tp = s['tp'] / sc; exp = int(s.get('expiry_h', 48))
        res = None; filled = None
        for j in range(min(len(f), exp)):
            hi, lo, op = f[j, 2], f[j, 3], f[j, 1]
            if (side == 1 and hi >= tp) or (side == -1 and lo <= tp):
                if not ((side == 1 and lo <= e) or (side == -1 and hi >= e)):
                    res = 'тейк до входа (нет сделки)'; break
            if (side == 1 and lo <= e) or (side == -1 and hi >= e):
                filled = j; break
        if filled is None:
            rows.append((s['id'], k['sym'], res or 'вход не исполнен', None, None)); continue
        risk = abs(e - st); R = None
        for j in range(filled, min(len(f), filled + 24 * 14)):
            hi, lo = f[j, 2], f[j, 3]
            if (side == 1 and lo <= st) or (side == -1 and hi >= st):
                R = (-risk - (e + st) * FT) / risk; why = 'стоп'; break
            if j > filled and ((side == 1 and hi >= tp) or (side == -1 and lo <= tp)):
                R = (abs(tp - e) - (e + tp) * FT) / risk; why = 'тейк'; break
        if R is None:
            px = f[min(len(f) - 1, filled + 24 * 14), 4]; R = (side * (px - e) - (e + px) * FT) / risk; why = 'выход через 14 дней'
        rows.append((s['id'], k['sym'], why, round(R, 2), round(abs(tp - e) / risk, 1)))
    print('| график | монета | итог | R | план RR |\n|---|---|---|---|---|')
    for r in rows:
        print(f'| {r[0]} | {r[1]} | {r[2]} | {"" if r[3] is None else f"{r[3]:+.2f}"} | {r[4] or ""} |')
    R = np.array([r[3] for r in rows if r[3] is not None])
    if len(R):
        print(f'\nСделок: {len(R)}, тейков {np.sum(R > 0)}, стопов {np.sum(R < 0)}, процент попаданий {np.mean(R > 0)*100:.0f}%, '
              f'средний R {R.mean():+.2f}, сумма {R.sum():+.1f}R')
        eq = 1.0
        for r in R:
            eq *= max(1 + r * 0.2, 0)
        print(f'Если каждая сделка = 20% депо на ликвидацию (как вы торгуете): счёт ×{eq:.2f}. При риске 1%: ×{np.prod(1 + R * 0.01):.3f}.')


if __name__ == '__main__':
    main()
