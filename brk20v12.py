#!/usr/bin/env python3
# V1.2 PAPER BOT — слот 3 терминала: «сильный пробой + выход за EMA20» (1h, лонг и шорт).
# Логика 1:1 из бэктеста p2 (ветка mme/label-study: label_study.features, backtest.events, stress.run):
#   сжатие: перцентиль ширины Боллинджера(20) за 60 дней < 30% (на прошлом баре);
#   пробой: закрытие 1h выше максимума / ниже минимума 36 прошлых часов, пауза по монете 24 ч;
#   сила пробоя: балл ≥ 5 из 6 (закрытие ≥ 0.2 ATR за уровнем, фитиль < 25%, тело ≥ 50% и закрытие в краю 30%,
#     объём ≥ 1.5× того же часа за 20 дней, доля агрессора ≥ 55%, OI z > 0.5); сигналы выходных пропускаются;
#   вход по рынку на открытии следующего часа; стоп = уровень пробоя ∓ 0.5 ATR; один добор равной части на +1R
#   (стоп не двигается); выход на открытии часа после закрытия за EMA20 или через 72 ч; издержки 0.08% на сторону, фандинг.
# Риск на первую часть 1% equity, до 8 позиций, одна на монету. Режим БУМАЖНЫЙ: ордеров на бирже нет.
import math
import os
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # тесты без сети
    requests = None

from slotlib import Slot, H

POOL = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT',
        'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'NEARUSDT', 'ATOMUSDT', 'FILUSDT', 'ETCUSDT', 'TRXUSDT', 'UNIUSDT',
        'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT', 'SUIUSDT',
        'XLMUSDT', 'EOSUSDT', 'ALGOUSDT', 'SANDUSDT', 'MANAUSDT', 'AXSUSDT', 'CRVUSDT', 'SNXUSDT', 'MKRUSDT',
        'COMPUSDT', 'GRTUSDT', 'ICPUSDT', 'EGLDUSDT', 'HBARUSDT', 'VETUSDT', 'THETAUSDT', 'RUNEUSDT', 'DYDXUSDT',
        'GALAUSDT', 'CHZUSDT', 'KAVAUSDT', '1INCHUSDT', 'IMXUSDT', 'LDOUSDT', 'STXUSDT', 'FETUSDT', 'WLDUSDT',
        'SEIUSDT', 'TIAUSDT', 'ORDIUSDT']
BASE = 'https://fapi.binance.com'
K = 0.0005 + 0.0003          # комиссия тейкера + проскальзывание, на сторону
RISK, MAXPOS, MAXH = 0.01, 8, 72
ENGINE = 'v1.2-1'
DIR = os.path.dirname(os.path.abspath(__file__))
SLOT = Slot('slot3', 'V1.2', DIR, eq0=1000.0, risk=RISK, engine=ENGINE)
NAN = float('nan')
TREND_FILTER = False          # слот 9: пропускать пробои против дневного тренда (закрытие вчерашнего дня vs EMA50 дней)


# ---------------------------------------------------------------- индикаторы (как в label_study.features)
def ema(x, n=20):
    a = 2 / (n + 1); out = [x[0]]
    for v in x[1:]:
        out.append(a * v + (1 - a) * out[-1])
    return out


def atr_wilder(h, l, c):
    n = len(c); tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    out = [NAN] * n
    if n < 15:
        return out
    a = sum(tr[:14]) / 14
    for i in range(14, n):
        a = a * 13 / 14 + tr[i] / 14; out[i] = a
    return out


def bb_width(c):
    out = [NAN] * len(c)
    for i in range(19, len(c)):
        w = c[i - 19:i + 1]; m = sum(w) / 20
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / 20)
        out[i] = 4 * sd / m
    return out


def bpct_at(bbw, i):
    """Доля последних 1440 значений ширины, меньших текущей (на баре i)."""
    if i < 1460:
        return NAN
    cur = bbw[i]; win = bbw[i - 1440:i]
    return sum(1 for v in win if v < cur) / 1440


def rvol_at(qv, i):
    if i < 480:
        return NAN
    past = qv[i - 480:i:24]; m = sum(past) / len(past)
    return qv[i] / m if len(past) == 20 and m > 0 else NAN


def doi4_series(t, oi):
    n = len(t); out = [NAN] * n
    for i in range(4, n):
        a, b = oi[i - 4], oi[i]
        if t[i] - t[i - 4] == 4 * H and a and b and a > 0 and b > 0:
            v = b / a - 1
            out[i] = v if abs(v) <= 0.5 else NAN
    return out


