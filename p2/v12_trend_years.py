#!/usr/bin/env python3
"""Разведка после v12_improve: сделки v1.2 по годам/полугодиям, по тренду дня (за/против), по группам монет."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v12_improve as V  # noqa: E402


def main():
    V.DIRS = sys.argv[1:3]; V.ls.DATA = V.DIRS[0]; V.REG = V.ls.btc_regime(V.ls.load('BTCUSDT'))
    tr, _ = V.collect(V.DIRS, 1, 1.0)
    L = ['# v1.2: сделки за и против дневного тренда (издержки ×1)', '',
         '| период | группа | за тренд: n / ср.R / ΣR / ΣR без топ-5 | против: n / ср.R / ΣR / ΣR без топ-5 |', '|---|---|---|---|']
    def cell(R):
        R = np.array(R)
        if not len(R):
            return '0'
        return f'{len(R)} / {R.mean():+.2f} / {R.sum():+.0f} / {R.sum() - np.sort(R)[::-1][:5].sum():+.0f}'
    for x in tr:
        dt = np.datetime64(x['t'], 'ms').astype(object)
        x['per'] = f'{dt.year}-{"H1" if dt.month <= 6 else "H2"}'
    for per in sorted({x['per'] for x in tr}):
        for g in 'AB':
            sub = [x for x in tr if x['per'] == per and x['grp'] == g]
            L.append(f'| {per} | {g} | ' + cell([x['R'] for x in sub if x['dtr'] > 0]) + ' | ' + cell([x['R'] for x in sub if x['dtr'] < 0]) + ' |')
    ho = [x for x in tr if x['t'] >= V.HOLD]
    L += ['', 'Holdout июнь–окт 2026 по месяцам (против тренда): '
          + ', '.join(f'{m}: {cell([x["R"] for x in ho if x["dtr"] < 0 and str(np.datetime64(x["t"], "ms").astype("datetime64[M]")) == m])}'
                      for m in sorted({str(np.datetime64(x['t'], 'ms').astype('datetime64[M]')) for x in ho}))]
    L += ['', 'Топ-10 сделок holdout против тренда: ' + ', '.join(f'{x["sym"]} {"L" if x["s"] > 0 else "S"} {x["R"]:+.1f}R'
          for x in sorted([x for x in ho if x['dtr'] < 0], key=lambda y: -y['R'])[:10])]
    print('\n'.join(L))


if __name__ == '__main__':
    main()
