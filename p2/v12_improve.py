#!/usr/bin/env python3
"""Улучшения v1.2 — совместный список гипотез Claude + ChatGPT (предрегистрация 2026-11-28, см. PREREG.md).
Сигнал v1.2 неизменен. Меняется только размер позиции / добор / отсев. Портфель: риск 0.3% первой части, до 8 позиций,
одна позиция на монету. Выборки: A = 24 монеты разработки до июня 2026, B = 30 новых монет до июня 2026,
C = все 54 монеты июнь–окт 2026 (holdout, уже частично смотрели). Издержки ×1 и ×2."""
import glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
FT0, SLP0 = 0.0005, 0.0003
RISK, MAXPOS = 0.003, 8


def run(d, e, em, cm, add_frac=1.0, fund=True, min_stop=0.0, pfrac=0.0, pR=1.0, pbe=False):
    """stress.run с выходом на следующем открытии + фандинг; add_frac — размер добора от первой части."""
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    s, a = e['s'], e['a']; j0 = e['i'] + 1; maxh = 72
    if j0 + maxh + 1 >= len(c):
        return None
    k = (FT0 + SLP0) * cm
    e1 = o[j0]; stop = e['lvl'] - s * 0.5 * a; risk = abs(e1 - stop)
    if risk <= 0 or s * (e1 - stop) <= 0:
        return None
    if risk < min_stop * e1:                   # минимальная ширина стопа: отодвигаем стоп, размер по риску
        risk = min_stop * e1; stop = e1 - s * risk
    units = [(e1, 1.0)]; added = False; part = 0.0; pdone = pfrac <= 0
    ft = d['_ft']; fr = d['_fr']

    def fin(px, j_exit, out):
        q = sum(w for _, w in units)
        pnl = sum(w * s * (px - u) for u, w in units) - k * sum(u * w for u, w in units) - k * px * q + part
        if fund and len(ft):
            a_, b_ = np.searchsorted(ft, t[j0]), np.searchsorted(ft, t[j_exit] + H)
            pnl -= s * fr[a_:b_].sum() * sum(u * w for u, w in units)
        seg = slice(j0, j_exit + 1)
        mfe = ((h[seg].max() - e1) if s == 1 else (e1 - l[seg].min())) / risk
        return dict(R=pnl / risk, t=int(t[j0]), tx=int(t[j_exit] + H), sp=risk / e1, out=out, mfe=mfe)
    for j in range(j0, j0 + maxh):
        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
            return fin(min(o[j], stop) if s == 1 else max(o[j], stop), j, 'stop')
        if not pdone:                          # частичная фиксация pfrac первой части на +pR (стоп проверен раньше)
            lp = e1 + s * pR * risk
            if (s == 1 and h[j] >= lp) or (s == -1 and l[j] <= lp):
                px = max(o[j], lp) if s == 1 else min(o[j], lp)
                part += pfrac * (s * (px - e1) - k * e1 - k * px)
                units[0] = (e1, 1.0 - pfrac); pdone = True
                if pbe:
                    stop = e1
        if not added and add_frac > 0:
            lv = e1 + s * risk
            if (s == 1 and h[j] >= lv) or (s == -1 and l[j] <= lv):
                units.append((max(o[j], lv) if s == 1 else min(o[j], lv), add_frac)); added = True
        if (s == 1 and c[j] < em[j]) or (s == -1 and c[j] > em[j]):
            return fin(o[j + 1], j + 1, 'ema')
    return fin(c[j0 + maxh - 1], j0 + maxh - 1, 'time')


def daily_trend(d):
    """Знак (закрытие дня − EMA50 дневных закрытий) по последнему ЗАКРЫТОМУ дню, для каждого часа."""
    t = pd.to_datetime(d['t'], unit='ms'); c = pd.Series(d['c'], index=t)
    dc = c.resample('1D').last().dropna(); e50 = dc.ewm(span=50, adjust=False).mean()
    sg = np.sign(dc - e50).shift(1)            # вчерашний закрытый день
    return sg.reindex(t.floor('1D')).values


