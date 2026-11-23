#!/usr/bin/env python3
# LISTINGS PAPER BOT — слот 4 терминала: шорт новых фьючерсов Binance после слома пампа (правило «под открытие»).
# Логика 1:1 из исследования listings/hourly.py (ветка auto/turtle), 311 сделок 2020–2026, PF 1.48, WR 56%:
#   P0 = открытие первого часа торгов. В первые 7 дней: цена хоть раз была ≥ P0 + 5%, затем часовая свеча
#   (не раньше 6-го часа) закрылась НИЖЕ P0 → шорт по рынку на открытии следующего часа.
#   Стоп = максимум после листинга + 2% (сделка пропускается, если стоп дальше 40% или ближе 1%);
#   тейк −25% от входа; выход по времени через 14 дней. Издержки 0.15% на сторону.
# Только крипто-контракты (акции и TradFi-фьючерсы исключаются по underlyingType). Риск 5% equity на сделку.
# Режим БУМАЖНЫЙ: ордеров на бирже нет.
import os
import time

try:
    import requests
except ImportError:
    requests = None

from slotlib import Slot, H

BASE = 'https://fapi.binance.com'
COST = 0.0015
RISK = 0.05
TP, PUMP, BUF = 0.25, 1.05, 1.02
WATCH_H, MAXH = 7 * 24, 14 * 24
ENGINE = 'listings-1'
DIR = os.path.dirname(os.path.abspath(__file__))
SLOT = Slot('slot4', 'LISTINGS', DIR, eq0=1000.0, risk=RISK, engine=ENGINE)


# ---------------------------------------------------------------- правило (как listings/hourly.py)
def find_signal(k):
    """k: часовые бары с первого часа торгов (t,o,h,l,c). Индекс бара входа и стоп, либо None.
    'wait' — условие ещё может сработать (прошло меньше 7 дней)."""
    if not k:
        return 'wait'
    p0, hh = k[0][1], 0.0
    for j in range(len(k)):
        hh = max(hh, k[j][2])
        if j > WATCH_H:
            return None
        if j >= 6 and k[j][4] < p0 and hh > p0 * PUMP:
            return j + 1, hh * BUF
    return 'wait' if len(k) <= WATCH_H + 1 else None


def simulate(k, i, stop, maxh=MAXH):
    """Шорт с открытия бара i. Стоп раньше тейка внутри бара. dict(done, px, j, out, R)."""
    e = k[i][1]; sd = (stop - e) / e; tpp = e * (1 - TP)
    end = min(len(k), i + maxh)
    for j in range(i, end):
        _, o, h, l, c = k[j][:5]
        if h >= stop:
            x = max(o, stop) if j > i else stop
            return dict(done=True, px=x, j=j, out='stop', R=((e - x) / e - 2 * COST) / sd)
        if l <= tpp:
            x = min(o, tpp) if j > i else tpp
            return dict(done=True, px=x, j=j, out='tp', R=((e - x) / e - 2 * COST) / sd)
    if end == i + maxh:
        x = k[end - 1][4]
        return dict(done=True, px=x, j=end - 1, out='time14d', R=((e - x) / e - 2 * COST) / sd)
    return dict(done=False)


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


def is_crypto(x):
    sub = ' '.join(str(s) for s in x.get('underlyingSubType', [])).lower()
    return x.get('underlyingType', 'COIN') == 'COIN' and 'tradfi' not in sub and 'stock' not in sub


def new_listings(since_ms):
    js = _get('/fapi/v1/exchangeInfo', {}, timeout=20)
    if not js:
        return None
    out = []
    for x in js['symbols']:
        if (x.get('contractType') == 'PERPETUAL' and x.get('quoteAsset') == 'USDT' and x.get('status') == 'TRADING'
                and int(x.get('onboardDate', 0)) >= since_ms):
            out.append((x['symbol'], int(x['onboardDate']), is_crypto(x)))
    return out


