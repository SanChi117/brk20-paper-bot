#!/usr/bin/env python3
"""Пакет 3 (54 монеты, 1h). См. p2/PREREG.md, раздел «Пакет 3».
M. Шорт после экстремального пампа: рост >= P за 7 дней (закрытие к минимуму 7 дней); вход — первое закрытие 4h ниже минимума
   предыдущих 24 ч; стоп = максимум пампа + 2%; выход: тейк −25% / 14 дней; пропуск при стопе > 40%; пауза 7 дней.
G. Парный арбитраж: для каждой монеты пара = монета с максимальной корреляцией 4h-доходностей за прошлые 90 дней;
   z-оценка лог-спреда (окно 30 дней, 4h); вход |z| >= 2.5, выход при |z| <= 0.5, стоп |z| >= 5, максимум 10 дней.
K. Альты против BTC: каждый понедельник, если корзина альтов обогнала BTC за 4 недели — лонг корзины / шорт BTC, иначе наоборот.
N. Лонг BTC после падения >= 20% за 7 дней (по закрытию), держать 30 дней.
Издержки 0.08% на сторону (пары: 4 стороны), фандинг не учтён."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from batch2 import load_all, grid, ci, periods, DEV_END, HOLD, H  # noqa: E402
COST = 0.0008


def to4h(T, C):
    m = (T % (4 * H)) == (3 * H)            # закрытие часа, кончающегося на границе 4h
    return T[m] + H, C[:, m]


def idea_M(S, T, C, U, L):
    L += ['## M. Шорт после экстремального пампа', '', '| памп за 7 дней | выборка | сделка (доля цены) [95% ДИ] | побед |', '|---|---|---|---|']
    T4, C4 = to4h(T, C)
    for P in (0.5, 1.0):
        res = []
        for k, s in enumerate(S):
            c = C4[k]; last = -10 ** 9
            for i in range(42 + 6, len(c) - 1):
                if not np.isfinite(c[i]) or i - last < 42:
                    continue
                w = c[i - 42:i]
                if np.isnan(w).any():
                    continue
                lo = w.min(); hi = w.max(); j_hi = i - 42 + int(np.argmax(w))
                if hi / lo - 1 < P or j_hi < i - 42 + int(np.argmin(w)):
                    continue
                if not c[i] < np.nanmin(c[i - 6:i]):
                    continue
                en = c[i]; st = max(hi, np.nanmax(c[i - 6:i + 1])) * 1.02
                if st / en - 1 > 0.40:
                    continue
                tp = en * 0.75; x = None
                for j in range(i + 1, min(len(c), i + 1 + 84)):
                    if not np.isfinite(c[j]):
                        continue
                    if c[j] >= st:
                        x = c[j]; break
                    if c[j] <= tp:
                        x = tp; break
                else:
                    jj = min(len(c) - 1, i + 84); x = c[jj] if np.isfinite(c[jj]) else en
                res.append((int(T4[i]), U[s]['grp'], (en - x) / en - 2 * COST)); last = i
        for lab, sel in (('все', None), ('24 монеты', 'A'), ('30 монет', 'B')):
            r = [(t, v) for t, g, v in res if sel is None or g == sel]
            tt = np.array([t for t, _ in r]); vv = np.array([v for _, v in r])
            for pl, x in periods(tt, vv).items():
                L.append(f'| {P * 100:.0f}% | {lab}, {pl} | {ci(x)} | {(x > 0).mean() * 100:.0f}% |' if len(x) else f'| {P * 100:.0f}% | {lab}, {pl} | — | — |')
    L.append('')


def idea_G(S, T, C, U, L):
    L += ['## G. Парный арбитраж (4h)', '']
    T4, C4 = to4h(T, C)
    lc = np.log(C4); R = np.diff(lc, axis=1)
    n = len(S); res = []
    step = 6 * 30                               # пересчёт пар раз в 30 дней
    for s0 in range(540, lc.shape[1] - 60, step):
        win = R[:, s0 - 540:s0]
        ok = np.isfinite(win).all(axis=1)
        idx = np.where(ok)[0]
        if len(idx) < 10:
            continue
        cm = np.corrcoef(win[idx]); np.fill_diagonal(cm, -1)
        pairs = set()
        for a in range(len(idx)):
            b = int(np.argmax(cm[a]))
            if cm[a, b] > 0.7:
                pairs.add(tuple(sorted((idx[a], idx[b]))))
        for a, b in pairs:
            beta = np.polyfit(lc[b, s0 - 180:s0], lc[a, s0 - 180:s0], 1)[0]
            pos = 0; e = None
            for i in range(s0, min(s0 + step, lc.shape[1])):
                sp_hist = lc[a, i - 180:i] - beta * lc[b, i - 180:i]
                if np.isnan(sp_hist).any() or not np.isfinite(lc[a, i]) or not np.isfinite(lc[b, i]):
                    continue
                mu, sd = sp_hist.mean(), sp_hist.std()
                if sd <= 0:
                    continue
                sp = lc[a, i] - beta * lc[b, i]; z = (sp - mu) / sd
                if pos == 0 and abs(z) >= 2.5:
                    pos = -1 if z > 0 else 1; e = (i, sp, z)
                elif pos != 0:
                    held = i - e[0]
                    if abs(z) <= 0.5 or abs(z) >= 5 or held >= 60 or i == min(s0 + step, lc.shape[1]) - 1:
                        pnl = pos * (sp - e[1]) / (1 + abs(beta)) - 4 * COST
                        res.append((int(T4[e[0]]), U[S[a]]['grp'], pnl)); pos = 0
        # продолжаем
    tt = np.array([t for t, _, _ in res]); vv = np.array([v for _, _, v in res])
    for pl, x in periods(tt, vv).items():
        L.append(f'- {pl}: {ci(x)}, побед {(x > 0).mean() * 100:.0f}%' if len(x) else f'- {pl}: —')
    L.append('')


def idea_K(S, T, C, L):
    L += ['## K. Корзина альтов против BTC (ротация раз в неделю)', '']
    bi = S.index('BTCUSDT'); alts = [k for k, s in enumerate(S) if s != 'BTCUSDT']
    W = 7 * 24; mons = np.where(((T // H) % (7 * 24)) == (4 * 24))[0]     # понедельник 00:00 UTC (1970-01-01 — четверг)
    tt, vv = [], []
    for i in mons:
        if i - 4 * W < 0 or i + W >= len(T):
            continue
        pa = np.nanmean(C[alts, i] / C[alts, i - 4 * W] - 1); pb = C[bi, i] / C[bi, i - 4 * W] - 1
        fa = np.nanmean(C[alts, i + W] / C[alts, i] - 1); fb = C[bi, i + W] / C[bi, i] - 1
        if not (np.isfinite(pa) and np.isfinite(pb) and np.isfinite(fa) and np.isfinite(fb)):
            continue
        sd = 1 if pa > pb else -1
        tt.append(int(T[i])); vv.append(sd * (fa - fb) / 2 - 2 * COST)
    tt, vv = np.array(tt), np.array(vv)
    for pl, x in periods(tt, vv).items():
        eq = np.cumprod(1 + x) if len(x) else [1]
        L.append(f'- {pl}: {ci(x)} за неделю; капитал ×{eq[-1]:.2f}')
    L.append('')


def idea_N(S, T, C, L):
    L += ['## N. Лонг BTC после падения ≥20% за 7 дней, держать 30 дней', '']
    bi = S.index('BTCUSDT'); c = C[bi]; last = -10 ** 9; out = []
    for i in range(168, len(c) - 720):
        if i - last < 720 or not np.isfinite(c[i]) or not np.isfinite(c[i - 168]):
            continue
        if c[i] / c[i - 168] - 1 <= -0.20:
            out.append((int(T[i]), c[i + 720] / c[i] - 1 - 2 * COST)); last = i
    L.append('- сделки: ' + ', '.join(f'{np.datetime64(t, "ms").astype("datetime64[D]")}: {r * 100:+.1f}%' for t, r in out))
    base = [c[i + 720] / c[i] - 1 for i in range(168, len(c) - 720, 720) if np.isfinite(c[i]) and np.isfinite(c[i + 720])]
    L.append(f'- для сравнения: любые 30 дней BTC в среднем {np.mean(base) * 100:+.1f}% (n={len(base)})')
    L.append('')


def main():
    U = load_all([(sys.argv[1], 'A'), (sys.argv[2], 'B')])
    S, T, C = grid(U)
    L = ['# Пакет 3: четыре идеи (54 монеты)', '']
    for f in (lambda: idea_M(S, T, C, U, L), lambda: idea_G(S, T, C, U, L), lambda: idea_K(S, T, C, L), lambda: idea_N(S, T, C, L)):
        try:
            f()
        except Exception:
            import traceback; L.append('ОШИБКА: ' + traceback.format_exc())
    os.makedirs('p2_out', exist_ok=True); open('p2_out/BATCH3.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