def zoi_at(d4, i, win=720, minn=300):
    if i >= len(d4) or d4[i] != d4[i]:
        return NAN
    vals = [v for v in d4[max(0, i - win):i] if v == v]
    if len(vals) < minn:
        return NAN
    mu = sum(vals) / len(vals); var = sum(v * v for v in vals) / len(vals) - mu * mu
    return d4[i] / math.sqrt(var) if var > 0 else NAN


class Series:
    """Закрытые 1h-бары одной монеты: t,o,h,l,c,qv,tb (+oi по тем же часам) и индикаторы."""
    def __init__(self, rows, oi=None):
        self.t = [r[0] for r in rows]; self.o = [r[1] for r in rows]; self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]; self.c = [r[4] for r in rows]; self.qv = [r[5] for r in rows]
        self.tb = [r[6] for r in rows]
        self.oi = [(oi or {}).get(x) for x in self.t]
        self.atr = atr_wilder(self.h, self.l, self.c)
        self.bbw = bb_width(self.c)
        self.ema = ema(self.c)
        self.d4 = doi4_series(self.t, self.oi)

    def hi36(self, i):
        return max(self.h[i - 36:i])

    def lo36(self, i):
        return min(self.l[i - 36:i])

    def score(self, i, s, lvl):
        c, o, h, l = self.c[i], self.o[i], self.h[i], self.l[i]
        a = self.atr[i]; rng = (h - l) or 1e-12
        body = abs(c - o) / rng
        cpos = (c - l) / rng if s == 1 else (h - c) / rng
        wick = (h - max(o, c)) / rng if s == 1 else (min(o, c) - l) / rng
        tk = self.tb[i] / self.qv[i] if self.qv[i] > 0 else NAN
        rv = rvol_at(self.qv, i); z = zoi_at(self.d4, i)
        parts = [s * (c - lvl) >= 0.2 * a, wick < 0.25, body >= 0.5 and cpos >= 0.7,
                 (rv if rv == rv else 0) >= 1.5, (tk >= 0.55) if s == 1 else (tk <= 0.45), z == z and z > 0.5]
        return sum(bool(x) for x in parts), dict(rvol=rv, taker=tk, zoi=z, body=body, wick=wick, cpos=cpos)


def breakouts(S, start=1461):
    """Пробои после сжатия с паузой 24 бара, как backtest.events (без требования будущих баров)."""
    out, last = [], -10 ** 9
    for i in range(max(start, 1461), len(S.c)):
        if i - last < 24:
            continue
        hi, lo = S.hi36(i), S.lo36(i)
        up, dn = S.c[i] > hi, S.c[i] < lo
        if not (up or dn):          # порядок проверок не меняет результат: пауза ставится, только если выполнены обе
            continue
        bp = bpct_at(S.bbw, i - 1)
        if not (bp == bp and bp < 0.30):
            continue
        last = i
        out.append((i, 1 if up else -1, hi if up else lo))
    return out


def weekday_ok(t_open):
    return datetime.fromtimestamp((t_open + H) / 1000, timezone.utc).weekday() < 5


def replay(bars, em, j0, s, e1, stop, lvl, risk, next_open=None):
    """Проигрывает сделку с бара j0 (бар входа) по закрытым барам. 1:1 со stress.run.
    bars: список (t,o,h,l,c); em: EMA20 тех же баров; next_open: открытие ещё не закрытого бара (для выхода).
    Возвращает dict: done, px, j_exit, out, units."""
    units = [e1]; added = False
    last = min(len(bars), j0 + MAXH)
    for j in range(j0, last):
        t, o, h, l, c = bars[j][:5]
        if (s == 1 and l <= stop) or (s == -1 and h >= stop):
            return dict(done=True, px=min(o, stop) if s == 1 else max(o, stop), j_exit=j, out='stop', units=units)
        if not added:
            lv = e1 + s * risk
            if (s == 1 and h >= lv) or (s == -1 and l <= lv):
                units.append(max(o, lv) if s == 1 else min(o, lv)); added = True
        if (s == 1 and c < em[j]) or (s == -1 and c > em[j]):
            if j + 1 < len(bars):
                return dict(done=True, px=bars[j + 1][1], j_exit=j + 1, out='ema20', units=units)
            if next_open is not None:
                return dict(done=True, px=next_open, j_exit=j + 1, out='ema20', units=units)
            return dict(done=False, units=units, exit_next=True)
        if j == j0 + MAXH - 1:
            return dict(done=True, px=c, j_exit=j, out='time72', units=units)
    return dict(done=False, units=units)


def pnl_of(s, units, qty, px, fund_sum=0.0):
    pnl = sum(s * (px - u) for u in units) * qty - K * sum(units) * qty - K * px * qty * len(units)
    return pnl - s * fund_sum * sum(units) * qty


