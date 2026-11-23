#!/usr/bin/env python3
"""Отскок после каскада ликвидаций (54 монеты, 1h, OI). См. p2/PREREG.md, раздел «Каскады».
Событие на закрытии часа i (только прошлые данные): цена за W часов ушла на ≥ D·ATR (ATR на начало окна),
OI за те же W часов упал на ≥ X% (закрываются позиции — ликвидации), объём часа i ≥ RV × средний за 20 дней.
Падение + падение OI = выбили лонги → покупаем на открытии i+1. Рост + падение OI = выбили шорты → шортим.
Стоп: экстремум каскада ∓ SB·ATR. Тейк: возврат TP-доли каскада (от экстремума к началу окна). Выход по времени MH ч.
Пауза по монете 12 ч после события. Издержки 0.12% на сторону (во время каскада спред шире). Фандинг не учтён."""
import glob, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B  # noqa: E402
import robust_ema as RE  # noqa: E402
from hedge import atr  # noqa: E402
ls = B.ls; H, HOLD = B.H, B.HOLD
COST = 0.0012


def events(d, AT, RV, side, W, D, X, rvm):
    o, h, l, c, t, oi = d['o'], d['h'], d['l'], d['c'], d['t'], d['oi']
    out, last = [], -10 ** 9
    for i in range(500, len(c) - 2):
        if i - last < 12 or t[i] - t[i - W] != W * H:
            continue
        a0 = AT[i - W]
        mv = (c[i] - c[i - W]) / a0
        if side == 1 and mv > -D or side == -1 and mv < D:
            continue
        o0, o1 = oi[i - W], oi[i]
        if not (np.isfinite(o0) and np.isfinite(o1) and o0 > 0) or o1 / o0 - 1 > -X:
            if X > -1:  # X = -9 → контроль без условия на OI
                continue
        if rvm and not (RV[i] >= rvm):
            continue
        ext = min(l[i - W + 1:i + 1]) if side == 1 else max(h[i - W + 1:i + 1])
        out.append(dict(i=i, s=side, ext=ext, top=c[i - W], a=a0)); last = i
    return out


def trade(d, e, SB, TP, MH):
    o, h, l, c, t = d['o'], d['h'], d['l'], d['c'], d['t']
    s, j0 = e['s'], e['i'] + 1
    if j0 + MH >= len(c):
        return None
    en = o[j0]; st = e['ext'] - s * SB * e['a']; risk = s * (en - st)
    tp = e['ext'] + TP * (e['top'] - e['ext'])
    if risk <= 0 or s * (tp - en) <= 0:
        return None
    for j in range(j0, j0 + MH):
        if (s == 1 and l[j] <= st) or (s == -1 and h[j] >= st):
            x = min(o[j], st) if s == 1 else max(o[j], st); break
        if (s == 1 and h[j] >= tp) or (s == -1 and l[j] <= tp):
            x = max(o[j], tp) if s == 1 else min(o[j], tp); break
    else:
        j = j0 + MH - 1; x = c[j]
    R = (s * (x - en) - COST * (en + x)) / risk
    return dict(R=R, t=int(t[j0]), tx=int(t[j] + H), rr=s * (tp - en) / risk, sd=risk / en)