def rsi(c, n=14):
    """RSI Уайлдера."""
    c = np.asarray(c, float); d = np.diff(c, prepend=c[0])
    up = pd.Series(np.clip(d, 0, None)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1 / n, adjust=False).mean().values
    with np.errstate(divide='ignore', invalid='ignore'):
        r = 100 - 100 / (1 + up / dn)
    r[dn == 0] = 100.0
    return r


def rv_ratio(c, i):
    r = np.diff(np.log(c[max(0, i - 168):i + 1]))
    if len(r) < 168:
        return np.nan
    a, b = r[-24:].std(), r.std()
    return a / b if b > 0 else np.nan


def collect(dirs, cm, add_frac, min_stop=0.0, **kw):
    out = []; rets = {}
    for dd in dirs:
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            d['_ft'] = np.array([x[0] for x in d['fund']]); d['_fr'] = np.array([x[1] for x in d['fund']])
            f = ls.features(d); em = X.ema(d['c'], 20); dt = daily_trend(d)
            rs = rsi(d['c']); rse = pd.Series(rs).ewm(span=9, adjust=False).mean().values
            rets[s] = pd.Series(np.r_[np.nan, np.diff(np.log(d['c']))], index=d['t'].astype(np.int64))
            for e in B.events(d, f, REG, True):
                if e['sc'] < 5 or pd.Timestamp(int(d['t'][e['i']]) + H, unit='ms').dayofweek >= 5:
                    continue
                r = run(d, e, em, cm, add_frac, min_stop=min_stop, **kw)
                if not r:
                    continue
                i = e['i']
                r.update(sym=s, s=e['s'], sc=e['sc'], ti=int(d['t'][i]), grp=('A' if dd == DIRS[0] else 'B'),
                         dtr=dt[i] * e['s'] if dt[i] == dt[i] else 0.0, rv=rv_ratio(d['c'], i), rsi=float(rs[i]), rsi_d=float(rs[i] - rse[i]) * e['s'])
                out.append(r)
    return out, rets


def corr_max(x, op, rets):
    """Максимальная корреляция (с учётом направлений) новой сделки с открытыми, часовые доходности за 168 ч."""
    if not op:
        return 0.0
    a = rets[x['sym']].loc[x['ti'] - 167 * H:x['ti']]
    best = -1.0
    for y in op:
        b = rets[y['sym']].loc[x['ti'] - 167 * H:x['ti']]
        j = pd.concat([a, b], axis=1).dropna()
        if len(j) < 100:
            continue
        cc = np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1] * x['s'] * y['s']
        best = max(best, cc)
    return best


def equity(tr, mult, rets=None):
    """mult(x, open_list, recent_stops) -> множитель риска (0 = пропуск)."""
    ev = sorted(tr, key=lambda x: x['t']); eq = pk = 1.0; dd = 0.0; op = []; stops = []; months = {}; n = 0; m_sum = 0.0

    def close_upto(tt):
        nonlocal eq, pk, dd
        op.sort(key=lambda y: y['tx'])
        while op and op[0]['tx'] <= tt:
            y = op.pop(0); eq += y['_st'] * y['R']; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
            months[np.datetime64(y['tx'], 'ms').astype('datetime64[M]')] = eq
            if y['out'] == 'stop':
                stops.append(y['tx'])
    for x in ev:
        close_upto(x['t'])
        if len(op) >= MAXPOS or any(y['sym'] == x['sym'] for y in op):
            continue
        rs = [s for s in stops if x['t'] - 24 * H <= s <= x['t']]
        m = mult(x, op, rs, rets)
        if m <= 0:
            continue
        y = dict(x); y['_st'] = eq * RISK * m; op.append(y); n += 1; m_sum += m
    close_upto(10 ** 15)
    ks = sorted(months); vals = [months[k] for k in ks]
    mret = np.diff(np.r_[1.0, vals]) / np.r_[1.0, vals[:-1]] if vals else np.array([0.0])
    return eq, dd, n, (m_sum / n if n else 0), mret


