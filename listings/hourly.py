#!/usr/bin/env python3
"""Часовой разбор первых 30 дней каждого листинга: где хай, насколько падает, какие входы/тейки работают (шорт).
python listings/hourly.py listings_out/listings.csv listings_1h
"""
import csv, os, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'research'))
from fetch_vision import fetch_zip  # noqa: E402

M = 'https://data.binance.vision/data/futures/um/monthly/klines'
H = 3600000
COST = 0.0015
LIST, CACHE = sys.argv[1], sys.argv[2]
os.makedirs(CACHE, exist_ok=True)


def nxt(mo):
    y, m = map(int, mo.split('-'))
    return f'{y + (m == 12):04d}-{m % 12 + 1:02d}'


def get(row):
    s, mo = row['sym'], row['date'][:7]
    p = f'{CACHE}/{s}.csv'
    if not os.path.exists(p):
        rows = []
        for m in (mo, nxt(mo)):
            txt, _ = fetch_zip(f'{M}/{s}/1h/{s}-1h-{m}.zip')
            for line in (txt or '').splitlines():
                q = line.split(',')
                if q[0].strip().isdigit():
                    rows.append((int(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4])))
        with open(p, 'w', newline='') as f:
            csv.writer(f).writerows(sorted(set(rows)))
    with open(p) as f:
        k = [tuple(map(float, r)) for r in csv.reader(f) if r]
    return row, k[:30 * 24]


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))] if v else float('nan')


def sim(k, i, stop, tp, maxh=14 * 24):
    """Шорт с открытия бара i. Возвращает (R, исход). Стоп проверяется раньше тейка внутри бара."""
    e = k[i][1]; sd = (stop - e) / e
    tpp = e * (1 - tp) if tp else None
    end = min(len(k), i + maxh)
    for j in range(i, end):
        _, o, h, l, c = k[j]
        if h >= stop:
            x = max(o, stop) if j > i else stop
            return ((e - x) / e - 2 * COST) / sd, 'stop'
        if tpp and l <= tpp:
            x = min(o, tpp) if j > i else tpp
            return ((e - x) / e - 2 * COST) / sd, 'tp'
    x = k[end - 1][4]
    return ((e - x) / e - 2 * COST) / sd, 'time'


def entries(k, rule):
    """Индекс бара входа и стоп. Все признаки — только по закрытым барам до входа."""
    if len(k) < 48:
        return None
    p0 = k[0][1]
    if rule == 'день1':
        i = 24; hh = max(b[2] for b in k[:i])
        return i, hh * 1.02
    hh = 0
    for j in range(len(k) - 1):
        hh = max(hh, k[j][2])
        if j > 7 * 24:
            return None
        if rule == 'под открытие' and j >= 6 and k[j][4] < p0 and hh > p0 * 1.05:
            return j + 1, hh * 1.02
        if rule == 'слом 4H' and j >= 12 and (j + 1) % 4 == 0:
            c4 = k[j][4]; prev = k[j - 15:j - 3]
            if hh > p0 * 1.1 and c4 < min(b[3] for b in prev):
                return j + 1, hh * 1.02
        if rule == 'откат 25% от хая' and j >= 6 and hh > p0 * 1.1 and k[j][4] < hh * 0.75:
            return j + 1, hh * 1.02
    return None


def main():
    rows = list(csv.DictReader(open(LIST)))
    with ThreadPoolExecutor(32) as ex:
        data = list(ex.map(get, rows))
    data = [(r, k) for r, k in data if len(k) >= 48]
    out = [f'# Часовой разбор листингов: {len(data)} монет', '']
    groups = {'все': lambda r: True, 'новый не мем': lambda r: r['new'] == '1' and r['meme'] == '0',
              'мем': lambda r: r['meme'] == '1'}
    out += ['## Как ведёт себя цена от первой часовой цены листинга (P0), медиана [25%…75%]',
            '| группа | n | хай за 3 дня | час хая | лоу 30 дней от P0 | лоу 30 дней от хая | закрытие дня 30 от P0 |', '|---|---|---|---|---|---|---|']
    for g, f in groups.items():
        d = [(r, k) for r, k in data if f(r)]
        hi, hr, lo0, loh, c30 = [], [], [], [], []
        for r, k in d:
            p0 = k[0][1]; f3 = k[:72]; j = max(range(len(f3)), key=lambda x: f3[x][2])
            hi.append(f3[j][2] / p0 - 1); hr.append(j)
            lo = min(b[3] for b in k); lo0.append(lo / p0 - 1); loh.append(lo / f3[j][2] - 1); c30.append(k[-1][4] / p0 - 1)
        fm = lambda v, m=100: f'{pct(v, .5) * m:+.0f}% [{pct(v, .25) * m:+.0f}…{pct(v, .75) * m:+.0f}]'
        out.append(f'| {g} | {len(d)} | {fm(hi)} | {pct(hr, .5)}ч [{pct(hr, .25)}…{pct(hr, .75)}] | {fm(lo0)} | {fm(loh)} | {fm(c30)} |')
    out += ['', '## Шорт: вход × тейк (стоп над хаем после листинга +2%, держим до 14 дней; стоп > 40% — пропуск)',
            '| группа | вход | тейк | n | WR | PF | средн. R | ΣR | без топ-5 | плюс. лет | медиана стопа | позиция при риске 2% |',
            '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for g in ('все', 'новый не мем', 'мем'):
        for rule in ('день1', 'под открытие', 'слом 4H', 'откат 25% от хая'):
            for tp in (0.15, 0.25, 0.4, None):
                R, sds, yrs = [], [], defaultdict(float)
                for r, k in data:
                    if not groups[g](r) or r['holdout'] == '1':
                        continue
                    en = entries(k, rule)
                    if not en or en[0] >= len(k):
                        continue
                    i, st = en; sd = st / k[i][1] - 1
                    if sd <= 0.01 or sd > 0.4:
                        continue
                    x, _ = sim(k, i, st, tp); R.append(x); sds.append(sd); yrs[r['date'][:4]] += x
                if not R:
                    continue
                w = sum(x for x in R if x > 0); l = -sum(x for x in R if x <= 0)
                lev = 0.02 / pct(sds, .5)
                out.append(f"| {g} | {rule} | {'нет, 14д' if tp is None else f'-{int(tp * 100)}%'} | {len(R)} | "
                           f"{round(100 * sum(x > 0 for x in R) / len(R))}% | {w / l if l else 99:.2f} | {sum(R) / len(R):+.2f} | "
                           f"{sum(R):+.1f} | {sum(sorted(R)[:-5]):+.1f} | {sum(v > 0 for v in yrs.values())}/{len(yrs)} | "
                           f"{pct(sds, .5) * 100:.0f}% | {lev * 100:.0f}% депо |")
    open(f'{CACHE}/REPORT.md', 'w').write('\n'.join(out) + '\n')
    print('\n'.join(out))


if __name__ == '__main__':
    main()
