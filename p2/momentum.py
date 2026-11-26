#!/usr/bin/env python3
"""Кросс-секционный моментум (ротация), 54 монеты USDT-M. См. p2/PREREG.md, раздел «Моментум-ротация».
Каждый понедельник 00:00 UTC: доходность каждой монеты за L недель (по закрытиям 1h -> дневные 00:00 UTC);
лонг топ-K, шорт низ-K, равные веса (по 1/(2K) капитала на позицию), держим H недель (H>1 — перекрывающиеся корзины).
Издержки 0.08% на сторону от оборота, фандинг начисляется по факту (лонг платит положительный, шорт получает).
Монета участвует, если у неё есть полная история за L недель и следующую неделю торгов."""
import glob, os, sys, csv, datetime as dt
import numpy as np
COST = 0.0008
W = 7 * 24 * 3600 * 1000
HOLD = int(np.datetime64('2026-06-01T00:00:00', 'ms').astype('int64'))


def load(dd, s):
    t, c = [], []
    with open(f'{dd}/{s}_1h.csv') as f:
        for r in csv.DictReader(f):
            t.append(int(r['t'])); c.append(float(r['c']))
    fund = []
    p = f'{dd}/{s}_funding.csv'
    if os.path.exists(p):
        with open(p) as f:
            for r in csv.DictReader(f):
                fund.append((int(r['t']), float(r['rate'])))
    return dict(zip(t, c)), sorted(fund)


