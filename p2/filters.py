#!/usr/bin/env python3
"""Фильтры для v1.1 (сильный пробой + добор +1R + выход за EMA20): время суток/день недели и позиционирование
(топ-трейдеры против толпы, фандинг). Правило выбирается на 24 монетах (до июня 2026), проверяется на 30 новых и holdout."""
import glob, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import exits as X  # noqa: E402
import pyramid as P  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD


def zs(x, w=720, mp=240):
    m = x.rolling(w, min_periods=mp).mean().shift(1); s = x.rolling(w, min_periods=mp).std().shift(1)
    return (x - m) / s


def metrics(mdir, sym, t):
    p = f'{mdir}/{sym}_metrics.csv'
    if not os.path.exists(p):
        return None
    m = pd.read_csv(p).drop_duplicates('t').set_index('t').reindex(t)
    tt = np.log(m['sum_toptrader_long_short_ratio'].where(lambda x: x > 0)).ffill(limit=3)
    gl = np.log(m['count_long_short_ratio'].where(lambda x: x > 0)).ffill(limit=3)
    return dict(div_z=zs(tt - gl).values, tt_d24=(tt - tt.shift(24)).values, gl_z=zs(gl).values)


def collect(pairs, reg):
    out = []
    for dd, mdir, tag in pairs:
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            f = ls.features(d); em = X.ema(d['c'], 20); mt = metrics(mdir, s, d['t'])
            for e in [e for e in B.events(d, f, reg, True) if e['sc'] >= 5]:
                r = P.run(d, f, e, em, 1.0, 1, 'none')
                if not r:
                    continue
                i = e['i']; tt = pd.Timestamp(int(d['t'][i]) + H, unit='ms')
                row = dict(R=r[0], t=r[1], tx=r[2], sym=s, tag=tag, s=e['s'], hour=tt.hour, dow=tt.dayofweek, fz=f['fz'][i])
                if mt:
                    row.update(div=e['s'] * mt['div_z'][i], ttd=e['s'] * mt['tt_d24'][i], glz=e['s'] * mt['gl_z'][i])
                out.append(row)
    return pd.DataFrame(out)


def ci(R):
    R = np.asarray(R)
    if len(R) < 20:
        return f'{R.mean() if len(R) else float("nan"):+.2f} (n={len(R)})'
    g = np.random.default_rng(1); bm = np.sort([g.choice(R, len(R)).mean() for _ in range(500)])
    return f'{R.mean():+.2f} [{bm[12]:+.2f}; {bm[487]:+.2f}] n={len(R)}'


def table(df, col, bins, labels, title):
    L = [f'### {title}', '', '| группа | 24 монеты, до июня 2026 | 30 новых, до июня 2026 | 54, июнь–окт 2026 |', '|---|---|---|---|']
    g = pd.cut(df[col], bins=bins, labels=labels, right=False)
    for lab in labels:
        a = df[(g == lab) & (df.tag == 'dev') & (df.t < HOLD)].R
        b = df[(g == lab) & (df.tag == 'new') & (df.t < HOLD)].R
        c = df[(g == lab) & (df.t >= HOLD)].R
        L.append(f'| {lab} | {ci(a)} | {ci(b)} | {ci(c)} |')
    return L + ['']


def main():
    d1, m1, d2, m2 = sys.argv[1:5]
    ls.DATA = d1; reg = ls.btc_regime(ls.load('BTCUSDT'))
    df = collect([(d1, m1, 'dev'), (d2, m2, 'new')], reg)
    L = ['# Фильтры для v1.1: время и позиционирование', '', 'Средний R сделки v1.1 (в рисках первой части) [95% ДИ], n — сделок.', '']
    L += table(df, 'hour', [0, 6, 12, 18, 24], ['00–06 UTC', '06–12 UTC', '12–18 UTC', '18–24 UTC'], 'Час сигнала (UTC, закрытие свечи пробоя)')
    df['wk'] = (df.dow >= 5).astype(int)
    L += table(df, 'wk', [0, 1, 2], ['будни', 'выходные'], 'Будни / выходные')
    L += table(df, 'div', [-99, -0.5, 0.5, 99], ['топ-трейдеры против пробоя', 'нейтрально', 'топ-трейдеры за пробой (сильнее толпы)'], 'Топ-трейдеры против толпы (z расхождения, со знаком сделки)')
    L += table(df, 'glz', [-99, -1, 1, 99], ['толпа против пробоя', 'нейтрально', 'толпа уже в сторону пробоя'], 'Позиция толпы (z соотношения лонг/шорт аккаунтов, со знаком сделки)')
    L += table(df, 'fz', [-99, -1, 1, 99], ['фандинг низкий', 'обычный', 'фандинг высокий'], 'Фандинг (z) — без знака сделки')
    df['fzs'] = df.fz * df.s
    L += table(df, 'fzs', [-99, -1, 1, 99], ['толпа платит против пробоя', 'обычно', 'толпа переплачивает за пробой'], 'Фандинг со знаком сделки')
    import robust_ema as RE
    L += ['## Капитал: 54 монеты, риск 0.3% на первую часть, до 8 позиций', '',
          '| вариант | сделок до июня 2026 | 2022–май 2026: капитал / год / просадка | июнь–окт 2026: капитал / просадка |', '|---|---|---|---|']
    rules = [('v1.1 без фильтров', lambda x: True),
             ('v1.2: без сигналов выходного дня', lambda x: x['dow'] < 5),
             ('v1.2 + топ-трейдеры не против (z ≥ −0.5)', lambda x: x['dow'] < 5 and not (x.get('div', 0) < -0.5)),
             ('v1.2 + толпа не в сторону пробоя (z ≤ 1)', lambda x: x['dow'] < 5 and not (x.get('glz', 0) > 1))]
    recs = df.to_dict('records')
    for name, fn in rules:
        sub = [dict(R=x['R'], t=x['t'], tx=x['tx'], sym=x['sym']) for x in recs if fn(x)]
        a = RE.equity([x for x in sub if x['t'] < HOLD], 0.003, 8); b = RE.equity([x for x in sub if x['t'] >= HOLD], 0.003, 8)
        L.append(f"| {name} | {sum(1 for x in sub if x['t'] < HOLD)} | ×{a[0]:.2f} / {(a[0]**(1/4.33)-1)*100:+.0f}% / {a[1]*100:.0f}% | ×{b[0]:.2f} / {b[1]*100:.0f}% |")
    os.makedirs('p2_out', exist_ok=True); open('p2_out/FILTERS.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
