#!/usr/bin/env python3
"""Слепой тест «ИИ по скринам»: рисует картинки как в приложении Binance (1D/4H/1H/15m + данные) на случайные
моменты прошлого. Монета и дата скрыты, цены умножены на случайный коэффициент. Ключ (монета, время, коэффициент)
сохраняется в закрытый кэш Actions, в репозиторий не попадает."""
import csv, json, os, sys, io, zipfile, random
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'research'))
from fetch_vision import fetch_zip  # noqa: E402

H = 3600_000; D = 24 * H
N = int(os.environ.get('N', 40)); SEED = int(os.environ.get('SEED', 2026))
DATA, ML, OUT, SEAL = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


def load1h(s):
    a = np.loadtxt(f'{DATA}/{s}_1h.csv', delimiter=',', skiprows=1); a = a[np.argsort(a[:, 0])]
    return a  # t,o,h,l,c,qv,tbq,oi


def agg(a, P):
    g = a[:, 0] // P * P; keys, st, cnt = np.unique(g, return_index=True, return_counts=True)
    en = st + cnt - 1
    return np.c_[keys, a[st, 1], np.maximum.reduceat(a[:, 2], st), np.minimum.reduceat(a[:, 3], st), a[en, 4],
                 np.add.reduceat(a[:, 5], st), np.add.reduceat(a[:, 6], st), a[en, 7]][cnt == P // H]


def ema(x, n):
    a = 2 / (n + 1); o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def rsi(c, n):
    d = np.diff(c, prepend=c[0]); up = np.maximum(d, 0); dn = np.maximum(-d, 0)
    au = ema(up, 2 * n - 1); ad = ema(dn, 2 * n - 1)
    return 100 - 100 / (1 + au / np.maximum(ad, 1e-12))


def supertrend(h, l, c, n=10, m=3):
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    atr = ema(tr, 2 * n - 1); hl = (h + l) / 2; up = hl + m * atr; dn = hl - m * atr
    st = np.zeros_like(c); dirn = np.ones_like(c); fu = up.copy(); fd = dn.copy()
    for i in range(1, len(c)):
        fu[i] = up[i] if (up[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fd[i] = dn[i] if (dn[i] > fd[i - 1] or c[i - 1] < fd[i - 1]) else fd[i - 1]
        dirn[i] = 1 if c[i] > fu[i - 1] else (-1 if c[i] < fd[i - 1] else dirn[i - 1])
        st[i] = fd[i] if dirn[i] == 1 else fu[i]
    return st, dirn


def panel(fig, gs, a, k, title, show_oi=True, nbars=110):
    t, o, h, l, c, v, oi = a[:, 0], a[:, 1] * k, a[:, 2] * k, a[:, 3] * k, a[:, 4] * k, a[:, 5], a[:, 7]
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    mb = np.convolve(c, np.ones(20) / 20)[:len(c)]; sd = np.array([c[max(0, i - 19):i + 1].std() for i in range(len(c))])
    st, dr = supertrend(h, l, c)
    r6, r12, r24 = rsi(c, 6), rsi(c, 12), rsi(c, 24)
    s = slice(max(0, len(c) - nbars), len(c)); x = np.arange(len(c))[s] - (len(c) - nbars)
    sub = gs.subgridspec(4 if show_oi else 3, 1, height_ratios=[5, 1.2, 1.2, 1.4][:4 if show_oi else 3] if show_oi else [5, 1.2, 1.4], hspace=0.05)
    ax = fig.add_subplot(sub[0]); col = np.where(c[s] >= o[s], '#26a69a', '#ef5350')
    ax.vlines(x, l[s], h[s], color=col, lw=0.7); ax.bar(x, c[s] - o[s], bottom=o[s], color=col, width=0.7)
    ax.plot(x, e20[s], c='#e6b422', lw=0.9, label=f'EMA20 {e20[-1]:.4g}'); ax.plot(x, e50[s], c='#c2185b', lw=0.9, label=f'EMA50 {e50[-1]:.4g}')
    ax.plot(x, e200[s], c='#7e57c2', lw=0.9, label=f'EMA200 {e200[-1]:.4g}')
    ax.plot(x, (mb + 2 * sd)[s], c='#f0c040', lw=0.6, ls='--'); ax.plot(x, (mb - 2 * sd)[s], c='#9575cd', lw=0.6, ls='--')
    stc = np.where(dr[s] > 0, st[s], np.nan); stn = np.where(dr[s] < 0, st[s], np.nan)
    ax.plot(x, stc, c='green', lw=1); ax.plot(x, stn, c='red', lw=1)
    ax.set_title(f'{title}   цена {c[-1]:.4g}   BOLL {mb[-1]+2*sd[-1]:.4g}/{mb[-1]:.4g}/{mb[-1]-2*sd[-1]:.4g}   Supertrend {st[-1]:.4g} ({"зел" if dr[-1]>0 else "крас"})', fontsize=8, loc='left')
    ax.legend(fontsize=6, loc='upper left'); ax.yaxis.tick_right(); ax.tick_params(labelsize=6); ax.set_xticks([])
    av = fig.add_subplot(sub[1], sharex=ax); av.bar(x, v[s], color=col, width=0.7); av.set_yticks([]); av.set_xticks([]); av.set_ylabel('VOL', fontsize=6)
    i = 2
    if show_oi:
        ao = fig.add_subplot(sub[2], sharex=ax); ao.bar(x, oi[s], color='#7cd67c', width=0.7)
        lo = np.nanmin(oi[s]) if np.isfinite(oi[s]).any() else 0
        ao.set_ylim(lo * 0.98 if lo > 0 else 0, np.nanmax(oi[s]) * 1.01 if np.isfinite(oi[s]).any() else 1)
        ao.set_yticks([]); ao.set_xticks([]); ao.set_ylabel('OI', fontsize=6); i = 3
    ar = fig.add_subplot(sub[i], sharex=ax); ar.plot(x, r6[s], c='#e6b422', lw=0.8); ar.plot(x, r12[s], c='#c2185b', lw=0.8); ar.plot(x, r24[s], c='#7e57c2', lw=0.8)
    ar.axhline(70, ls='--', c='gray', lw=0.5); ar.axhline(30, ls='--', c='gray', lw=0.5); ar.set_ylim(0, 100)
    ar.set_ylabel(f'RSI {r6[-1]:.0f}/{r12[-1]:.0f}/{r24[-1]:.0f}', fontsize=6); ar.tick_params(labelsize=5); ar.set_xticks([])


def fetch15(s, T):
    rows = []
    for mo in {np.datetime64(int(T) - 5 * D, 'ms').astype('datetime64[M]'), np.datetime64(int(T), 'ms').astype('datetime64[M]')}:
        txt, _ = fetch_zip(f'https://data.binance.vision/data/futures/um/monthly/klines/{s}/15m/{s}-15m-{mo}.zip')
        for line in (txt or '').splitlines():
            p = line.split(',')
            if p[0].strip().isdigit():
                rows.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7]), float(p[10]), np.nan])
    a = np.array(sorted(rows)) if rows else np.zeros((0, 8))
    return a[a[:, 0] + 900_000 <= T] if len(a) else a


