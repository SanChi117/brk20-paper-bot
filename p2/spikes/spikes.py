#!/usr/bin/env python3
"""Сигналы в стиле уведомлений Binance по 5m-свечам, 54 монеты, 2023-01 – 2026-10.
Рост: 5m-свеча закрылась ≥ X% выше открытия; падение: ≤ −X%; разворот вверх: цена упала ≥ 5% от максимума 2 ч и 5m-свеча ≥ +2%;
разворот вниз — зеркально. Вход на открытии следующей 5m-свечи. Выходы через 15 мин, 1 ч, 4 ч, 24 ч.
Оба направления (по движению / против). Издержки 0.08% на сторону. Пауза по монете 1 ч.
Периоды: dev 2023-01–2024-05, проверка 2024-06–2026-05, holdout 2026-06–2026-10."""
import io, urllib.request, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np
V = 'https://data.binance.vision/data/futures/um/monthly/klines'
SYMS = ('BTC ETH SOL BNB XRP DOGE ADA AVAX LINK DOT LTC BCH NEAR ATOM FIL ETC TRX UNI AAVE APT ARB OP INJ SUI XLM EOS ALGO SAND MANA '
        'AXS CRV SNX COMP GRT ICP EGLD HBAR VET THETA RUNE DYDX GALA CHZ KAVA 1INCH IMX LDO STX FET WLD SEI TIA ORDI').split()
MONTHS = [f'{y}-{m:02d}' for y in range(2023, 2027) for m in range(1, 13) if f'{y}-{m:02d}' <= '2026-10']
DEV = dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000
HOLD = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000


def rows(sym, mo):
    s = sym + 'USDT'
    try:
        data = urllib.request.urlopen(f'{V}/{s}/5m/{s}-5m-{mo}.zip', timeout=60).read()
    except Exception:
        return []
    z = zipfile.ZipFile(io.BytesIO(data)); r = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        q = line.split(',')
        if q and q[0].strip().isdigit():
            tt = int(q[0])
            if tt > 10 ** 14:
                tt //= 1000                      # микросекунды -> миллисекунды
            r.append((tt, float(q[1]), float(q[2]), float(q[3]), float(q[4])))
    return r


def load(sym):
    r = []
    for mo in MONTHS:
        r += rows(sym, mo)
    r = sorted(set(r))
    return sym, np.array(r) if r else None


with ThreadPoolExecutor(16) as ex:
    D = {s: a for s, a in ex.map(load, SYMS) if a is not None and len(a) > 1000}
print('монет', len(D))
for s_, a_ in list(D.items())[:3]:
    d = np.diff(a_[:, 0]); print(s_, 'шаг 5m доля', round(float(np.mean(d == 300000)), 4), 'разрывов', int(np.sum(d != 300000)), 'макс t', int(a_[-1, 0]))
HZ = {'15m': 3, '1h': 12, '4h': 48, '24h': 288}
res = {}
for s, a in D.items():
    t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    r5 = c / o - 1
    hi2 = np.array([0.0] * len(c))
    from numpy.lib.stride_tricks import sliding_window_view
    w = sliding_window_view(h, 24); hi2[24:] = w[:-1].max(axis=1); hi2[:24] = np.nan
    lw = sliding_window_view(l, 24); lo2 = np.full(len(c), np.nan); lo2[24:] = lw[:-1].min(axis=1)
    sig = {}
    for X in (0.03, 0.05):
        sig[f'рост ≥{X * 100:.0f}% за 5 мин'] = (r5 >= X, 1)
        sig[f'падение ≥{X * 100:.0f}% за 5 мин'] = (r5 <= -X, -1)
    sig['разворот вверх (−5% от хая 2ч, свеча +2%)'] = ((o / hi2 - 1 <= -0.05) & (r5 >= 0.02), 1)
    sig['разворот вниз (+5% от лоя 2ч, свеча −2%)'] = ((o / lo2 - 1 >= 0.05) & (r5 <= -0.02), -1)
    for name, (mask, sd) in sig.items():
        idx = np.where(mask)[0]; last = -10 ** 9
        for i in idx:
            if i - last < 12 or i + 289 >= len(c):
                continue
            if t[i + 1] - t[i] != 300000:
                continue
            en = o[i + 1]; last = i
            for hz, k in HZ.items():
                if t[i + k] != t[i] + k * 300000:
                    continue
                ex_ = c[i + k]
                res.setdefault((name, hz), []).append((t[i], sd * (ex_ / en - 1)))
    rng = np.random.default_rng(7)
    for i in rng.integers(300, len(c) - 300, 400):
        for hz, k in HZ.items():
            if t[i + k] == t[i] + k * 300000 and t[i + 1] == t[i] + 300000:
                res.setdefault(('КОНТРОЛЬ: случайный момент', hz), []).append((t[i], c[i + k] / o[i + 1] - 1))


def ci(x):
    x = np.asarray(x)
    if len(x) < 10:
        return f'n={len(x)}'
    bs = np.random.default_rng(1).choice(x, (2000, len(x))).mean(1); lo, hi = np.percentile(bs, [2.5, 97.5])
    return f'{x.mean() * 100:+.2f}% [{lo * 100:+.2f}; {hi * 100:+.2f}] n={len(x)}'


names = sorted({k[0] for k in res})
for name in names:
    print(f'\n## {name}')
    for hz in HZ:
        v = res.get((name, hz), [])
        for lab, direc in (('по движению', 1), ('против движения', -1)):
            parts = []
            for per, a_, b_ in (('dev', 0, DEV), ('проверка', DEV, HOLD), ('holdout', HOLD, 1e15)):
                x = [direc * r - 0.0016 for tt, r in v if a_ <= tt < b_]
                parts.append(f'{per} {ci(x)}')
            print(f'- {hz}, {lab}: ' + ' | '.join(parts))
