#!/usr/bin/env python3
"""Проверка крупнейших сделок holdout: цены входа/выхода и свечи (ловим битые данные)."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v12_improve as V  # noqa: E402


def main():
    V.DIRS = sys.argv[1:3]; V.ls.DATA = V.DIRS[0]; V.REG = V.ls.btc_regime(V.ls.load('BTCUSDT'))
    tr, _ = V.collect(V.DIRS, 1, 1.0)
    top = sorted(tr, key=lambda y: -y['R'])[:12]
    for x in top:
        dd = V.DIRS[0] if x['grp'] == 'A' else V.DIRS[1]; V.ls.DATA = dd; d = V.ls.load(x['sym'])
        t = d['t'].astype(np.int64); j0 = int(np.searchsorted(t, x['t'])); j1 = int(np.searchsorted(t, x['tx'] - V.H))
        seg = slice(j0, j1 + 1)
        print(f"{x['sym']} {'L' if x['s'] > 0 else 'S'} R={x['R']:+.1f} stop%={x['sp']*100:.2f} out={x['out']} "
              f"{np.datetime64(x['t'], 'ms')} -> {np.datetime64(x['tx'], 'ms')} bars={j1-j0+1} "
              f"open={d['o'][j0]:.6g} max={d['h'][seg].max():.6g} min={d['l'][seg].min():.6g} exit~={d['o'][min(j1+1,len(t)-1)]:.6g}")
        r = d['c'][max(j0-3,1):j1+2] / d['c'][max(j0-4,0):j1+1] - 1
        print('   max |1h ret| %.1f%%, gaps in time: %d' % (np.abs(r).max()*100, int((np.diff(t[seg]) != V.H).sum())))


if __name__ == '__main__':
    main()