# ---------------------------------------------------------------- биржа
def _get(path, params, timeout=15):
    for attempt in range(3):
        try:
            r = requests.get(BASE + path, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            raise RuntimeError(f'HTTP {r.status_code} {r.text[:100]}')
        except Exception as e:
            if attempt == 2:
                SLOT.log(f'WARN {path} {params.get("symbol", "")}: {e}')
            time.sleep(2)
    return None


def fetch_rows(sym, n_old=200):
    """~1700 баров 1h: закрытые (t,o,h,l,c,qv,tb) + открытие формирующегося бара."""
    a = _get('/fapi/v1/klines', {'symbol': sym, 'interval': '1h', 'limit': 1500})
    if not a:
        return None, None
    b = _get('/fapi/v1/klines', {'symbol': sym, 'interval': '1h', 'limit': n_old, 'endTime': int(a[0][0]) - 1}) or []
    now = int(time.time() * 1000)
    rows, forming = {}, None
    for x in b + a:
        t = int(x[0])
        row = (t, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7]), float(x[10]))
        if t + H <= now:
            rows[t] = row
        else:
            forming = row
    return [rows[k] for k in sorted(rows)], forming


def fetch_oi(sym):
    out = {}
    end = None
    for _ in range(2):
        p = {'symbol': sym, 'period': '1h', 'limit': 500}
        if end:
            p['endTime'] = end
        js = _get('/futures/data/openInterestHist', p)
        if not js:
            break
        for x in js:
            ts = int(x['timestamp']); out[((ts - 1) // H) * H] = float(x['sumOpenInterest'])
        end = min(int(x['timestamp']) for x in js) - 1
        if len(js) < 500:
            break
    return out


def fetch_funding(sym, t0, t1):
    js = _get('/fapi/v1/fundingRate', {'symbol': sym, 'startTime': t0, 'endTime': t1 - 1, 'limit': 1000}) or []
    return sum(float(x['fundingRate']) for x in js if t0 <= int(x['fundingTime']) < t1)


def day_trend(sym, t_bar):
    """Знак (закрытие − EMA50) последнего закрытого дня ДО дня бара t_bar (UTC), как в p2/v12_improve.daily_trend.
    0 — если данных мало или биржа не ответила (тогда сделка не отсеивается, как в бэктесте)."""
    js = _get('/fapi/v1/klines', {'symbol': sym, 'interval': '1d', 'limit': 1000})
    if not js:
        return 0
    day0 = (t_bar // (24 * H)) * 24 * H
    closes = [float(x[4]) for x in js if int(x[0]) < day0]
    return day_trend_of(closes)


def day_trend_of(closes):
    if len(closes) < 60:
        return 0
    e = ema(closes, 50)
    d = closes[-1] - e[-1]
    return (d > 0) - (d < 0)


def active_pool():
    js = _get('/fapi/v1/exchangeInfo', {}, timeout=20)
    if not js:
        return list(POOL), []
    ok = {x['symbol'] for x in js['symbols'] if x.get('status') == 'TRADING' and x.get('contractType') == 'PERPETUAL'}
    return [s for s in POOL if s in ok], [s for s in POOL if s not in ok]


# ---------------------------------------------------------------- бот
def manage(st, pos):
    rows, forming = fetch_rows(pos['sym'], n_old=0)
    if not rows:
        return
    bars = [r[:5] for r in rows[-400:]]
    em = ema([b[4] for b in bars])
    idx = {b[0]: k for k, b in enumerate(bars)}
    j0 = idx.get(pos['entry_t'])
    if j0 is None:
        if forming and forming[0] == pos['entry_t']:
            return      # бар входа ещё формируется
        SLOT.log(f"WARN {pos['sym']}: бар входа не найден в истории — позиция закрыта по рынку")
        close_now(st, pos, 'нет истории бара входа', price=bars[-1][4]); return
    r = replay(bars, em, j0, pos['s'], pos['entry'], pos['stop'], pos['lvl'], pos['risk_px'],
               next_open=forming[1] if forming else None)
    pos['units'] = r['units']; pos['added'] = len(r['units']) > 1
    pos['notional'] = sum(r['units']) * pos['qty']
    pos['bars'] = len(bars) - j0
    pos['ema20'] = em[-1]
    if not r['done']:
        return
    t_exit = bars[r['j_exit']][0] if r['j_exit'] < len(bars) else (forming[0] if forming else int(time.time() * 1000))
    fund = fetch_funding(pos['sym'], pos['entry_t'], t_exit + H)
    pnl = pnl_of(pos['s'], r['units'], pos['qty'], r['px'], fund)
    SLOT.book_trade(st, pos, r['px'], t_exit, r['out'], pnl, {'units': len(r['units']), 'funding': fund})


def close_now(st, pos, note=None, price=None):
    if price is None:
        js = _get('/fapi/v1/ticker/price', {'symbol': pos['sym']})
        price = float(js['price']) if js else pos['entry']
    units = pos.get('units') or [pos['entry']]
    pnl = pnl_of(pos['s'], units, pos['qty'], price)
    SLOT.book_trade(st, pos, price, int(time.time() * 1000), 'manual', pnl,
                    {'note': str(note)[:300]} if note else None)


def scan(st, pool):
    if st.get('paused'):
        return
    for sym in pool:
        if len(st['positions']) >= MAXPOS:
            SLOT.log('MAXPOS: 8 позиций открыто — новые сигналы пропускаются до закрытия')
            return
        if any(p['sym'] == sym for p in st['positions']):
            continue
        rows, forming = fetch_rows(sym)
        if not rows or len(rows) < 1500 or not forming:
            continue
        S = Series(rows)
        i = len(rows) - 1
        evs = [e for e in breakouts(S, start=i - 220) if e[0] == i]
        if not evs or st.setdefault('seen', {}).get(sym) == S.t[i]:
            continue
        st['seen'][sym] = S.t[i]
        _, s, lvl = evs[0]
        S = Series(rows, fetch_oi(sym))          # OI нужен только для балла
        sc, why = S.score(i, s, lvl)
        side = 'long' if s == 1 else 'short'
        if sc < 5:
            SLOT.log(f'SKIP {sym}: пробой {side} слабый, балл {sc}/6'); continue
        if not weekday_ok(S.t[i]):
            SLOT.log(f'SKIP {sym}: сильный пробой {side} в выходные — правило v1.2 их пропускает'); continue
        if TREND_FILTER:
            dtr = day_trend(sym, S.t[i])
            if dtr * s < 0:
                SLOT.log(f"SKIP {sym}: сильный пробой {side}, но дневной тренд {'вниз' if dtr < 0 else 'вверх'} — "
                         f"слот 9 берёт только сделки по тренду"); continue
        a = S.atr[i]; e1 = forming[1]; stop = lvl - s * 0.5 * a; risk = abs(e1 - stop)
        if risk <= 0 or s * (e1 - stop) <= 0:
            SLOT.log(f'SKIP {sym}: вход {e1:.6g} уже за стопом {stop:.6g}'); continue
        r_usd = st['eq'] * st.get('risk', RISK); qty = r_usd / risk
        ctx = {'sig_t': S.t[i], 'score': sc, 'lvl': lvl, 'atr': a, **{k: round(v, 3) for k, v in why.items() if v == v},
               'reason': f"{'пробой вверх' if s == 1 else 'пробой вниз'} 36-часового {'максимума' if s == 1 else 'минимума'} "
                         f"{lvl:.6g} после сжатия, балл {sc}/6; стоп = уровень {'−' if s == 1 else '+'} 0.5 ATR"}
        pos = {'sym': sym, 'side': side, 's': s, 'entry': e1, 'lvl': lvl, 'stop': stop, 'risk_px': risk,
               'qty': qty, 'r_usd': r_usd, 'dist': risk / e1, 'notional': qty * e1, 'rem': 1.0, 'stage': 0,
               'units': [e1], 'added': False, 'add_at': e1 + s * risk, 'entry_t': forming[0], 'ctx': ctx}
        st['positions'].append(pos)
        SLOT.log(f"FILL {sym} {side} по рынку @ {e1:.6g} | уровень {lvl:.6g} | стоп {stop:.6g} | добор @ {pos['add_at']:.6g} "
                 f"| риск {r_usd:.2f}$ | балл {sc}/6")


def main():
    SLOT.log(f'=== {SLOT.name} PAPER BOT стартовал{" (только по дневному тренду)" if TREND_FILTER else ""} | пул {len(POOL)} монет | риск {RISK * 100:.0f}% | бумажный режим ===')
    st = SLOT.load_state()
    pool, missing = active_pool(); pool_t = time.time()
    if missing:
        SLOT.log(f'пул: нет на бирже / не торгуются — {", ".join(missing)}')
    st['pool'], st['pool_missing'] = pool, missing
    while True:
        try:
            SLOT.apply_controls(st, close_now)
            if time.time() - pool_t > 6 * 3600:
                pool, missing = active_pool(); pool_t = time.time(); st['pool'], st['pool_missing'] = pool, missing
            last_closed = (int(time.time() * 1000) // H - 1) * H
            if st.get('last_bar', 0) < last_closed:
                for pos in list(st['positions']):
                    manage(st, pos)
                scan(st, pool)
                st['last_bar'] = last_closed
            SLOT.save_state(st)
            SLOT.write_status(st)
        except Exception as e:
            SLOT.log(f'ERROR main loop: {e}')
        time.sleep(60)


if __name__ == '__main__':
    main()
