#!/usr/bin/env python3
"""Проверка замороженной системы (сильный пробой после сжатия + выход за EMA20) на НОВЫХ монетах,
не участвовавших в разработке. Правила не меняются."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import robust_ema as RE  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD


def main():
    data = sys.argv[1]; btcdir = sys.argv[2]
    ls.DATA = btcdir; btc = ls.load('BTCUSDT'); reg = ls.btc_regime(btc)
    ls.DATA = data
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{data}/*_1h.csv'))
    D = {s: ls.load(s) for s in syms}
    F = {s: ls.features(D[s]) for s in syms if D[s] is not None and len(D[s]['c']) >= 2000}
    EV = {s: [e for e in B.events(D[s], F[s], reg, True) if e['sc'] >= 5] for s in F}
    tr = RE.exit_time(D, F, EV, 20, 72)
    L = ['# Новые монеты: замороженная система (сильный пробой + выход за EMA20)', '',
         f'Монет с данными: {len(F)} — ' + ', '.join(sorted(F)), '',
         '| выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|']
    L.append('| вся история до июня 2026 ' + B.stats([x['R'] for x in tr if x['t'] < HOLD]))
    L.append('| июнь–окт 2026 ' + B.stats([x['R'] for x in tr if x['t'] >= HOLD]))
    L += ['', '| год | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|']
    for y in range(2022, 2027):
        a = int(np.datetime64(f'{y}-01-01', 'ms').astype('int64')); b = int(np.datetime64(f'{y+1}-01-01', 'ms').astype('int64'))
        L.append(f'| {y} ' + B.stats([x['R'] for x in tr if a <= x['t'] < b]))
    L += ['', '## По монетам (средний R, сделок)']
    per = {}
    for x in tr:
        per.setdefault(x['sym'], []).append(x['R'])
    pos = sum(np.mean(v) > 0 for v in per.values())
    L.append(f'Монет со средним R > 0: {pos} из {len(per)}.')
    L.append(', '.join(f'{s} {np.mean(v):+.2f}({len(v)})' for s, v in sorted(per.items(), key=lambda kv: -np.mean(kv[1]))))
    L += ['', '## Капитал (≤5 позиций, одна на монету)']
    for r in (0.01, 0.02):
        eq, dd, nt, mr = RE.equity([x for x in tr if x['t'] < HOLD], r)
        L.append(f'- риск {r*100:.0f}%: ×{eq:.2f}, макс. просадка {dd*100:.0f}%, сделок {nt}' +
                 (f', месяцев в плюсе {np.mean(mr > 0)*100:.0f}%' if len(mr) else ''))
    os.makedirs('p2_out', exist_ok=True); open('p2_out/OOS_COINS.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
