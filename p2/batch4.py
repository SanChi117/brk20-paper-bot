#!/usr/bin/env python3
"""Пакет 4 (BTC и ETH, 1h, 2021-10 – 2026-10). См. p2/PREREG.md, раздел «Пакет 4».
CME-гэп: цена «пятничного закрытия» = закрытие часа, кончающегося в пт 21:00 UTC; «открытие CME» = вс 23:00 UTC.
  Гэп = открытие / пятничное закрытие − 1. При |гэп| ≥ G: вход в вс 23:00 к пятничной цене, тейк = пятничная цена,
  стоп = 2×гэп в другую сторону, выход не позже пт 21:00. Контроль: уровень на том же расстоянии В СТОРОНУ гэпа
  (вероятность «дойти» туда — то, что было бы без особого «закрытия гэпа»).
Экспирация опционов: последняя пятница месяца 08:00 UTC; доходность чт 08:00→пт 08:00 и пт 08:00→сб 08:00
  против тех же окон в остальные пятницы. Издержки 0.08% на сторону."""
import csv, sys, datetime as dt
import numpy as np
H = 3600 * 1000


def load(dd, s):
    t, o, h, l, c = [], [], [], [], []
    for r in csv.DictReader(open(f'{dd}/{s}_1h.csv')):
        t.append(int(r['t'])); o.append(float(r['o'])); h.append(float(r['h'])); l.append(float(r['l'])); c.append(float(r['c']))
    return np.array(t), np.array(o), np.array(h), np.array(l), np.array(c)


def ci(x):
    x = np.asarray(x)
    if len(x) < 5:
        return f'n={len(x)}'
    bs = np.random.default_rng(1).choice(x, (2000, len(x))).mean(1); lo, hi = np.percentile(bs, [2.5, 97.5])
    return f'{x.mean() * 100:+.2f}% [{lo * 100:+.2f}; {hi * 100:+.2f}] n={len(x)}, в плюс {np.mean(x > 0) * 100:.0f}%'


def main():
    dd = sys.argv[1]
    for s in ('BTCUSDT', 'ETHUSDT'):
        t, o, h, l, c = load(dd, s); idx = {int(x): i for i, x in enumerate(t)}
        print(f'\n# {s}')
        fri = [i for i, x in enumerate(t) if dt.datetime.fromtimestamp((x + H) / 1000, dt.timezone.utc).strftime('%a %H') == 'Fri 21']
        for G in (0.005, 0.01, 0.02):
            fills, ctrl, pnl, yrs = [], [], [], []
            for i in fri:
                tf = int(t[i]) + H; pf = c[i]
                j = idx.get(tf + 50 * H - H)               # бар, открывшийся в вс 23:00
                k_end = idx.get(tf + 7 * 24 * H - H)
                if j is None or k_end is None:
                    continue
                en = o[j]; gap = en / pf - 1
                if abs(gap) < G:
                    continue
                sd = -1 if gap > 0 else 1                  # к пятничной цене
                tp = pf; st = en * (1 - sd * 2 * abs(gap)); lvl_c = en * (1 + np.sign(gap) * abs(gap))
                hit_tp = hit_c = False; x = None
                for k in range(j, k_end + 1):
                    if (sd == 1 and l[k] <= st) or (sd == -1 and h[k] >= st):
                        x = st if x is None else x
                    if (sd == 1 and h[k] >= tp) or (sd == -1 and l[k] <= tp):
                        hit_tp = True; x = tp if x is None else x
                    if (gap > 0 and h[k] >= lvl_c) or (gap < 0 and l[k] <= lvl_c):
                        hit_c = True
                    if x is not None and hit_c:
                        break
                if x is None:
                    x = c[k_end]
                fills.append(hit_tp); ctrl.append(hit_c); pnl.append(sd * (x / en - 1) - 0.0016)
                yrs.append(dt.datetime.fromtimestamp(tf / 1000, dt.timezone.utc).year)
            if not pnl:
                continue
            print(f'## CME-гэп ≥{G * 100:.1f}%: недель {len(pnl)}; гэп закрылся за неделю {np.mean(fills) * 100:.0f}%, '
                  f'уровень на том же расстоянии в сторону гэпа достигнут {np.mean(ctrl) * 100:.0f}%')
            print(f'- сделка к пятничной цене (тейк у неё, стоп 2× гэп): {ci(pnl)}')
            pn = np.array(pnl); yy = np.array(yrs)
            print('- по годам: ' + ', '.join(f'{y}: {pn[yy == y].mean() * 100:+.2f}% (n={int((yy == y).sum())})' for y in sorted(set(yrs))))
        # экспирация
        pre_e, post_e, pre_o, post_o = [], [], [], []
        for i, x in enumerate(t):
            d = dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc)
            if d.strftime('%a %H') != 'Fri 08':
                continue
            last = (d + dt.timedelta(days=7)).month != d.month
            a, b = idx.get(int(x) - 24 * H), idx.get(int(x) + 24 * H)
            if a is None or b is None:
                continue
            pre = o[i] / o[a] - 1; post = o[b] / o[i] - 1
            (pre_e if last else pre_o).append(pre); (post_e if last else post_o).append(post)
        print('## Экспирация опционов (последняя пятница месяца 08:00 UTC), без издержек')
        print(f'- сутки ДО: в день экспирации {ci(pre_e)} | в остальные пятницы {ci(pre_o)}')
        print(f'- сутки ПОСЛЕ: в день экспирации {ci(post_e)} | в остальные пятницы {ci(post_o)}')
        print(f'- модуль движения суток после: экспирация {np.mean(np.abs(post_e)) * 100:.2f}% против {np.mean(np.abs(post_o)) * 100:.2f}%')


if __name__ == '__main__':
    main()