def bars_since(sym, t0):
    """Часовые бары с часа листинга: закрытые + формирующийся отдельно."""
    js = _get('/fapi/v1/klines', {'symbol': sym, 'interval': '1h', 'startTime': t0 // H * H, 'limit': 1500})
    if js is None:
        return None, None
    now = int(time.time() * 1000)
    k = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in js]
    closed = [b for b in k if b[0] + H <= now]
    forming = next((b for b in k if b[0] + H > now), None)
    return closed, forming


# ---------------------------------------------------------------- бот
def close_now(st, pos, note=None):
    js = _get('/fapi/v1/ticker/price', {'symbol': pos['sym']})
    px = float(js['price']) if js else pos['entry']
    R = ((pos['entry'] - px) / pos['entry'] - 2 * COST) / pos['dist']
    SLOT.book_trade(st, pos, px, int(time.time() * 1000), 'manual', R * pos['r_usd'],
                    {'note': str(note)[:300]} if note else None)


def step(st, now_ms):
    w = st.setdefault('watch', {})
    found = new_listings(now_ms - (WATCH_H + 24) * H)
    for sym, onboard, crypto in found or []:
        if sym in w:
            continue
        if not crypto:
            w[sym] = {'onboard': onboard, 'state': 'skip', 'why': 'акция / TradFi-контракт — не крипта'}
            SLOT.log(f'LISTING {sym}: не крипто-контракт, пропускаем'); continue
        w[sym] = {'onboard': onboard, 'state': 'watch', 'seen_at': now_ms}
        SLOT.log(f'LISTING {sym}: новый фьючерс, начало торгов {time.strftime("%Y-%m-%d %H:%M", time.gmtime(onboard / 1000))} UTC — наблюдаю')
    # позиции
    for pos in list(st['positions']):
        k, forming = bars_since(pos['sym'], pos['entry_t'])
        if not k:
            continue
        r = simulate(k, 0, pos['stop'])
        if r['done']:
            SLOT.book_trade(st, pos, r['px'], k[r['j']][0], r['out'], r['R'] * pos['r_usd'])
            w.get(pos['sym'], {})['state'] = 'done'
        else:
            pos['bars'] = len(k)
    # наблюдение
    st['pendings'] = []
    for sym, x in w.items():
        if x['state'] != 'watch':
            continue
        k, forming = bars_since(sym, x['onboard'])
        if k is None:
            continue
        sig = find_signal(k)
        if sig is None:
            x['state'] = 'done'; x['why'] = 'за 7 дней слома пампа не было'
            SLOT.log(f'LISTING {sym}: 7 дней без сигнала — снят с наблюдения'); continue
        if sig == 'wait':
            if k:
                p0 = k[0][1]; hh = max(b[2] for b in k)
                st['pendings'].append({'sym': sym, 'side': 'short', 'entry': p0, 'stop': hh * BUF,
                                       'sf': hh * BUF / p0 - 1, 'exp': k[0][0] + WATCH_H * H + H, 'kind': 'watch',
                                       'armed': hh > p0 * PUMP,
                                       'ctx': {'sig_t': k[0][0], 'reason': f'P0 {p0:.6g}, хай {hh:.6g} '
                                               f'({(hh / p0 - 1) * 100:+.1f}%); вход — закрытие часа ниже P0'
                                               + ('' if hh > p0 * PUMP else ', сначала нужен памп ≥ +5%')}})
            continue
        i, stop = sig
        bar_in = k[i] if i < len(k) else (forming if forming and forming[0] == k[-1][0] + H else None)
        if bar_in is None:
            continue
        sig_close = k[i - 1][0] + H
        if sig_close < x.get('seen_at', 0) - H:
            x['state'] = 'done'; x['why'] = 'сигнал был до начала наблюдения — не догоняем'
            SLOT.log(f'LISTING {sym}: сигнал был до запуска бота — пропуск'); continue
        x['state'] = 'done'
        if st.get('paused'):
            SLOT.log(f'LISTING {sym}: сигнал на паузе — пропуск'); continue
        e = bar_in[1]; sd = stop / e - 1
        if sd <= 0.01 or sd > 0.4:
            SLOT.log(f'SKIP {sym}: стоп {sd * 100:.1f}% от входа (допустимо 1–40%)'); continue
        r_usd = st['eq'] * st.get('risk', RISK); p0 = k[0][1]
        pos = {'sym': sym, 'side': 'short', 's': -1, 'entry': e, 'stop': stop, 'tp': e * (1 - TP), 'dist': sd,
               'r_usd': r_usd, 'notional': r_usd / sd, 'rem': 1.0, 'stage': 0, 'entry_t': bar_in[0],
               'ctx': {'sig_t': k[i - 1][0], 'p0': p0, 'hh': stop / BUF,
                       'reason': f'листинг {time.strftime("%Y-%m-%d", time.gmtime(x["onboard"] / 1000))}: памп до '
                                 f'{stop / BUF:.6g} ({(stop / BUF / p0 - 1) * 100:+.0f}% к P0 {p0:.6g}), '
                                 f'час закрылся ниже P0 — памп сломался'}}
        st['positions'].append(pos)
        SLOT.log(f"FILL {sym} шорт @ {e:.6g} | стоп {stop:.6g} ({sd * 100:.1f}%) | тейк {pos['tp']:.6g} | риск {r_usd:.2f}$")
    for sym in [s for s, x in w.items() if x['onboard'] < now_ms - 40 * 24 * H]:
        if not any(p['sym'] == sym for p in st['positions']):
            del w[sym]


def main():
    SLOT.log(f'=== LISTINGS PAPER BOT стартовал | шорт новых листингов | риск {RISK * 100:.0f}% | бумажный режим ===')
    st = SLOT.load_state()
    while True:
        try:
            SLOT.apply_controls(st, close_now)
            last_closed = (int(time.time() * 1000) // H - 1) * H
            if st.get('last_bar', 0) < last_closed:
                step(st, int(time.time() * 1000))
                st['last_bar'] = last_closed
            SLOT.save_state(st)
            SLOT.write_status(st, f" | наблюдаю {sum(1 for x in st.get('watch', {}).values() if x['state'] == 'watch')}")
        except Exception as e:
            SLOT.log(f'ERROR main loop: {e}')
        time.sleep(60)


if __name__ == '__main__':
    main()
