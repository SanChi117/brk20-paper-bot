#!/usr/bin/env python3
# EVENTS PAPER BOT — слот 7 терминала «СОБЫТИЯ»: две событийные идеи, прошедшие проверку на истории (p2/PREREG.md).
#  1) Метка мониторинга Binance: как только в объявлениях Binance выходит «...Monitoring Tag to Include X, Y...»,
#     шорт фьючерсов X, Y по рынку (не раньше чем через 5 минут после публикации, не позже 30 минут), выход через 24 ч,
#     аварийный стоп +20%. Размер: 5% капитала на монету (риск ≈1% при стопе 20%).
#  2) Отскок после пролива (5m, 53 монеты): 5-минутная свеча закрылась на −3% и ниже, ЛИБО цена была на −5% от максимума
#     2 часов и 5-минутная свеча закрылась на +2% и выше → лонг по рынку, выход через 1 час (12 свечей). Пауза по монете 1 ч.
#     Размер: 20% капитала на сделку, не больше 5 сделок одновременно.
#  3) Листинг на Upbit: как только Upbit объявляет новую монету («신규 거래지원 안내»), через 1 час после объявления —
#     шорт её фьючерса на Binance, стоп +10%, выход через 3 дня. Размер: 10% капитала (риск 1% при стопе 10%).
#     История 05.2024–11.2026: 106 сделок, со стопом 10% в среднем +4.3% на сделку (+0.43R), плюс во всех полугодиях.
# Издержки 0.08% на сторону (как в проверке). Режим БУМАЖНЫЙ: ордеров на бирже нет.
import json
import os
import re
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

from slotlib import Slot

BASE = 'https://fapi.binance.com'
CMS = 'https://www.binance.com/bapi/composite/v1/public/cms/article/list/query'
ENGINE = 'events-1'
UPBIT = 'https://api-manager.upbit.com/api/v1/announcements'
UP_FRAC, UP_STOP, UP_DELAY, UP_HOLD = 0.10, 0.10, 60 * 60 * 1000, 72 * 60 * 60 * 1000
DIR = os.path.dirname(os.path.abspath(__file__))
SLOT = Slot('slot7', 'СОБЫТИЯ', DIR, eq0=1000.0, risk=0.01, engine=ENGINE)
COST = 0.0008
M5 = 5 * 60 * 1000
H = 3600 * 1000
TAG_FRAC, TAG_STOP, TAG_HOLD = 0.05, 0.20, 24 * H
TAG_MIN_DELAY, TAG_MAX_DELAY = 5 * 60 * 1000, 30 * 60 * 1000
DIP_FRAC, DIP_MAX, DIP_BARS = 0.20, 5, 12
POOL = ('BTC ETH SOL BNB XRP DOGE ADA AVAX LINK DOT LTC BCH NEAR ATOM FIL ETC TRX UNI AAVE APT ARB OP INJ SUI XLM EOS ALGO '
        'SAND MANA AXS CRV SNX COMP GRT ICP EGLD HBAR VET THETA RUNE DYDX GALA CHZ KAVA 1INCH IMX LDO STX FET WLD SEI TIA '
        'ORDI').split()
POOL = [s + 'USDT' for s in POOL]


# ---------------------------------------------------------------- чистая логика (тестируется)
def parse_tag_title(title):
    """Заголовок объявления -> список тикеров, на которые вешают метку мониторинга (или [])."""
    if not title or not re.search(r'(?i)monitoring tag', title) or not re.search(r'(?i)(extend|add|include)', title):
        return []
    m = re.search(r'(?i)include (.+?)(?:,? remove| on \d|$)', title)
    if not m:
        return []
    parts = re.split(r',\s*|\s+&\s+|\s+and\s+', m.group(1))
    return [p.strip() for p in parts if re.fullmatch(r'[A-Z0-9]{2,12}', p.strip())]


def parse_upbit_title(title):
    """Заголовок уведомления Upbit -> тикеры нового листинга (или [])."""
    if not title or '신규 거래지원 안내' not in title or re.search(r'종료|연기|취소', title):
        return []
    return list(dict.fromkeys(re.findall(r'\(([A-Z0-9]{2,12})\)', title)))


def dip_signal(bars):
    """bars: закрытые 5m-свечи (t,o,h,l,c), последняя — только что закрытая. Возвращает причину входа или None."""
    if len(bars) < 25:
        return None
    t, o, h, l, c = bars[-1]
    r5 = c / o - 1
    if r5 <= -0.03:
        return f'пролив {r5 * 100:.1f}% за 5 минут'
    hi2 = max(b[2] for b in bars[-25:-1])
    if o / hi2 - 1 <= -0.05 and r5 >= 0.02:
        return f'разворот: была {(o / hi2 - 1) * 100:.1f}% от максимума 2 ч, свеча +{r5 * 100:.1f}%'
    return None