def main():
    syms = sorted(f[:-7] for f in os.listdir(DATA) if f.endswith('_1h.csv'))
    rng = random.Random(SEED); os.makedirs(OUT, exist_ok=True); os.makedirs(SEAL, exist_ok=True)
    key = []; made = 0
    while made < N:
        s = rng.choice(syms); a = load1h(s)
        t0 = max(a[0, 0] + 260 * D, 1672531200000); t1 = min(1793404800000, a[-1, 0]) - 15 * D   # 2023-01 … конец данных − 15 дней
        if t1 <= t0:
            continue
        T = (rng.randrange(int(t0), int(t1)) // H) * H        # момент = закрытие часа T
        hist = a[a[:, 0] + H <= T]
        if len(hist) < 260 * 24:
            continue
        m15 = fetch15(s, T)
        if len(m15) < 300:
            continue
        k = round(10 ** rng.uniform(-1, 1), 6)
        fig = plt.figure(figsize=(16, 21), dpi=72); G = fig.add_gridspec(3, 2, height_ratios=[1, 1, 0.55], hspace=0.12, wspace=0.08)
        panel(fig, G[0, 0], agg(hist, D), k, '1D'); panel(fig, G[0, 1], agg(hist, 4 * H), k, '4H')
        panel(fig, G[1, 0], hist, k, '1H'); panel(fig, G[1, 1], m15, k, '15m', show_oi=False)
        # данные
        sub = G[2, :].subgridspec(1, 4, wspace=0.25)
        fu = np.loadtxt(f'{DATA}/{s}_funding.csv', delimiter=',', skiprows=1, ndmin=2); fu = fu[fu[:, 0] <= T][-30:]
        ax = fig.add_subplot(sub[0]); ax.bar(range(len(fu)), fu[:, 1] * 100, color=np.where(fu[:, 1] >= 0, '#e6b422', '#ef5350'))
        ax.set_title(f'Фандинг (последние 30), сейчас {fu[-1,1]*100:.4f}%', fontsize=8); ax.tick_params(labelsize=6)
        h48 = hist[-48:]; ax = fig.add_subplot(sub[1]); xi = np.arange(len(h48))
        ax.bar(xi - 0.2, h48[:, 6], width=0.4, color='#26a69a'); ax.bar(xi + 0.2, h48[:, 5] - h48[:, 6], width=0.4, color='#ef5350')
        ax.set_title('Тейкеры 1h (48ч): покупка / продажа', fontsize=8); ax.set_yticks([]); ax.tick_params(labelsize=6)
        ax = fig.add_subplot(sub[2]); mp = f'{ML}/{s}_metrics.csv'
        if os.path.exists(mp):
            m = np.genfromtxt(mp, delimiter=',', skip_header=1); m = m[(m[:, 0] <= T - H) & (m[:, 0] > T - 49 * H)]
            if len(m):
                ax.plot(m[:, 2], c='#26a69a', label=f'топ-трейдеры позиции L/S {m[-1,2]:.2f}'); ax.plot(m[:, 3], c='#ef5350', label=f'все аккаунты L/S {m[-1,3]:.2f}')
                ax.legend(fontsize=6)
        ax.set_title('Лонг/шорт (48ч)', fontsize=8); ax.tick_params(labelsize=6)
        ax = fig.add_subplot(sub[3]); ax.plot(h48[:, 7], c='#e6b422'); ax.set_title(f'OI 1h (48ч): {(h48[-1,7]/h48[0,7]-1)*100:+.1f}%', fontsize=8); ax.set_yticks([]); ax.tick_params(labelsize=6)
        cid = f'{made+1:02d}'
        fig.suptitle(f'График {cid}   (монета и дата скрыты, цены в условных единицах)', fontsize=11)
        fig.savefig(f'{OUT}/{cid}.png', bbox_inches='tight'); plt.close(fig)
        key.append(dict(id=cid, sym=s, T=int(T), k=k)); made += 1; print('made', cid, flush=True)
    json.dump(key, open(f'{SEAL}/key.json', 'w'))


if __name__ == '__main__':
    main()
