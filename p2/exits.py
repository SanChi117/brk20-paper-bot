#!/usr/bin/env python3
"""Система выхода для сильных пробоев (оценка 5–6 после сжатия). Одинаковые входы и начальный стоп,
сравниваются разные выходы. Сначала — профиль движения после входа (MFE/MAE)."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
FT, FM, SLP = B.FT, B.FM, B.SL_


def ema(x, n):
    a = 2 / (n + 1); out = np.empty_like(x); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def run_exit(d, f, e, rule, ema20, maxh=72):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    s, a = e['s'], e['a']; j0 = e['i'] + 1
    if j0 + maxh >= len(c):
        return None
    en = o[j0]; stop = e['lvl'] - s * 0.5 * a; risk = abs(en - stop)
    if risk <= 0 or s * (en - stop) <= 0:
        return None
    rem = 1.0; pnl = -en * (FT + SLP); best = en; kind = rule[0]; be_done = False
    for j in range(j0, j0 + maxh):
        # 1) стоп (текущий)
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            px = min(o[j], stop) if s == 1 else max(o[j], stop)
            pnl += rem * (s * (px - en) - px * (FT + SLP)); return pnl / risk, int(t[j0])
        # 2) тейк/частичные
        if kind == 'tp':
            tp = en + s * rule[1] * risk
            if (s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp):
                pnl += rem * (s * (tp - en) - tp * FM); return pnl / risk, int(t[j0])
        if kind == 'part' and not be_done:
            tp1 = en + s * rule[1] * risk
            if (s == 1 and h[j] >= tp1) or (s == -1 and l[j] <= tp1):
                pnl += 0.5 * (s * (tp1 - en) - tp1 * FM); rem = 0.5; be_done = True
                stop = en + s * en * (FT + FM)
        # 3) время
        if kind == 'time' and j - j0 + 1 >= rule[1]:
            pnl += rem * (s * (c[j] - en) - c[j] * (FT + SLP)); return pnl / risk, int(t[j0])
        # 4) обновление трейлинга по закрытию бара (действует со следующего бара)
        best = max(best, h[j]) if s == 1 else min(best, l[j])
        if kind in ('chand', 'part'):
            k = rule[2] if kind == 'part' else rule[1]
            if kind == 'chand' or be_done:
                ns = best - s * k * f['atr'][j]
                stop = max(stop, ns) if s == 1 else min(stop, ns)
        if kind == 'swing' and j >= j0 + 2:
            # подтверждённый фрактал на j-1
            if s == 1 and l[j - 1] < l[j - 2] and l[j - 1] < l[j]:
                stop = max(stop, l[j - 1] - 0.1 * f['atr'][j])
            if s == -1 and h[j - 1] > h[j - 2] and h[j - 1] > h[j]:
                stop = min(stop, h[j - 1] + 0.1 * f['atr'][j])
        if kind == 'ema' and ((s == 1 and c[j] < ema20[j]) or (s == -1 and c[j] > ema20[j])):
            pnl += rem * (s * (c[j] - en) - c[j] * (FT + SLP)); return pnl / risk, int(t[j0])
    px = c[j0 + maxh - 1]
    pnl += rem * (s * (px - en) - px * (FT + SLP)); return pnl / risk, int(t[j0])


def profile(d, e, hz=48):
    o, h, l, c = d['o'], d['h'], d['l'], d['c']
    s, a = e['s'], e['a']; j0 = e['i'] + 1
    if j0 + hz >= len(c):
        return None
    en = o[j0]; stop = e['lvl'] - s * 0.5 * a; risk = abs(en - stop)
    if risk <= 0 or s * (en - stop) <= 0:
        return None
    mfe = 0; tmfe = 0; stopped = False
    for j in range(j0, j0 + hz):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            stopped = True; break
        fav = (h[j] - en) / risk if s == 1 else (en - l[j]) / risk
        if fav > mfe:
            mfe, tmfe = fav, j - j0 + 1
    return mfe, tmfe, stopped


def main():
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{ls.DATA}/*_1h.csv'))
    D = {s: ls.load(s) for s in syms}; reg = ls.btc_regime(D['BTCUSDT'])
    F = {s: ls.features(D[s]) for s in syms if len(D[s]['c']) >= 2000}
    EM = {s: ema(D[s]['c'], 20) for s in F}
    EV = {s: [e for e in B.events(D[s], F[s], reg, True) if e['sc'] >= 5] for s in F}
    L = ['# Система выхода для сильных пробоев (оценка 5–6)', '']
    pr = [profile(D[s], e) for s in F for e in EV[s] if D[s]['t'][e['i']] < HOLD]
    pr = [x for x in pr if x]
    m = np.array([x[0] for x in pr]); tm = np.array([x[1] for x in pr]); st = np.array([x[2] for x in pr])
    L += ['## Как идёт цена после входа (dev, до стопа, окно 48ч; R = расстояние до стопа)', '',
          f'Входов: {len(pr)}. Выбиты стопом за 48ч: {st.mean()*100:.0f}%.',
          'Доля, дошедших до +XR до стопа: ' + ', '.join(f'{k}R {np.mean(m >= k)*100:.0f}%' for k in (0.5, 1, 1.5, 2, 3, 5)),
          f'Медиана максимального хода в плюс: {np.median(m):.2f}R; медиана времени до максимума: {np.median(tm):.0f} ч.', '',
          '## Сравнение выходов (одинаковые входы и начальный стоп; лимит 72ч)', '',
          '| выход | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    R = [('тейк 2R', ('tp', 2)), ('тейк 3R', ('tp', 3)),
         ('по времени 4ч', ('time', 4)), ('по времени 8ч', ('time', 8)), ('по времени 24ч', ('time', 24)), ('по времени 48ч', ('time', 48)),
         ('трейлинг 1.5 ATR', ('chand', 1.5)), ('трейлинг 2 ATR', ('chand', 2)), ('трейлинг 3 ATR', ('chand', 3)),
         ('стоп за свингами 1h', ('swing',)), ('закрытие за EMA20', ('ema',)),
         ('50% на 1R + БУ + трейлинг 2 ATR', ('part', 1, 2)), ('50% на 1.5R + БУ + трейлинг 3 ATR', ('part', 1.5, 3))]
    for name, rule in R:
        tr = [run_exit(D[s], F[s], e, rule, EM[s]) for s in F for e in EV[s]]
        tr = [x for x in tr if x]
        L.append(f'| {name} | dev ' + B.stats([r for r, te in tr if te < HOLD]))
        L.append(f'| ↳ | hold ' + B.stats([r for r, te in tr if te >= HOLD]))
    os.makedirs('p2_out', exist_ok=True); open('p2_out/EXITS.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