def new_position(sym, side, price, now_ms, notional, stop, exit_t, kind, reason):
    s = 1 if side == 'long' else -1
    dist = abs(stop - price) / price if stop else 0.01
    return {'sym': sym, 'side': side, 's': s, 'entry': price, 'entry_t': now_ms, 'stop': stop, 'dist': dist,
            'notional': notional, 'r_usd': notional * 0.01, 'rem': 1.0, 'tp': None, 'exit_t': exit_t, 'kind': kind,
            'ctx': {'reason': reason, 'kind': kind}}


def pnl_of(pos, px):
    s, e = pos['s'], pos['entry']
    return pos['notional'] * (s * (px - e) / e - 2 * COST)


# ---------------------------------------------------------------- биржа
def _get(url, params, timeout=15):
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={'User-Agent': 'Mozilla/5.0', 'clienttype': 'web'})
            if r.status_code == 200:
                return r.json()
            raise RuntimeError(f'HTTP {r.status_code}')
        except Exception as e:
            if attempt == 2:
                SLOT.log(f'WARN {url.split("/")[-1]} {params.get("symbol", params.get("catalogId", ""))}: {e}')
            time.sleep(2)
    return None


def klines5(sym, limit=30):
    js = _get(BASE + '/fapi/v1/klines', {'symbol': sym, 'interval': '5m', 'limit': limit})
    now = int(time.time() * 1000)
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in js or [] if int(x[0]) + M5 <= now]


def price(sym):
    js = _get(BASE + '/fapi/v1/ticker/price', {'symbol': sym})
    return float(js['price']) if js else None


_PERPS = {'t': 0, 'set': set()}


def perp_of(base):
    if time.time() - _PERPS['t'] > 3600 or not _PERPS['set']:
        js = _get(BASE + '/fapi/v1/exchangeInfo', {}, timeout=20)
        if js:
            _PERPS['set'] = {x['symbol'] for x in js['symbols'] if x.get('status') == 'TRADING'
                             and x.get('contractType') == 'PERPETUAL' and x.get('quoteAsset') == 'USDT'}
            _PERPS['t'] = time.time()
    for p in ('', '1000', '10000', '1000000', '1M'):
        if p + base + 'USDT' in _PERPS['set']:
            return p + base + 'USDT'
    return None


def upbit_notices():
    js = _get(UPBIT, {'os': 'web', 'page': 1, 'per_page': 20, 'category': 'trade'})
    out = []
    try:
        for n in js['data']['notices']:
            ts = datetime.fromisoformat(n.get('first_listed_at') or n['listed_at']).timestamp()
            out.append((int(ts * 1000), n['title'], str(n['id'])))
    except Exception:
        pass
    return out


def announcements():
    out = []
    for cat in (49, 161, 48):
        js = _get(CMS, {'type': 1, 'catalogId': cat, 'pageNo': 1, 'pageSize': 20})
        try:
            out += [(int(a['releaseDate']), a['title'], a.get('code') or a['title']) for a in js['data']['catalogs'][0]['articles']]
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- бот
def close_pos(st, pos, px, t, out):
    SLOT.book_trade(st, pos, px, t, out, pnl_of(pos, px), {'kind': pos['kind']})


def step_tags(st, now):
    seen = set(st.setdefault('seen_ann', []))
    first_run = not st.get('ann_init')
    for t, title, key in announcements():
        if key in seen:
            continue
        seen.add(key)
        coins = parse_tag_title(title)
        if not coins:
            continue
        if first_run:
            continue                                  # старые объявления при первом запуске не торгуем
        age = now - t
        if age > TAG_MAX_DELAY:
            SLOT.log(f'SKIP метка «{title[:80]}»: увидели через {age / 60000:.0f} мин, поздно'); continue
        st.setdefault('tag_queue', []).append({'t': t, 'title': title, 'coins': coins})
        SLOT.log(f'МЕТКА МОНИТОРИНГА: {", ".join(coins)} — шорт через 5 минут после публикации')
    st['seen_ann'] = list(seen)[-500:]
    st['ann_init'] = True
    keep = []
    for q in st.get('tag_queue', []):
        if now - q['t'] < TAG_MIN_DELAY:
            keep.append(q); continue
        for c in q['coins']:
            sym = perp_of(c)
            if not sym:
                SLOT.log(f'SKIP {c}: нет фьючерса USDT-M'); continue
            if any(p['sym'] == sym for p in st['positions']):
                continue
            px = price(sym)
            if not px:
                continue
            pos = new_position(sym, 'short', px, now, st['eq'] * TAG_FRAC, px * (1 + TAG_STOP), now + TAG_HOLD, 'tag',
                               f'метка мониторинга Binance: «{q["title"][:120]}»')
            st['positions'].append(pos)
            SLOT.log(f'FILL {sym} шорт @ {px:.6g} | {pos["notional"]:.2f}$ | стоп {pos["stop"]:.6g} (+20%) | выход через 24 ч')
    st['tag_queue'] = keep