def main():
    U = {}
    for dd, grp in ((sys.argv[1], 'A'), (sys.argv[2], 'B')):
        for p in glob.glob(f'{dd}/*_1h.csv'):
            s = os.path.basename(p)[:-7]
            if s in U:
                continue
            px, fu = load(dd, s); U[s] = (grp, px, np.array([x[0] for x in fu]), np.array([x[1] for x in fu]))
    t0 = int(np.datetime64('2021-11-01T00:00:00', 'ms').astype('int64'))
    while dt.datetime.utcfromtimestamp(t0 / 1000).weekday() != 0:
        t0 += 24 * 3600 * 1000
    tend = max(max(v[1]) for v in U.values())
    mondays = list(range(t0, tend - W, W))

    def px(s, t):
        return U[s][1].get(t - 3600 * 1000)          # закрытие часа, закончившегося в t (бар открыт в t-1h)

    def fund(s, a, b):
        ft, fr = U[s][2], U[s][3]
        i, j = np.searchsorted(ft, a), np.searchsorted(ft, b)
        return float(fr[i:j].sum())

    def week_ret(s, t, side):
        a, b = px(s, t), px(s, t + W)
        if not a or not b:
            return None
        return side * (b / a - 1) - side * fund(s, t, t + W)

    def run(L, K, H, grp=None, long_only=False, btc_filter=False):
        rets, prev = [], {}
        books = []                                      # перекрывающиеся корзины
        for t in mondays:
            if t - L * W < t0 - 0:
                pass
            cand = []
            for s, v in U.items():
                if grp and v[0] != grp:
                    continue
                a, b = px(s, t - L * W), px(s, t)
                if a and b and px(s, t + W):
                    cand.append((b / a - 1, s))
            if len(cand) < 2 * K + 2:
                rets.append((t, 0.0)); continue
            cand.sort()
            longs = [s for _, s in cand[-K:]]; shorts = [] if long_only else [s for _, s in cand[:K]]
            if btc_filter and long_only:
                b1, b0 = px('BTCUSDT', t), px('BTCUSDT', t - 7 * W)
                if b1 and b0 and b1 < b0:
                    longs = []
            book = {s: 1.0 for s in longs}; book.update({s: -1.0 for s in shorts})
            books.append(book); books = books[-H:]
            # вес позиции: 1/(число позиций в корзине) / H
            w = {}
            for bk in books:
                n = max(1, len(bk))
                for s, sd in bk.items():
                    w[s] = w.get(s, 0) + sd / n / H
            r = 0.0
            for s, ww in w.items():
                wr = week_ret(s, t, 1 if ww > 0 else -1)
                if wr is not None:
                    r += abs(ww) * wr
            turn = sum(abs(w.get(s, 0) - prev.get(s, 0)) for s in set(w) | set(prev))
            r -= turn * COST
            prev = w
            rets.append((t, r))
        return rets

    def summ(rets, a=None, b=None):
        x = np.array([r for t, r in rets if (a is None or t >= a) and (b is None or t < b)])
        if len(x) < 4:
            return '| — |'
        eq = np.cumprod(1 + x); dd = 1 - eq / np.maximum.accumulate(eq)
        bs = np.random.default_rng(1).choice(x, (2000, len(x))).mean(1)
        lo, hi = np.percentile(bs, [2.5, 97.5])
        yrs = len(x) / 52
        return (f'| {len(x)} | {x.mean() * 100:+.2f}% [{lo * 100:+.2f}; {hi * 100:+.2f}] | {(x > 0).mean() * 100:.0f}% | '
                f'×{eq[-1]:.2f} | {(eq[-1] ** (1 / yrs) - 1) * 100:+.0f}% | {dd.max() * 100:.0f}% | '
                f'{x.mean() / x.std() * np.sqrt(52):.2f} |')

    VS = [('ОСНОВНОЙ: 4 нед., топ/низ 5, держать 1 нед.', dict(L=4, K=5, H=1)),
          ('2 нед., 5/5, 1 нед.', dict(L=2, K=5, H=1)),
          ('1 нед., 5/5, 1 нед.', dict(L=1, K=5, H=1)),
          ('8 нед., 5/5, 1 нед.', dict(L=8, K=5, H=1)),
          ('12 нед., 5/5, 1 нед.', dict(L=12, K=5, H=1)),
          ('4 нед., 10/10, 1 нед.', dict(L=4, K=10, H=1)),
          ('4 нед., 5/5, держать 4 нед. (перекрытие)', dict(L=4, K=5, H=4)),
          ('только лонг: 4 нед., топ-5', dict(L=4, K=5, H=1, long_only=True)),
          ('только лонг + BTC выше 7 нед. назад', dict(L=4, K=5, H=1, long_only=True, btc_filter=True))]
    L_ = ['# Моментум-ротация (54 монеты, недельная)', '',
          'Издержки 0.08% на сторону от оборота, фандинг учтён. Доходность — на весь капитал за неделю (без плеча).', '',
          '| вариант | выборка | недель | средн. за неделю [95% ДИ] | недель в плюсе | капитал | в год | макс. просадка | Шарп |',
          '|---|---|---|---|---|---|---|---|---|']
    years = []
    for name, V in VS:
        ra = run(**V)
        L_.append(f'| {name} | все монеты, до июня 2026 ' + summ(ra, None, HOLD))
        L_.append(f'| {name} | 24 монеты, до июня 2026 ' + summ(run(**V, grp='A'), None, HOLD))
        L_.append(f'| {name} | 30 монет, до июня 2026 ' + summ(run(**V, grp='B'), None, HOLD))
        L_.append(f'| {name} | все, июнь–окт 2026 ' + summ(ra, HOLD, None))
        yr = {}
        for t, r in ra:
            yr.setdefault(dt.datetime.utcfromtimestamp(t / 1000).year, []).append(r)
        years.append(f'- {name}: ' + ', '.join(f'{y}: {(np.prod(1 + np.array(v)) - 1) * 100:+.0f}%' for y, v in sorted(yr.items())))
    L_ += ['', '## По годам (все монеты)'] + years
    os.makedirs('p2_out', exist_ok=True); open('p2_out/MOMENTUM.md', 'w').write('\n'.join(L_) + '\n'); print('\n'.join(L_))


if __name__ == '__main__':
    main()
