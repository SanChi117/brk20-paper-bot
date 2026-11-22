#!/usr/bin/env python3
"""Скрининг «есть ли сигнал вообще»: ~70 признаков, LightGBM, walk-forward по кварталам с очисткой (purge 24ч).
Цели: относительная доходность 24ч (кросс-секция), направление 4ч и 24ч. Оценка только вне выборки обучения.
Индекс строки t = открытие 1h бара; все признаки известны на закрытии бара (t+1ч); цель — от закрытия бара."""
import glob, os, sys
import numpy as np
import pandas as pd
import lightgbm as lgb

H = 3600_000
D1, D2 = sys.argv[1], sys.argv[2]   # mme_data (свечи, OI, фандинг), ml_data (метрики, премия)
START = pd.Timestamp('2022-03-01', tz='UTC').value // 10**6
FOLDS = pd.date_range(os.environ.get('F0','2023-01-01'), os.environ.get('F1','2026-10-01'), freq='QS', tz='UTC')
HOLD = pd.Timestamp('2026-06-01', tz='UTC').value // 10**6
COST_RT = 0.0016   # туда-обратно: 2 × (taker 0.05% + проскальзывание 0.03%)


def zs(x, w=720, mp=240):
    m = x.rolling(w, min_periods=mp).mean().shift(1); s = x.rolling(w, min_periods=mp).std().shift(1)
    return (x - m) / s


def build(sym):
    k = pd.read_csv(f'{D1}/{sym}_1h.csv').drop_duplicates('t').set_index('t').sort_index()
    idx = np.arange(k.index[0], k.index[-1] + H, H)
    k = k.reindex(idx)
    o, h, l, c, qv, tb = k.o, k.h, k.l, k.c, k.qv, k.tbq
    oi = k.oi.where(k.oi > 0)
    f = pd.DataFrame(index=idx)
    lr = np.log(c).diff()
    for n in (1, 4, 12, 24, 72, 168):
        f[f'ret{n}'] = np.log(c / c.shift(n))
    f['vol24'] = lr.rolling(24, min_periods=20).std(); f['vol168'] = lr.rolling(168, min_periods=120).std()
    f['volr'] = f.vol24 / f.vol168
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean(); f['atrp'] = atr / c
    rng = (h - l).replace(0, np.nan)
    f['body'] = (c - o) / rng; f['uw'] = (h - np.maximum(o, c)) / rng; f['lw'] = (np.minimum(o, c) - l) / rng
    f['cpos'] = (c - l) / rng
    for n in (36, 168):
        hi = h.rolling(n).max().shift(1); lo = l.rolling(n).min().shift(1)
        f[f'dhi{n}'] = (c - hi) / atr; f[f'dlo{n}'] = (c - lo) / atr
        f[f'rpos{n}'] = (c - lo) / (hi - lo)
    f['rvol'] = qv / qv.rolling(168, min_periods=100).mean().shift(1)
    f['rv24'] = qv.rolling(24).sum() / qv.rolling(168, min_periods=100).sum().shift(24) * 7
    for n in (1, 4, 24):
        f[f'tak{n}'] = tb.rolling(n).sum() / qv.rolling(n).sum()
    for n in (1, 4, 24):
        d = np.log(oi / oi.shift(n)); f[f'doi{n}'] = d.where(d.abs() < 0.5)
    f['zoi4'] = zs(f.doi4)
    f['oi_vol'] = np.log(oi * c / qv.rolling(24).sum())
    fu = pd.read_csv(f'{D1}/{sym}_funding.csv').drop_duplicates('t').sort_values('t')
    if len(fu):
        pos = np.searchsorted(fu.t.values, idx + H, side='right') - 1
        rate = np.where(pos >= 0, fu.rate.values[np.maximum(pos, 0)], np.nan)
        f['fund'] = rate
        cs = np.r_[0, np.cumsum(fu.rate.values)]
        p3 = np.maximum(pos - 2, 0)
        f['fund3'] = np.where(pos >= 2, cs[pos + 1] - cs[p3], np.nan)
        f['fundz'] = zs(f.fund, 2160, 500)
    try:
        m = pd.read_csv(f'{D2}/{sym}_metrics.csv').drop_duplicates('t').set_index('t').reindex(idx)
        for col, nm in (('count_toptrader_long_short_ratio', 'tt_acc'), ('sum_toptrader_long_short_ratio', 'tt_pos'),
                        ('count_long_short_ratio', 'gl_acc'), ('sum_taker_long_short_vol_ratio', 'tk_ls')):
            x = np.log(m[col].where(m[col] > 0)).ffill(limit=3)
            f[nm] = x; f[nm + '_d24'] = x - x.shift(24); f[nm + '_z'] = zs(x)
        f['tt_vs_gl'] = f.tt_pos - f.gl_acc
    except FileNotFoundError:
        pass
    try:
        p = pd.read_csv(f'{D2}/{sym}_premium.csv').drop_duplicates('t').set_index('t').prem.reindex(idx)
        f['prem'] = p; f['prem8'] = p.rolling(8, min_periods=6).mean(); f['premz'] = zs(p)
    except FileNotFoundError:
        pass
    tt = pd.to_datetime(idx + H, unit='ms', utc=True)
    f['hs'] = np.sin(2 * np.pi * tt.hour / 24); f['hc'] = np.cos(2 * np.pi * tt.hour / 24); f['dow'] = tt.dayofweek
    # цели (от закрытия бара t)
    f['y4'] = np.log(c.shift(-4) / c); f['y24'] = np.log(c.shift(-24) / c)
    f['sym'] = sym
    return f


