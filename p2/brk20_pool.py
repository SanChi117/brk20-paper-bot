#!/usr/bin/env python3
"""BRK-20 (лонг-пробой 20 дней на 4h, логика brk20_paper.py 1:1) на расширенном пуле.
Текущий пул бота против новых монет: стоит ли расширять. Сделки по каждой монете независимо (R),
затем портфель с MAXPOS=2 и одной позицией на монету, риск 1%."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import robust_ema as RE  # noqa: E402
ls = B.ls; HOLD = B.HOLD
H, H4 = 3600000, 4 * 3600000
FEE_IN, FEE_STOP, SLIP, RISK, TTL = 0.0002, 0.0004, 0.0002, 0.01, 24 * 3600000
CUR = ['SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'AVAXUSDT', 'SUIUSDT', 'APTUSDT', 'NEARUSDT', 'LINKUSDT',
       'AAVEUSDT', 'ARBUSDT', 'OPUSDT', 'DOGEUSDT', 'TAOUSDT', 'ONDOUSDT', 'INJUSDT', 'SEIUSDT']
T0 = int(np.datetime64('2022-01-01', 'ms').astype('int64'))


def sim(t, o, h, l, c):
    # 4h как build4h
    k4 = t // H4; b4 = []
    for i in range(len(t)):
        if not b4 or b4[-1][0] != k4[i] * H4:
            b4.append([k4[i] * H4, o[i], h[i], l[i], c[i]])
        else:
            x = b4[-1]; x[2] = max(x[2], h[i]); x[3] = min(x[3], l[i]); x[4] = c[i]
    n = len(b4); atr = [None] * n
    for i in range(1, n):
        tr = max(b4[i][2] - b4[i][3], abs(b4[i][2] - b4[i - 1][4]), abs(b4[i][3] - b4[i - 1][4]))
        atr[i] = tr if atr[i - 1] is None else atr[i - 1] * 13 / 14 + tr / 14
    pl = [(i + 3, i, b4[i][3]) for i in range(3, n - 3) if all(b4[i][3] <= b4[j][3] for j in range(i - 3, i + 4))]
    hi = [None] * n
    for i in range(120, n):
        hi[i] = max(x[2] for x in b4[i - 120:i])
    idx4 = {x[0]: i for i, x in enumerate(b4)}
    sig = {}  # время закрытия сигнального 4h -> (entry, stop, sig_t)
    for i in range(130, n):
        if atr[i] and hi[i] and b4[i][4] > hi[i]:
            e = hi[i]; s = e - 1.75 * atr[i]
            if 0 < (e - s) / e <= 0.2:
                sig[b4[i][0] + H4] = (e, s, b4[i][0])
    trades, pend, pos = [], None, None
    pli = 0
    for j in range(len(t)):
        tj = int(t[j])
        if pos:
            if l[j] <= pos['stop']:
                px = min(o[j], pos['stop']) * (1 - SLIP)
                R = pos['rt'] + (px - pos['e']) / pos['e'] / pos['d'] - FEE_STOP * pos['nr']
                trades.append(dict(R=R, t=pos['t'], tx=tj + H, out='stop' if pos['st'] == 0 else 'trail')); pos = None
            else:
                if pos['st'] == 0 and h[j] >= pos['be']:
                    pos['stop'] = pos['e'] * (1 + FEE_IN + FEE_STOP); pos['st'] = 1
                if pos['st'] == 1 and tj in idx4:
                    a = atr[idx4[tj]]
                    cands = [p for p in pl if b4[p[0]][0] <= tj and p[2] > pos['e'] and b4[p[1]][0] > pos['t4']]
                    if cands and a:
                        ns = cands[-1][2] - 0.5 * a
                        if ns > pos['stop']:
                            pos['stop'] = ns
        # новый сигнал появляется на закрытии 4h (время открытия следующего 4h) — если нет заявки и позиции
        if tj in sig and pend is None and pos is None:
            e, s, st4 = sig[tj]; pend = dict(e=e, s=s, exp=st4 + TTL, af=st4 + H4)
        if pend and pos is None:
            if tj > pend['exp']:
                pend = None
            elif tj >= pend['af'] and h[j] >= pend['e'] * (1 + SLIP):
                fill = max(pend['e'], o[j]) * (1 + SLIP); sf = (fill - pend['s']) / fill
                if 0 < sf <= 0.2:
                    nr = min(1 / sf, 1 / RISK)
                    pos = dict(e=fill, stop=pend['s'], d=sf, nr=nr, rt=-FEE_IN * nr, st=0, t4=tj // H4 * H4, t=tj, be=fill * (1 + 2 * sf))
                    if l[j] <= pos['stop']:
                        px = pos['stop'] * (1 - SLIP)
                        trades.append(dict(R=pos['rt'] + (px - fill) / fill / sf - FEE_STOP * nr, t=tj, tx=tj + H, out='stop')); pos = None
                pend = None
    return trades


def main():
    U = {}
    for dd in sys.argv[1:3]:
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            tr = [x for x in sim(d['t'].astype('int64'), d['o'], d['h'], d['l'], d['c']) if x['t'] >= T0]
            for x in tr:
                x['sym'] = s
            U[s] = tr
    cur = [s for s in U if s in CUR]; new = [s for s in U if s not in CUR]
    L = ['# BRK-20 на расширенном пуле', '', f'Текущий пул в данных: {len(cur)} из 17 ({", ".join(x[:-4] for x in cur)}); новых монет: {len(new)}', '',
         '| группа | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    for name, g in (('текущий пул', cur), ('новые монеты', new), ('все вместе', cur + new)):
        tr = [x for s in g for x in U[s]]
        for lab, sub in (('2022 – май 2026', [x for x in tr if x['t'] < HOLD]), ('июнь–окт 2026', [x for x in tr if x['t'] >= HOLD])):
            L.append(f'| {name} | {lab} ' + B.stats([x['R'] for x in sub]))
    L += ['', '## По монетам (2022 – окт 2026)', '', '| монета | в пуле | сделок | ΣR | PF |', '|---|---|---|---|---|']
    for s in sorted(U, key=lambda s: -sum(x['R'] for x in U[s])):
        R = [x['R'] for x in U[s]]; w = sum(r for r in R if r > 0); lo = -sum(r for r in R if r <= 0)
        L.append(f"| {s[:-4]} | {'да' if s in CUR else ''} | {len(R)} | {sum(R):+.1f} | {w / lo if lo else 99:.2f} |")
    L += ['', '## Портфель: до 2 позиций, риск 1%', '', '| пул | 2022 – май 2026: × / просадка / сделок | июнь–окт 2026: × |', '|---|---|---|']
    for name, g in (('текущий пул', cur), ('все монеты', cur + new), ('новые монеты отдельно', new)):
        tr = sorted([x for s in g for x in U[s]], key=lambda x: x['t'])
        a = RE.equity([x for x in tr if x['t'] < HOLD], RISK, 2); b = RE.equity([x for x in tr if x['t'] >= HOLD], RISK, 2)
        L.append(f'| {name} | ×{a[0]:.2f} / {a[1] * 100:.0f}% / {a[2] if len(a) > 2 else "—"} | ×{b[0]:.2f} |')
    L += ['', '## Сколько позиций держать одновременно (2022 – май 2026 / июнь–окт 2026)', '',
          '| пул | риск | до 2 | до 4 | до 6 | до 8 | до 12 |', '|---|---|---|---|---|---|---|']
    for name, g in (('текущий пул', cur), ('все 54', cur + new)):
        tr = sorted([x for s in g for x in U[s]], key=lambda x: x['t'])
        for rk in (0.005, 0.01):
            cells = []
            for mp in (2, 4, 6, 8, 12):
                a = RE.equity([x for x in tr if x['t'] < HOLD], rk, mp); b = RE.equity([x for x in tr if x['t'] >= HOLD], rk, mp)
                cells.append(f'×{a[0]:.2f} (dd {a[1] * 100:.0f}%) / ×{b[0]:.2f}')
            L.append(f'| {name} | {rk * 100:.1f}% | ' + ' | '.join(cells) + ' |')
    os.makedirs('p2_out', exist_ok=True); open('p2_out/BRK20_POOL.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
