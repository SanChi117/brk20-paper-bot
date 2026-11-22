#!/usr/bin/env python3
"""Проверка устойчивости пробоя канала: сетка параметров, без 2020, динамический топ по объёму без выжившего."""
import datetime as dt, glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import run, stats, equity, load, HOLD, D  # noqa: E402

Y21 = int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def pf(tr):
    R = np.array([x['R'] for x in tr]) if tr else np.array([0.0])
    ls = -R[R < 0].sum()
    return (R[R > 0].sum() / ls) if ls else float('inf'), R.sum(), len(tr)


def yrs(tr):
    out = []
    for y in range(2020, 2027):
        a = int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000); b = int(dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        out.append(sum(x['R'] for x in tr if a <= x['t_entry'] < b))
    return out


def main():
    core, alld = sys.argv[1], sys.argv[2]
    L = ['# Устойчивость пробоя канала', '', '## Сетка параметров, BTC+ETH, лонг+шорт (вход N / выход N/2)',
         '| N | сделок | PF всё | ΣR всё | PF с 2021 | ΣR с 2021 | ΣR по годам 2020…2026 |', '|---|---|---|---|---|---|---|']
    A = {s: load(core, s) for s in ('BTCUSDT', 'ETHUSDT')}
    for N in (10, 15, 20, 30, 40, 55, 80):
        tr = [x for s in A for x in run(*A[s], N, max(5, N // 2))]
        p1, r1, n1 = pf(tr); p2, r2, _ = pf([x for x in tr if x['t_entry'] >= Y21])
        L.append(f'| {N} | {n1} | {p1:.2f} | {r1:+.0f} | {p2:.2f} | {r2:+.0f} | ' + ' '.join(f'{v:+.0f}' for v in yrs(tr)) + ' |')
    # динамическая вселенная: каждый день топ-K по среднему обороту 30 дней среди всех USDT-M (вкл. делистинг)
    S = {}
    for path in glob.glob(f'{alld}/*_1d.csv'):
        s = os.path.basename(path)[:-7]
        try:
            a = np.loadtxt(path, delimiter=',', skiprows=1, ndmin=2)
            f = np.loadtxt(f'{alld}/{s}_funding.csv', delimiter=',', skiprows=1, ndmin=2)
        except Exception:
            continue
        if len(a) < 120:
            continue
        S[s] = (a, f)
    days = np.unique(np.concatenate([S[s][0][:, 0] for s in S])).astype(np.int64)
    idx = {d: i for i, d in enumerate(days)}
    QV = np.full((len(S), len(days)), np.nan); names = list(S)
    for k, s in enumerate(names):
        a = S[s][0]; qv = np.convolve(a[:, 5], np.ones(30) / 30)[:len(a)]; qv[:120] = np.nan  # ≥120 дней истории
        QV[k, [idx[int(t)] for t in a[:, 0]]] = qv
    bt = S['BTCUSDT'][0]; bc = bt[:, 4]
    sma = np.convolve(bc, np.ones(200) / 200)[:len(bc)]
    reg = np.where(np.arange(len(bc)) >= 199, np.where(bc > sma, 1, -1), 0); treg = bt[:, 0].astype(np.int64) + D
    L += ['', '## Динамический топ по обороту (все USDT-M, включая делистинг; без выбора задним числом)',
          '| вселенная | N | режим | сделок | WR | avgR | PF | ΣR | maxDD R | ΣR без топ-5 | PF с 2021 | ×кап. 1% / DD | ΣR по годам |', '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for K in (10, 20):
        rank = np.argsort(np.argsort(-np.nan_to_num(QV, nan=-1), axis=0), axis=0)
        elig = (rank < K) & ~np.isnan(QV)
        for N in (20, 55):
            for use_reg in (False, True):
                tr = []
                for k, s in enumerate(names):
                    a, f = S[s]
                    allow = elig[k, [idx[int(t)] for t in a[:, 0]]]
                    kw = dict(regime=reg, t_reg=treg) if use_reg else {}
                    tr += run(a, f, N, N // 2, allow=allow, **kw)
                dev = [x for x in tr if x['t_entry'] < HOLD]
                p2 = pf([x for x in dev if x['t_entry'] >= Y21])[0]
                eq, mdd = equity(dev, 0.01)
                L.append(f'| топ-{K} | {N} | {"да" if use_reg else "нет"} ' + stats(dev)[:-1] + f'| {p2:.2f} | ×{eq:.1f} / {mdd*100:.0f}% | '
                         + ' '.join(f'{v:+.0f}' for v in yrs(tr)) + ' |')
                h = [x for x in tr if x['t_entry'] >= HOLD]
                L.append(f'| ↳ holdout | {N} | {"да" if use_reg else "нет"} ' + stats(h)[:-1] + '| – | – | – |')
    os.makedirs('turtle_out', exist_ok=True)
    open('turtle_out/ROBUST.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
