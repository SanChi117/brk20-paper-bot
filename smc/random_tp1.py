#!/usr/bin/env python3
"""Проверка: случайный вход, тейк +1% цены, разные стопы. 15m, 24 монеты, 2022–2026.
Если тейк и стоп задеты в одной 15m свече — порядок неизвестен: засчитываем по 0.5 (честно, без перекоса)."""
import glob, sys
import numpy as np
FT, FM, SLP = 0.0005, 0.0002, 0.0003
TP = 0.01; MAXB = 96 * 7    # не дольше 7 дней


def main():
    rng = np.random.default_rng(42)
    files = sorted(glob.glob(f'{sys.argv[1]}/*_15m.csv'))
    res = {sl: [] for sl in (0.005, 0.01, 0.02, 0.03, 0.05, 0.10)}
    for p in files:
        a = np.loadtxt(p, delimiter=',', skiprows=1); a = a[np.argsort(a[:, 0])]
        o, h, l, c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        t = a[:, 0]; ok = t >= 1640995200000
        idx = np.nonzero(ok)[0]; idx = idx[idx < len(c) - MAXB - 1]
        picks = rng.choice(idx, size=min(3000, len(idx)), replace=False)
        for i in picks:
            s = 1 if rng.random() < 0.5 else -1
            en = o[i + 1]
            for sl in res:
                tp_px = en * (1 + s * TP); st_px = en * (1 - s * sl)
                out = None
                for j in range(i + 1, i + 1 + MAXB):
                    hit_tp = h[j] >= tp_px if s == 1 else l[j] <= tp_px
                    hit_sl = l[j] <= st_px if s == 1 else h[j] >= st_px
                    if hit_tp and hit_sl:
                        out = 0.5; break
                    if hit_tp:
                        out = 1.0; break
                    if hit_sl:
                        out = 0.0; break
                if out is None:
                    px = c[i + MAXB]; r = s * (px - en) / en - 2 * (FT + SLP); res[sl].append((0.5, r)); continue
                gain = TP - (FT + SLP) - FM
                loss = -sl - 2 * (FT + SLP)
                res[sl].append((out, out * gain + (1 - out) * loss))
    print('| стоп | доля сделок, где +1% раньше стопа | средний итог сделки (от цены, после комиссий) | то же в % маржи при 20x | сделок |')
    print('|---|---|---|---|---|')
    for sl, v in res.items():
        w = np.mean([x[0] for x in v]); r = np.mean([x[1] for x in v])
        print(f'| {sl*100:.1f}% | {w*100:.0f}% | {r*100:+.3f}% | {r*2000:+.2f}% | {len(v)} |')


if __name__ == '__main__':
    main()