def main():
    d1, d2 = sys.argv[1], sys.argv[2]; U = []
    for dd in (d1, d2):
        ls.DATA = dd
        for s in sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{dd}/*_1h.csv')):
            d = ls.load(s)
            if d is None or len(d['c']) < 2000:
                continue
            qv = d['qv']; RV = np.full(len(qv), np.nan)
            cs = np.concatenate([[0], np.cumsum(qv)])
            RV[480:] = qv[480:] / ((cs[480:-1] - cs[:-481]) / 480)
            U.append((s, dd == d1, d, atr(d['h'], d['l'], d['c']), RV))
    #       name                                              side W  D    X      RV  SB   TP   MH
    VS = [('ОСНОВНОЙ: лонг, 3ч, −3 ATR, OI −3%, RV2, тейк 50%', 1, 3, 3.0, 0.03, 2.0, 0.5, 0.5, 24),
          ('контроль: то же без условия на OI', 1, 3, 3.0, -9, 2.0, 0.5, 0.5, 24),
          ('лонг, 1ч, −2.5 ATR, OI −2%', 1, 1, 2.5, 0.02, 2.0, 0.5, 0.5, 24),
          ('лонг, 3ч, −4 ATR, OI −5%', 1, 3, 4.0, 0.05, 2.0, 0.5, 0.5, 24),
          ('лонг, 3ч, −3 ATR, OI −3%, тейк 30%', 1, 3, 3.0, 0.03, 2.0, 0.5, 0.3, 24),
          ('лонг, 3ч, −3 ATR, OI −3%, тейк 75%, 48ч', 1, 3, 3.0, 0.03, 2.0, 0.5, 0.75, 48),
          ('лонг, 3ч, −3 ATR, OI −3%, стоп 1 ATR', 1, 3, 3.0, 0.03, 2.0, 1.0, 0.5, 24),
          ('лонг, 6ч, −4 ATR, OI −4%', 1, 6, 4.0, 0.04, 1.5, 0.5, 0.5, 24),
          ('ШОРТ после сквиза: 3ч, +3 ATR, OI −3%', -1, 3, 3.0, 0.03, 2.0, 0.5, 0.5, 24),
          ('шорт: контроль без OI', -1, 3, 3.0, -9, 2.0, 0.5, 0.5, 24),
          ('шорт: 3ч, +4 ATR, OI −5%', -1, 3, 4.0, 0.05, 2.0, 0.5, 0.5, 24)]
    L = ['# Отскок после каскада ликвидаций (54 монеты, 1h)', '', 'Издержки 0.12% на сторону. R = в долях риска до стопа.', '',
         '| вариант | выборка | сделок | WR | средн. R [95% ДИ] | PF | ΣR | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    ALL = {}
    for name, side, W, D, X, rv, SB, TP, MH in VS:
        tr = []
        for s, dev, d, AT, RV in U:
            for e in events(d, AT, RV, side, W, D, X, rv):
                r = trade(d, e, SB, TP, MH)
                if r:
                    r.update(sym=s, dev=dev); tr.append(r)
        ALL[name] = tr
        for lab, sub in (('24 монеты, до июня 2026', [x for x in tr if x['dev'] and x['t'] < HOLD]),
                         ('30 новых, до июня 2026', [x for x in tr if not x['dev'] and x['t'] < HOLD]),
                         ('все 54, июнь–окт 2026', [x for x in tr if x['t'] >= HOLD])):
            L.append(f'| {name} | {lab} ' + B.stats([x['R'] for x in sub]))
    L += ['', '## Детали и капитал (54 монеты, до 8 позиций)', '',
          '| вариант | сделок в год | медиана RR | медиана стопа | R по годам | 1% риска: 2022–май 2026 × / просадка | июнь–окт × | 3% риска: × / просадка |',
          '|---|---|---|---|---|---|---|---|']
    for name in ALL:
        tr = ALL[name]
        if not tr:
            continue
        yrs = {}
        for x in tr:
            y = str(np.datetime64(x['t'], 'ms'))[:4]; yrs[y] = yrs.get(y, 0) + x['R']
        span = (max(x['t'] for x in tr) - min(x['t'] for x in tr)) / (365.25 * 24 * H)
        a = RE.equity([x for x in tr if x['t'] < HOLD], 0.01, 8); b = RE.equity([x for x in tr if x['t'] >= HOLD], 0.01, 8)
        c3 = RE.equity([x for x in tr if x['t'] < HOLD], 0.03, 8)
        L.append(f"| {name} | {len(tr) / span:.0f} | {np.median([x['rr'] for x in tr]):.2f} | {np.median([x['sd'] for x in tr]) * 100:.1f}% | "
                 f"{' '.join(f'{k[2:]}:{v:+.0f}' for k, v in sorted(yrs.items()))} | ×{a[0]:.2f} / {a[1] * 100:.0f}% | ×{b[0]:.2f} | ×{c3[0]:.2f} / {c3[1] * 100:.0f}% |")
    os.makedirs('p2_out', exist_ok=True); open('p2_out/CASCADE.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