def step_upbit(st, now):
    seen = set(st.setdefault('seen_up', []))
    first_run = not st.get('up_init')
    for t, title, key in upbit_notices():
        if key in seen:
            continue
        seen.add(key)
        coins = parse_upbit_title(title)
        if not coins or first_run:
            continue
        if now - t > TAG_MAX_DELAY:
            SLOT.log(f'SKIP Upbit «{title[:60]}»: увидели через {(now - t) / 60000:.0f} мин'); continue
        st.setdefault('up_queue', []).append({'t': t, 'title': title, 'coins': coins})
        SLOT.log(f'ЛИСТИНГ UPBIT: {", ".join(coins)} — шорт через 1 час после объявления')
    st['seen_up'] = list(seen)[-500:]
    st['up_init'] = True
    keep = []
    for q in st.get('up_queue', []):
        if now - q['t'] < UP_DELAY:
            keep.append(q); continue
        for c in q['coins']:
            sym = perp_of(c)
            if not sym:
                SLOT.log(f'SKIP {c}: нет фьючерса USDT-M на Binance'); continue
            if any(p['sym'] == sym for p in st['positions']):
                continue
            px = price(sym)
            if not px:
                continue
            pos = new_position(sym, 'short', px, now, st['eq'] * UP_FRAC, px * (1 + UP_STOP), now + UP_HOLD, 'upbit',
                               f'листинг на Upbit: «{q["title"][:100]}»')
            st['positions'].append(pos)
            SLOT.log(f'FILL {sym} шорт @ {px:.6g} | {pos["notional"]:.2f}$ | стоп {pos["stop"]:.6g} (+10%) | выход через 3 дня')
    st['up_queue'] = keep


def step_dips(st, now):
    bar_t = now // M5 * M5 - M5                        # последняя закрытая 5m-свеча
    if st.get('last_bar') == bar_t:
        return
    st['last_bar'] = bar_t
    cool = st.setdefault('cooldown', {})
    open_dips = [p for p in st['positions'] if p['kind'] == 'dip']
    for sym in POOL:
        if len(open_dips) >= DIP_MAX:
            break
        if cool.get(sym, 0) > now or any(p['sym'] == sym for p in st['positions']):
            continue
        bars = klines5(sym)
        if not bars or bars[-1][0] != bar_t:
            continue
        why = dip_signal(bars)
        if not why:
            continue
        px = price(sym) or bars[-1][4]
        pos = new_position(sym, 'long', px, now, st['eq'] * DIP_FRAC, None, bar_t + M5 + DIP_BARS * M5, 'dip', why)
        st['positions'].append(pos); open_dips.append(pos); cool[sym] = now + H
        SLOT.log(f'FILL {sym} лонг @ {px:.6g} | {pos["notional"]:.2f}$ | {why} | выход через 1 ч')
    st['cooldown'] = {k: v for k, v in cool.items() if v > now}


def step_exits(st, now):
    for pos in list(st['positions']):
        if pos['kind'] in ('tag', 'upbit'):
            bars = klines5(pos['sym'], 1000)
            hit = next((b for b in bars if b[0] >= pos['entry_t'] // M5 * M5 and b[2] >= pos['stop']), None)
            if hit:
                close_pos(st, pos, max(hit[1], pos['stop']), hit[0], 'stop'); continue
        if now >= pos['exit_t']:
            px = price(pos['sym'])
            if px:
                close_pos(st, pos, px, now, 'time')


def main():
    SLOT.log(f'=== EVENTS PAPER BOT стартовал | метка мониторинга (шорт 24 ч) + листинг Upbit (шорт 3 дня) + отскок после пролива (лонг 1 ч), {len(POOL)} монет ===')
    while True:
        try:
            st = SLOT.load_state()
            now = int(time.time() * 1000)
            SLOT.apply_controls(st, lambda st_, pos, note=None: close_pos(st_, pos, price(pos['sym']) or pos['entry'], now, 'manual'))
            step_exits(st, now)
            if not st.get('paused'):
                step_tags(st, now)
                step_upbit(st, now)
                step_dips(st, now)
            SLOT.save_state(st)
            SLOT.write_status(st, f" | позиций по метке {sum(1 for p in st['positions'] if p['kind'] == 'tag')}")
        except Exception as e:
            SLOT.log(f'ERROR: {e}')
        time.sleep(20)


if __name__ == '__main__':
    main()
