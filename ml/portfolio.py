#!/usr/bin/env python3
"""Портфель «сильные против слабых» на прогнозах ML (walk-forward). См. ml/PREREG2.md.
Раз в сутки (00:00 UTC) ранжируем монеты по прогнозу, держим лонг сильных и шорт слабых с гистерезисом
(вход в топ-N, выход только при выпадении за топ-M) → меньше оборот. Доходность close→close 24ч, фандинг учтён."""
import os, sys
import numpy as np
import pandas as pd
import lightgbm as lgb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screen as S  # noqa: E402

H = S.H
HOLD = S.HOLD


def predictions(df, feats, ycol, hz):
    P = dict(objective='regression', learning_rate=0.03, num_leaves=31, min_data_in_leaf=1000, feature_fraction=0.7,
             bagging_fraction=0.7, bagging_freq=1, lambda_l2=10, verbose=-1, num_threads=os.cpu_count())
    out = []
    folds = list(S.FOLDS)
    for fi, fs in enumerate(folds):
        f0 = fs.value // 10**6
        f1 = folds[fi + 1].value // 10**6 if fi + 1 < len(folds) else pd.Timestamp('2026-11-01', tz='UTC').value // 10**6
        tr = df[df.t < f0 - (hz + 24) * H].dropna(subset=[ycol]).iloc[::2]
        yy = tr[ycol] / tr.vol24.clip(lower=1e-4); yy = yy.clip(yy.quantile(0.005), yy.quantile(0.995))
        m = lgb.train(P, lgb.Dataset(tr[feats], yy), num_boost_round=400)
        te = df[(df.t >= f0) & (df.t < f1)][['t', 'sym']].copy()
        te['pred'] = m.predict(df.loc[te.index, feats])
        out.append(te)
        print(ycol, fs.date(), flush=True)
    return pd.concat(out)