def main():
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{D1}/*_1h.csv'))
    F = {s: build(s) for s in syms}
    b = F['BTCUSDT']
    btc = pd.DataFrame({'btc_r4': b.ret4, 'btc_r24': b.ret24, 'btc_r168': b.ret168, 'btc_vol24': b.vol24})
    bc = np.exp(b.ret1.fillna(0).cumsum())
    btc['btc_trend'] = np.log(bc / bc.rolling(4800, min_periods=2400).mean())
    df = pd.concat([F[s].join(btc) for s in syms]).reset_index().rename(columns={'index': 't'})
    df = df[df.t >= START]
    for col in ('ret24', 'ret168', 'doi24', 'fund', 'tt_pos', 'gl_acc', 'prem', 'vol24', 'tak24', 'rvol'):
        if col in df:
            df['cs_' + col] = df.groupby('t')[col].rank(pct=True)
    df['ns'] = df.groupby('t').sym.transform('count')
    df['y24_rel'] = df.y24 - df.groupby('t').y24.transform('mean')
    feats = [c for c in df.columns if c not in ('t', 'sym', 'y4', 'y24', 'y24_rel', 'ns')]
    df[feats] = df[feats].astype('float32')
    print('rows', len(df), 'features', len(feats), flush=True)
    targets = {'rel24': ('y24_rel', 24), 'dir4': ('y4', 4), 'dir24': ('y24', 24)}
    P = dict(objective='regression', learning_rate=0.03, num_leaves=31, min_data_in_leaf=1000, feature_fraction=0.7,
             bagging_fraction=0.7, bagging_freq=1, lambda_l2=10, verbose=-1, num_threads=os.cpu_count())
    imp = {k: pd.Series(0.0, index=feats) for k in targets}
    out = {k: [] for k in targets}
    for fi, fs in enumerate(FOLDS):
        f0 = fs.value // 10**6; f1 = (FOLDS[fi + 1].value // 10**6) if fi + 1 < len(FOLDS) else pd.Timestamp('2026-11-01', tz='UTC').value // 10**6
        test = df[(df.t >= f0) & (df.t < f1)]
        for k, (y, hz) in targets.items():
            tr = df[(df.t < f0 - (hz + 24) * H)].dropna(subset=[y]).iloc[::2]
            yy = tr[y] / tr.vol24.clip(lower=1e-4)        # цель в единицах волатильности
            yy = yy.clip(yy.quantile(0.005), yy.quantile(0.995))
            mdl = lgb.train(P, lgb.Dataset(tr[feats], yy), num_boost_round=400)
            imp[k] += pd.Series(mdl.feature_importance('gain'), index=feats)
            te = test.dropna(subset=[y]).copy()
            te['pred'] = mdl.predict(te[feats])
            # порог «сильного сигнала» — по распределению прогнозов на обучении (без заглядывания в тест)
            ptr = mdl.predict(tr[feats].iloc[::7])
            te['hi'], te['lo'] = np.quantile(ptr, 0.95), np.quantile(ptr, 0.05)
            out[k].append(te[['t', 'sym', y, 'pred', 'hi', 'lo', 'ns']].rename(columns={y: 'y'}))
        print('fold', fs.date(), 'done', flush=True)
    L = ['# ML-скрининг: есть ли предсказуемость', '', f'Монет: {len(syms)}, признаков: {len(feats)}, строк: {len(df)}. '
         'Walk-forward по кварталам 2023Q1…2026Q4, обучение только на прошлом (зазор горизонт+24ч). Издержки сделки 0.16% туда-обратно.', '']
    for k, (y, hz) in targets.items():
        r = pd.concat(out[k]); r['q'] = pd.to_datetime(r.t, unit='ms').dt.to_period('Q').astype(str)
        L += [f'## Цель: {k}', '', '| квартал | IC (ранговая корр. прогноз/факт по моментам) | доля моментов IC>0 | стратегия: средн. за сделку после издержек | сделок | доля плюс |',
              '|---|---|---|---|---|---|']
        allic = []; alltr = []
        for q, g in r.groupby('q'):
            ic = g[g.ns >= 10].groupby('t').apply(lambda x: x.pred.rank().corr(x.y.rank()) if len(x) >= 10 else np.nan).dropna()
            allic.append(ic)
            if k == 'rel24':
                gg = g[pd.to_datetime(g.t + H, unit='ms', utc=True).dt.hour == 0]   # раз в сутки, без перекрытия
                rets = []
                for t, x in gg.groupby('t'):
                    if len(x) < 12:
                        continue
                    x = x.sort_values('pred'); rets.append(x.y.tail(4).mean() - x.y.head(4).mean() - 2 * COST_RT)
                tr_ = np.array(rets)
            else:
                g = g.sort_values('t'); tr_ = []
                for s, x in g.groupby('sym'):
                    nxt = -1
                    for t, p, yv, hi, lo in zip(x.t.values, x.pred.values, x.y.values, x.hi.values, x.lo.values):
                        if t < nxt:
                            continue
                        if p >= hi:
                            tr_.append(yv - COST_RT); nxt = t + hz * H
                        elif p <= lo:
                            tr_.append(-yv - COST_RT); nxt = t + hz * H
                tr_ = np.array(tr_)
            alltr.append((q, tr_))
            L.append(f'| {q} | {ic.mean():+.3f} | {(ic > 0).mean()*100:.0f}% | {tr_.mean()*100 if len(tr_) else float("nan"):+.2f}% | {len(tr_)} | {(tr_ > 0).mean()*100 if len(tr_) else 0:.0f}% |')
        ic = pd.concat(allic); trs = np.concatenate([x[1] for x in alltr]) if alltr else np.array([])
        qs = [x[1].mean() for x in alltr if len(x[1])]
        hl = [x[1] for x in alltr if x[0] >= '2026Q2']; hold = np.concatenate(hl) if hl else np.array([])
        L += [f'| **итого** | **{ic.mean():+.3f}** (t≈{ic.mean()/ic.std()*np.sqrt(len(ic)/max(hz,1)):.1f}) | {(ic>0).mean()*100:.0f}% | '
              f'**{trs.mean()*100:+.3f}%** | {len(trs)} | {(trs>0).mean()*100:.0f}% |', '',
              f'Кварталов со стратегией в плюсе: {sum(1 for v in qs if v > 0)} из {len(qs)}. С 2026Q2 (≈holdout): средн. {hold.mean()*100 if len(hold) else float("nan"):+.3f}% на {len(hold)} сделках.', '']
        top = (imp[k] / imp[k].sum()).sort_values(ascending=False).head(15)
        L += ['Главные признаки: ' + ', '.join(f'{n} {v*100:.1f}%' for n, v in top.items()), '']
    os.makedirs('ml_out', exist_ok=True); open('ml_out/REPORT.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