VARIANTS = [
    ('База v1.2', 1.0, lambda x, op, rs, R: 1.0),
    ('H3 добор 50% (ChatGPT)', 0.5, lambda x, op, rs, R: 1.0),
    ('H2 корреляция ≥0.7 с открытой → ×0.5 (ChatGPT)', 1.0,
     lambda x, op, rs, R: 0.5 if corr_max(x, op, R) >= 0.7 else 1.0),
    ('H5 RV24/RV168 1–1.5 → ×0.75, >1.5 → ×0.5 (ChatGPT)', 1.0,
     lambda x, op, rs, R: 1.0 if not (x['rv'] > 1.0) else (0.75 if x['rv'] <= 1.5 else 0.5)),
    ('H7 ≥4 стопов за 24ч → ×0.5 (ChatGPT)', 1.0, lambda x, op, rs, R: 0.5 if len(rs) >= 4 else 1.0),
    ('C1 дневной тренд против → ×0.5 (Claude)', 1.0, lambda x, op, rs, R: 0.5 if x['dtr'] < 0 else 1.0),
    ('C2 дневной тренд против → пропуск (Claude)', 1.0, lambda x, op, rs, R: 0.0 if x['dtr'] < 0 else 1.0),
    ('C3 балл 6 → ×1.5, балл 5 → ×0.75 (Claude)', 1.0, lambda x, op, rs, R: 1.5 if x['sc'] == 6 else 0.75),
    ('C4 стоп > 3.5% цены → пропуск (под 20x, Claude)', 1.0, lambda x, op, rs, R: 0.0 if x['sp'] > 0.035 else 1.0),
]


def main():
    global DIRS, REG
    DIRS = sys.argv[1:3]
    ls.DATA = DIRS[0]; REG = ls.btc_regime(ls.load('BTCUSDT'))
    cache = {}
    L = ['# Улучшения v1.2 (портфель: риск 0.3%, до 8 позиций)', '',
         'Капитал ×, макс. просадка, MAR = годовая доходность / просадка. «Ср. риск» — средний множитель размера.', '']
    res = {}
    for cm in (1, 2):
        L += [f'## Издержки ×{cm}', '', '| вариант | A: 24 монеты до 06.2026 | B: 30 новых до 06.2026 | C: 54 монеты 06–10.2026 | ср. риск |',
              '|---|---|---|---|---|']
        for name, af, mult in VARIANTS:
            key = (cm, af)
            if key not in cache:
                cache[key] = collect(DIRS, cm, af)
            tr, rets = cache[key]
            cells = []; ms = []
            for g, sel in (('A', lambda x: x['grp'] == 'A' and x['t'] < HOLD), ('B', lambda x: x['grp'] == 'B' and x['t'] < HOLD),
                           ('C', lambda x: x['t'] >= HOLD)):
                sub = [x for x in tr if sel(x)]
                eq, dd, n, mm, _ = equity(sub, mult, rets)
                yrs = 4.33 if g != 'C' else 5 / 12
                cagr = eq ** (1 / yrs) - 1 if eq > 0 else -1
                mar = cagr / dd if dd > 0 else 0
                res[(cm, name, g)] = (eq, dd, mar)
                cells.append(f'×{eq:.2f} / {dd*100:.0f}% / MAR {mar:.2f} ({n})'); ms.append(mm)
            L.append(f'| {name} | ' + ' | '.join(cells) + f' | {np.mean(ms):.2f} |')
            print(L[-1], flush=True)
        L.append('')
    # вердикт по предрегистрированному критерию
    L += ['## Вердикт (критерий: MAR выше базы ≥10% на A и на B при издержках ×1 и ×2; C не хуже базы по MAR)', '']
    for name, _, _ in VARIANTS[1:]:
        ok = all(res[(cm, name, g)][2] >= 1.10 * res[(cm, 'База v1.2', g)][2] for cm in (1, 2) for g in 'AB')
        okc = all(res[(cm, name, 'C')][2] >= res[(cm, 'База v1.2', 'C')][2] for cm in (1, 2))
        L.append(f'- {name}: {"ПРИНЯТЬ" if ok and okc else ("A/B да, holdout хуже" if ok else "нет")}')
    os.makedirs('p2_out', exist_ok=True)
    open('p2_out/V12_IMPROVE.md', 'w').write('\n'.join(L))
    print('\n'.join(L))


if __name__ == '__main__':
    main()