def backtest(pr, px, fund, n_in, n_out, every, cost_side, smooth):
    """pr: прогнозы (t, sym, pred); px: pivot close по часам; fund: pivot фандинга следующих 24ч."""
    p = pr.pivot(index='t', columns='sym', values='pred')
    if smooth > 1:
        p = p.rolling(smooth, min_periods=max(1, smooth // 2)).mean()
    ts = [t for t in p.index if pd.Timestamp(t + H, unit='ms').hour == 0]
    ts = ts[::every]
    longs, shorts = set(), set(); w_old = pd.Series(dtype=float)
    rows = []
    for a, b in zip(ts[:-1], ts[1:]):
        r = p.loc[a].dropna()
        if len(r) < 12:
            continue
        rk = r.rank(ascending=False)            # 1 = сильнейшая
        rk_s = r.rank(ascending=True)           # 1 = слабейшая
        longs = {s for s in longs if s in rk.index and rk[s] <= n_out} | set(rk[rk <= n_in].index)
        shorts = {s for s in shorts if s in rk_s.index and rk_s[s] <= n_out} | set(rk_s[rk_s <= n_in].index)
        both = longs & shorts; longs -= both; shorts -= both
        w = pd.Series(0.0, index=r.index)
        if longs:
            w[list(longs)] = 0.5 / len(longs)
        if shorts:
            w[list(shorts)] = -0.5 / len(shorts)
        turn = w.subtract(w_old, fill_value=0).abs().sum()
        ra = px.loc[b, w.index] / px.loc[a, w.index] - 1
        # фандинг за удержание: лонг платит положительный, шорт получает
        fsum = pd.Series(0.0, index=w.index)
        tt = a
        while tt < b:
            if tt in fund.index:
                fsum = fsum.add(fund.loc[tt, w.index].fillna(0), fill_value=0)
            tt += 24 * H
        gross = (w * ra.fillna(0)).sum()
        rows.append(dict(t=a, gross=gross, fund=-(w * fsum).sum(), cost=turn * cost_side, turn=turn,
                         n=len(longs) + len(shorts)))
        w_old = w
    d = pd.DataFrame(rows)
    d['net'] = d.gross + d.fund - d.cost
    return d


def summary(d, per_year):
    if d.empty:
        return '| – | – | – | – | – | – |'
    r = d.net.values; eq = np.cumprod(1 + r); dd = (1 - eq / np.maximum.accumulate(eq)).max()
    yrs = len(d) / per_year
    cagr = eq[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    sh = r.mean() / r.std() * np.sqrt(per_year) if r.std() > 0 else np.nan
    return (f'| {cagr*100:+.0f}% | {sh:.2f} | {dd*100:.0f}% | {d.gross.mean()*100:+.3f}% / {d.cost.mean()*100:.3f}% / {d.fund.mean()*100:+.3f}% | '
            f'{d.turn.mean():.2f} | ×{eq[-1]:.2f} |')


def main():
    syms, df, feats = S.dataset()
    px = df.pivot(index='t', columns='sym', values='cl')
    fund = df.pivot(index='t', columns='sym', values='f24')
    P = {k: predictions(df, feats, y, hz) for k, (y, hz) in {'rel24': ('y24_rel', 24), 'rel72': ('y72_rel', 72)}.items()}
    V = [
        ('ОСНОВНОЙ: rel72, сглаж. 24ч, вход топ-4 / выход за топ-8, maker', 'rel72', 4, 8, 1, 0.0003, 24),
        ('то же, taker', 'rel72', 4, 8, 1, 0.0008, 24),
        ('rel72 без гистерезиса (4/4), maker', 'rel72', 4, 4, 1, 0.0003, 24),
        ('rel72, 3 / 10, maker', 'rel72', 3, 10, 1, 0.0003, 24),
        ('rel72, ребаланс раз в 3 дня, maker', 'rel72', 4, 8, 3, 0.0003, 24),
        ('rel72 без сглаживания, maker', 'rel72', 4, 8, 1, 0.0003, 1),
        ('rel24, сглаж. 24ч, 4 / 8, maker', 'rel24', 4, 8, 1, 0.0003, 24),
        ('rel24, сглаж. 24ч, 4 / 8, taker', 'rel24', 4, 8, 1, 0.0008, 24),
    ]
    L = ['# Портфель «сильные против слабых» (ML, walk-forward 2023–2026)', '',
         'Капитал: 50% в лонгах, 50% в шортах, без плеча (рыночно-нейтрально). Доходность после издержек и фандинга.',
         'Издержки на сторону сделки: maker 0.03%, taker 0.08% (комиссия + проскальзывание).', '',
         '| вариант | выборка | год. доходн. | Шарп | макс. просадка | в день: до издержек / издержки / фандинг | оборот в ребаланс | капитал |',
         '|---|---|---|---|---|---|---|---|']
    res = {}
    for name, tgt, ni, no, ev, cs, sm in V:
        d = backtest(P[tgt], px, fund, ni, no, ev, cs, sm); res[name] = d
        py = 365 / ev
        L.append(f'| {name} | 2023–май 2026 ' + summary(d[d.t < HOLD], py))
        L.append(f'| ↳ | июнь–окт 2026 ' + summary(d[d.t >= HOLD], py))
    main_d = res[V[0][0]]
    L += ['', '## Основной по кварталам', '| квартал | доходность | Шарп |', '|---|---|---|']
    q = pd.to_datetime(main_d.t, unit='ms').dt.to_period('Q').astype(str)
    pos = 0
    for k, g in main_d.groupby(q):
        r = (1 + g.net).prod() - 1; sh = g.net.mean() / g.net.std() * np.sqrt(365) if g.net.std() > 0 else 0
        pos += r > 0
        L.append(f'| {k} | {r*100:+.1f}% | {sh:.1f} |')
    L.append(f'\nКварталов в плюсе: {pos} из {q.nunique()}.')
    os.makedirs('ml_out', exist_ok=True); open('ml_out/PORTFOLIO.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
