#!/usr/bin/env python3
"""Реакция фьючерсов USDT-M на объявления Binance (2021-06 – 2026-11).
S: «Binance Will List X» (спот) для монет, у которых фьючерс уже торгуется; M: метка мониторинга; D: делистинг со спота.
Вход через 5 минут после публикации (открытие 1m-бара), выходы через 1 ч, 4 ч, 24 ч, 3 и 7 дней.
Также затухание: вход через 1 ч, выход через 24 ч / 3 дня. Издержки 0.08% на сторону (+0.10% проскальзывание в строгом варианте).
Разметка: dev — до 2024-06, проверка — 2024-06…2026-05, holdout — с 2026-06."""
import io, json, re, urllib.request, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np
V = 'https://data.binance.vision/data/futures/um'
LIST = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/daily/klines/'
EV = json.load(open('events.json'))
DEV_END = dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000
HOLD = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000
out, marker = [], ''
while True:
    x = urllib.request.urlopen(LIST + (f'&marker={marker}' if marker else ''), timeout=60).read().decode()
    ps = re.findall(r'<Prefix>data/futures/um/daily/klines/([^/<]+)/</Prefix>', x); out += ps
    if '<IsTruncated>true' not in x or not ps:
        break
    marker = f'data/futures/um/daily/klines/{ps[-1]}/'
PERPS = set(out)
print('perps known', len(PERPS))


def resolve(b):
    for p in ('', '1000', '10000', '1000000', '1M'):
        if p + b + 'USDT' in PERPS:
            return p + b + 'USDT'
    return None


def rows(url):
    try:
        data = urllib.request.urlopen(url, timeout=60).read()
    except Exception:
        return []
    z = zipfile.ZipFile(io.BytesIO(data)); r = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        q = line.split(',')
        if q and q[0].strip().isdigit():
            r.append((int(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[7])))
    return r


def day(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date()


def job(item):
    k, t, sym = item
    s = resolve(sym)
    if not s:
        return None
    d0 = day(t)
    m1 = []
    for dd in range(-1, 2):
        m1 += rows(f'{V}/daily/klines/{s}/1m/{s}-1m-{d0 + dt.timedelta(days=dd)}.zip')
    if not m1:
        return None
    h1 = []
    for dd in range(0, 9):
        h1 += rows(f'{V}/daily/klines/{s}/1h/{s}-1h-{d0 + dt.timedelta(days=dd)}.zip')
    m1 = sorted(set(m1)); h1 = sorted(set(h1))
    tm = np.array([r[0] for r in m1]); om = np.array([r[1] for r in m1]); cm = np.array([r[4] for r in m1])
    i0 = np.searchsorted(tm, t + 5 * 60000)
    if i0 >= len(tm) or tm[i0] - (t + 5 * 60000) > 5 * 60000 or tm[0] > t - 60 * 60000:
        return None
    pre = np.searchsorted(tm, t - 60 * 60000)
    e5 = om[i0]; p_pre = cm[max(0, pre - 1)]; p_t = om[np.searchsorted(tm, t)] if np.searchsorted(tm, t) < len(tm) else e5
    th = np.array([r[0] for r in h1]); ch = np.array([r[4] for r in h1])

    def px_at(T):
        if T <= tm[-1]:
            j = np.searchsorted(tm, T)
            return om[j] if j < len(om) else None
        j = np.searchsorted(th, T)
        return ch[j - 1] if 0 < j <= len(ch) else None
    r = dict(k=k, t=t, sym=sym, s=s, pre=(p_t / p_pre - 1) if p_pre else None, jump=e5 / p_t - 1, qv=float(np.mean([x[5] for x in m1])) * 1440)
    for lab, dtm in (('1h', 1), ('4h', 4), ('24h', 24), ('3d', 72), ('7d', 168)):
        p = px_at(t + 5 * 60000 + dtm * 3600000); r[lab] = (p / e5 - 1) if p else None
    e1 = px_at(t + 3600000)
    for lab, dtm in (('f24h', 24), ('f3d', 72)):
        p = px_at(t + 3600000 + dtm * 3600000); r[lab] = (p / e1 - 1) if (p and e1) else None
    return r


items = [(e['k'], e['t'], s) for e in EV for s in e['syms']]
with ThreadPoolExecutor(16) as ex:
    R = [r for r in ex.map(job, items) if r]
json.dump(R, open('ann_res.json', 'w'))
print('events with perp data', len(R))


def ci(x):
    x = np.asarray([v for v in x if v is not None])
    if len(x) < 5:
        return f'n={len(x)}'
    bs = np.random.default_rng(1).choice(x, (2000, len(x))).mean(1); lo, hi = np.percentile(bs, [2.5, 97.5])
    return f'{x.mean() * 100:+.2f}% [{lo * 100:+.2f}; {hi * 100:+.2f}] n={len(x)}, в плюс {np.mean(x > 0) * 100:.0f}%'


names = {'S': 'листинг на споте (лонг)', 'M': 'метка мониторинга (шорт)', 'D': 'делистинг со спота (шорт)'}
for k in 'SMD':
    rs = [r for r in R if r['k'] == k]
    sd = 1 if k == 'S' else -1
    print(f'\n## {names[k]}: событий с фьючерсом {len(rs)}')
    print(f"- движение за час ДО публикации: {ci([r['pre'] for r in rs])}")
    print(f"- скачок за первые 5 минут (не доступен боту): {ci([r['jump'] for r in rs])}")
    for per, a, b in (('dev до 06.2024', 0, DEV_END), ('проверка 06.2024–05.2026', DEV_END, HOLD), ('holdout с 06.2026', HOLD, 1e15)):
        sub = [r for r in rs if a <= r['t'] < b]
        print(f'### {per} ({len(sub)})')
        for lab in ('1h', '4h', '24h', '3d', '7d'):
            print(f"- вход +5 мин, выход {lab}: {ci([sd * r[lab] - 0.0016 for r in sub if r[lab] is not None])}")
        for lab in ('f24h', 'f3d'):
            print(f"- против движения: вход +1 ч, выход {lab[1:]}: {ci([-sd * r[lab] - 0.0016 for r in sub if r[lab] is not None])}")

print('\n## Детально: метка мониторинга, шорт +5 мин → 24 ч')
ms = sorted([r for r in R if r['k'] == 'M' and r['24h'] is not None], key=lambda r: r['t'])
grp = {}
for r in ms:
    grp.setdefault(r['t'], []).append(-r['24h'] - 0.0016)
for t, v in grp.items():
    print(f"{dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc):%Y-%m-%d %H:%M} монет {len(v)}: среднее {np.mean(v) * 100:+.1f}%, худшая {min(v) * 100:+.1f}%, лучшая {max(v) * 100:+.1f}%")
g = np.array([np.mean(v) for v in grp.values()])
print(f'по объявлениям: {len(g)}, среднее {g.mean() * 100:+.2f}%, в плюс {np.mean(g > 0) * 100:.0f}%')
allv = np.array([-r['24h'] - 0.0016 for r in ms])
print(f'все сделки: {len(allv)}, худшая {allv.min() * 100:+.1f}%, 5% худших в среднем {np.sort(allv)[:max(1, len(allv) // 20)].mean() * 100:+.1f}%; с проскальзыванием +0.2%: {(allv - 0.002).mean() * 100:+.2f}%')
print('объём фьючерса в сутки, медиана $', round(float(np.median([r['qv'] for r in ms]))))
