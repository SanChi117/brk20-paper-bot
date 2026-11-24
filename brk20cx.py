#!/usr/bin/env python3
# CRYPTOX COPY PAPER BOT — слоты 5–6 терминала: бумажное копирование сигналов закрытого канала «CryptoX | ProTrading».
# Бот только ЧИТАЕТ канал вашим аккаунтом Telegram (сессия из tg.sh) и ведёт сделки на бумаге по правилам канала:
#   вход по рынку в момент поста (цена Binance USDT-M), размер как у канала: «Входим на X$ / Банк Y$» (по умолчанию 10% банка),
#   плечо из поста (первое число в «25-30x»), на первом тейке закрыть 50% и стоп в безубыток, остаток на втором тейке,
#   максимум 14 дней. Комиссии: 0.05% вход/стоп, 0.02% тейки.
# Два счёта по 100$, как старт марафона канала:
#   slot5 — изолированная маржа: если стоп канала дальше цены ликвидации, позицию закрывает ликвидация (минус вся маржа);
#   slot6 — кросс-маржа: ликвидации нет, работает стоп канала (сигналы «стоп пока не ставлю» держатся до тейка или 14 дней).
# Всё, что канал пишет потом о сделке («взяли тейк», «вышли по стопу») и его «Банк: …$», сохраняется для сравнения.
# Режим БУМАЖНЫЙ: ордеров на бирже нет.
import asyncio
import json
import os
import re
import threading
import time

try:
    import requests
except ImportError:
    requests = None

from slotlib import Slot

BASE = 'https://fapi.binance.com'
CHANNEL_ID = 1184729372
ENGINE = 'cryptox-copy-1'
DIR = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.join(DIR, 'tg_private')
EQ0 = 100.0
TAKER, MAKER, MMR = 0.0005, 0.0002, 0.005
DEF_FRAC, DEF_LEV, MAXH = 0.10, 25, 14 * 24
SLOTS = {'iso': Slot('slot5', 'CRYPTOX ИЗОЛ', DIR, eq0=EQ0, risk=DEF_FRAC, engine=ENGINE),
         'cross': Slot('slot6', 'CRYPTOX КРОСС', DIR, eq0=EQ0, risk=DEF_FRAC, engine=ENGINE)}
H = 3600 * 1000


# ---------------------------------------------------------------- разбор поста
def _num(t):
    try:
        return float(t.replace(',', '.'))
    except ValueError:
        return None


def _nums(s):
    return [v for v in (_num(z) for z in re.findall(r'\d+(?:[.,]\d+)?', s or '')) if v]


def parse_signal(text):
    """Сигнал канала -> dict или None. Формат: «#SYM LONG/SHORT, Плечо 25-30x, Вход: по рынку | цена, Тейки: a b c, Стоп: x»."""
    if not text or not re.search(r'(?i)тейк', text):
        return None
    t = text.replace('Cтоп', 'Стоп').replace('cтоп', 'стоп')         # в канале «C» бывает латинской
    m = re.search(r'#?\$?([A-Za-z0-9]{2,20})\s*(?:/\s*USDT)?\s+(LONG|SHORT)\b', t, re.I)
    if not m:
        return None
    sym, side = m.group(1).upper(), 1 if m.group(2).lower() == 'long' else -1
    if sym.endswith('USDT') and len(sym) > 4:
        sym = sym[:-4]
    lev = re.search(r'(?i)плеч[оe][^\n\d]*(\d+)', t) or re.search(r'(?i)\bx\s?(\d+)\b|(\d+)\s?x\b', t)
    lev = int(next(g for g in lev.groups() if g)) if lev else DEF_LEV
    tp = re.search(r'(?i)тейки?\s*:?\s*([^\n]*)', t)
    tps = _nums(tp.group(1)) if tp else []
    en = re.search(r'(?i)(?:диапазон входа|точка входа|вход)\s*:?\s*([^\n]*)', t)
    entry = None
    if en and not re.search(r'(?i)рынк', en.group(1)):
        v = _nums(en.group(1)); entry = sum(v) / len(v) if v else None
    st = re.search(r'(?i)стоп\s*:?\s*([^\n]*)', t)
    stop = (_nums(st.group(1)) or [None])[0] if st else None
    size = re.search(r'(?i)входим на\s*([\d.,]+)\s*\$', t)
    bank = re.search(r'(?i)банк\s*:?\s*([\d.,]+)\s*\$', t)
    frac = None
    if size and bank and _num(bank.group(1)):
        frac = _num(size.group(1)) / _num(bank.group(1))
        if not 0.005 <= frac <= 0.5:
            frac = None
    if not tps:
        return None
    return {'sym': sym, 'side': side, 'lev': max(1, min(lev, 125)), 'tps': tps[:3], 'stop': stop, 'entry': entry,
            'frac': frac, 'claimed_bank': _num(bank.group(1)) if bank else None}


def claimed_bank(text):
    m = re.search(r'(?i)банк\s*:?\s*([\d.,]+)\s*\$', text or '')
    return _num(m.group(1)) if m else None


# ---------------------------------------------------------------- сделка (чистая логика, тестируется)
def open_position(sig, price, now_ms, equity, mode):
    """Позиция по сигналу на цене price. None + причина, если входить нельзя."""
    s = sig['side']
    tps = [x for x in sig['tps'] if s * (x - price) > 0]
    if not tps or any(s * (tps[i + 1] - tps[i]) <= 0 for i in range(len(tps) - 1)):
        return None, 'цена уже за первым тейком / тейки не по порядку'
    stop = sig['stop']
    if stop is not None and s * (price - stop) <= 0:
        return None, 'цена уже за стопом'
    lev = sig['lev']
    frac = sig['frac'] or DEF_FRAC
    margin = equity * frac
    if margin <= 0.01:
        return None, 'счёт пуст'
    liq = price * (1 - s * (1 / lev - MMR))
    eff, kind = stop, 'стоп канала'
    if mode == 'iso' and (stop is None or s * (stop - liq) < 0):
        eff, kind = liq, 'ликвидация (раньше стопа канала)'
    notional = margin * lev
    dist = abs(price - eff) / price if eff is not None else 0.05
    parts = [0.5, 0.5] if len(tps) >= 2 else [1.0]
    pos = {'sym': None, 'side': 'long' if s == 1 else 'short', 's': s, 'entry': price, 'entry_t': now_ms,
           'stop': eff, 'stop_kind': kind, 'chan_stop': stop, 'liq': liq if mode == 'iso' else None,
           'tps': tps[:len(parts)], 'parts': parts, 'hit': 0, 'tp': tps[0], 'rem': 1.0,
           'margin': margin, 'lev': lev, 'notional': notional, 'dist': dist, 'r_usd': notional * dist,
           'realized': -notional * TAKER, 'last_t': now_ms, 'mode': mode}
    return pos, None


def step_bar(pos, o, h, l, c, first=False):
    """Обработать 1m-бар. Внутри бара стоп раньше тейка (консервативно). Возвращает (закрыта?, причина, цена)."""
    s, e = pos['s'], pos['entry']

    def stop_hit():
        return pos['stop'] is not None and ((s == 1 and l <= pos['stop']) or (s == -1 and h >= pos['stop']))

    if stop_hit():
        st = pos['stop']
        if pos['hit'] == 0 and pos['liq'] is not None and st == pos['liq']:
            pos['realized'] += -pos['margin'] * pos['rem'] + pos['notional'] * TAKER   # вся маржа остатка (комиссию входа уже сняли)
            pos['rem'] = 0
            return True, 'liquidation', st
        px = st if first else (min(o, st) if s == 1 else max(o, st))
        pos['realized'] += pos['rem'] * pos['notional'] * (s * (px - e) / e - TAKER)
        pos['rem'] = 0
        return True, ('be' if pos['hit'] else 'stop'), px
    while pos['hit'] < len(pos['tps']) and ((s == 1 and h >= pos['tps'][pos['hit']]) or (s == -1 and l <= pos['tps'][pos['hit']])):
        k = pos['hit']; px = pos['tps'][k]; part = pos['parts'][k]
        pos['realized'] += part * pos['notional'] * (s * (px - e) / e - MAKER)
        pos['rem'] = round(pos['rem'] - part, 9); pos['hit'] += 1
        if pos['hit'] == 1:
            pos['stop'], pos['stop_kind'], pos['liq'] = e, 'безубыток', None
        if pos['hit'] < len(pos['tps']):
            pos['tp'] = pos['tps'][pos['hit']]
        if pos['rem'] <= 1e-9:
            return True, 'tp' + str(pos['hit']), px
    return False, None, None


def close_market(pos, px):
    s, e = pos['s'], pos['entry']
    pos['realized'] += pos['rem'] * pos['notional'] * (s * (px - e) / e - TAKER)
    pos['rem'] = 0


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
                SLOTS['iso'].log(f'WARN {path} {params.get("symbol", "")}: {e}')
            time.sleep(2)
    return None


_SYMS = {'t': 0, 'set': set()}


def resolve(base, ref_px):
    """Монета канала -> (символ Binance, множитель цены) или (None, причина). Цена сверяется с тейком канала."""
    if time.time() - _SYMS['t'] > 3600 or not _SYMS['set']:
        js = _get('/fapi/v1/exchangeInfo', {}, timeout=20)
        if js:
            _SYMS['set'] = {x['symbol'] for x in js['symbols'] if x.get('status') == 'TRADING'
                            and x.get('contractType') == 'PERPETUAL' and x.get('quoteAsset') == 'USDT'}
            _SYMS['t'] = time.time()
    names = {'TONCOIN': 'TON'}
    b = names.get(base, base)
    cands = [(b + 'USDT', 1.0), ('1000' + b + 'USDT', 1000.0), ('10000' + b + 'USDT', 1e4), ('1000000' + b + 'USDT', 1e6),
             ('1M' + b + 'USDT', 1e6)]
    if b.startswith('1000'):
        cands.append((b[4:] + 'USDT', 0.001))
    for sym, f in cands:
        if sym in _SYMS['set']:
            js = _get('/fapi/v1/ticker/price', {'symbol': sym})
            if not js:
                return None, 'нет цены'
            px = float(js['price'])
            if ref_px and abs(ref_px * f / px - 1) > 0.15:
                continue
            return (sym, f, px), None
    return None, 'монеты нет на Binance USDT-M (или цена не сходится с уровнями канала)'


def klines_1m(sym, start_ms):
    js = _get('/fapi/v1/klines', {'symbol': sym, 'interval': '1m', 'startTime': start_ms, 'limit': 1000})
    now = int(time.time() * 1000)
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in js or [] if int(x[0]) + 60000 <= now]


# ---------------------------------------------------------------- бот
class Bot:
    def __init__(self):
        self.lock = threading.RLock()       # сигналы (поток Telegram) и сопровождение (таймер) не пишут состояние одновременно
        self.st = {k: sl.load_state() for k, sl in SLOTS.items()}
        for k in self.st:
            self.st[k].setdefault('claimed', []); self.st[k].setdefault('skips', [])

    def save(self):
        for k, sl in SLOTS.items():
            st = self.st[k]
            sl.save_state(st)
            last = st['claimed'][-1][1] if st['claimed'] else None
            sl.write_status(st, f" | банк канала {last:.2f}$" if last else '')

    def book(self, k, pos, px, t, out):
        pnl = pos['realized']
        SLOTS[k].book_trade(self.st[k], pos, px, t, out, pnl,
                            {'hit': pos['hit'], 'margin': round(pos['margin'], 4), 'lev': pos['lev'],
                             'roe': round(pnl / pos['margin'] * 100, 1) if pos['margin'] else 0,
                             'chan_notes': pos['ctx'].get('notes', [])[-6:]})

    def on_signal(self, msg_id, text, t_ms, live=True):
        with self.lock:
            return self._on_signal(msg_id, text, t_ms, live)

    def _on_signal(self, msg_id, text, t_ms, live=True):
        sig = parse_signal(text)
        if not sig:
            return
        if not live:
            for k in SLOTS:
                self.st[k]['skips'].append({'id': msg_id, 'sym': sig['sym'], 't': t_ms, 'why': 'пост вышел, пока бот был выключен'})
            SLOTS['iso'].log(f"SKIP #{sig['sym']}: пост вышел, пока бот не работал — по старой цене не входим")
            return
        res, why = resolve(sig['sym'], sig['entry'] or sig['tps'][0])
        if not res:
            for k in SLOTS:
                self.st[k]['skips'].append({'id': msg_id, 'sym': sig['sym'], 't': t_ms, 'why': why})
            SLOTS['iso'].log(f"SKIP #{sig['sym']}: {why}")
            return
        sym, f, px = res
        sig = dict(sig, tps=[x * f for x in sig['tps']], stop=sig['stop'] * f if sig['stop'] else None)
        if sig['claimed_bank']:
            for k in SLOTS:
                self.st[k]['claimed'].append([t_ms, sig['claimed_bank']])
        for k, sl in SLOTS.items():
            st = self.st[k]
            if st.get('paused'):
                sl.log(f'PAUSE: сигнал #{sig["sym"]} пропущен'); continue
            if any(p['sym'] == sym for p in st['positions']):
                sl.log(f'SKIP {sym}: позиция уже открыта'); continue
            pos, why = open_position(sig, px, t_ms, st['eq'], k)
            if not pos:
                st['skips'].append({'id': msg_id, 'sym': sym, 't': t_ms, 'why': why}); sl.log(f'SKIP {sym}: {why}'); continue
            pos['sym'] = sym
            stop_txt = f"{sig['stop']:.6g}" if sig['stop'] else 'не указан'
            pos['ctx'] = {'msg_id': msg_id, 'base': sig['sym'], 'notes': [],
                          'reason': f"пост канала #{msg_id}: {sig['sym']} {pos['side']} x{sig['lev']}, тейки "
                                    f"{' '.join(f'{x:.6g}' for x in sig['tps'])}, стоп "
                                    f"{stop_txt}; доля банка "
                                    f"{(sig['frac'] or DEF_FRAC) * 100:.1f}%"}
            st['positions'].append(pos)
            sl.log(f"FILL {sym} {pos['side']} @ {px:.6g} | маржа {pos['margin']:.2f}$ x{pos['lev']} | "
                   f"тейки {' '.join(f'{x:.6g}' for x in pos['tps'])} | выход по {pos['stop_kind']} "
                   f"{pos['stop']:.6g}" if pos['stop'] else f"FILL {sym} {pos['side']} @ {px:.6g} | без стопа")
        self.save()

    def on_note(self, msg_id, text, t_ms):
        with self.lock:
            return self._on_note(msg_id, text, t_ms)

    def _on_note(self, msg_id, text, t_ms):
        cb = claimed_bank(text)
        if cb:
            for k in SLOTS:
                self.st[k]['claimed'].append([t_ms, cb])
        tags = {x.upper() for x in re.findall(r'#([A-Za-z0-9]{2,20})', text or '')}
        if not tags:
            return
        for k in SLOTS:
            for p in self.st[k]['positions']:
                if p['ctx'].get('base') in tags:
                    p['ctx']['notes'].append({'t': t_ms, 'text': text[:400]})
            for tr in self.st[k]['trades'][-40:]:
                if tr.get('ctx', {}).get('base') in tags and t_ms - (tr.get('exit_t') or 0) < 3 * 24 * H:
                    tr.setdefault('chan_notes', []).append({'t': t_ms, 'text': text[:400]})
        self.save()

    def manage(self):
        with self.lock:
            return self._manage()

    def _manage(self):
        now = int(time.time() * 1000)
        for k, sl in SLOTS.items():
            st = self.st[k]
            sl.apply_controls(st, lambda st_, pos, note=None, k=k: self.close_now(k, pos, note))
            for pos in list(st['positions']):
                bars = klines_1m(pos['sym'], pos['last_t'] // 60000 * 60000)
                done = False
                for b in bars:
                    if b[0] < pos['last_t'] // 60000 * 60000:
                        continue
                    first = b[0] <= pos['entry_t'] < b[0] + 60000
                    if first:      # бар входа: до входа цена могла быть любой — считаем только движение после
                        o = pos['entry']; h = max(pos['entry'], b[4]); l = min(pos['entry'], b[4])
                        done, out, px = step_bar(pos, o, h, l, b[4], first=True)
                    else:
                        done, out, px = step_bar(pos, *b[1:5])
                    pos['last_t'] = b[0] + 60000
                    if done:
                        self.book(k, pos, px, b[0], out); break
                if not done and now - pos['entry_t'] >= MAXH * H:
                    js = _get('/fapi/v1/ticker/price', {'symbol': pos['sym']})
                    if js:
                        px = float(js['price']); close_market(pos, px); self.book(k, pos, px, now, 'time14d')
            if st['eq'] <= 1.0 and not st.get('paused'):
                st['paused'] = True; sl.log('СЧЁТ ОБНУЛЁН: новые сделки остановлены')
        self.save()

    def close_now(self, k, pos, note=None):
        js = _get('/fapi/v1/ticker/price', {'symbol': pos['sym']})
        px = float(js['price']) if js else pos['entry']
        close_market(pos, px)
        self.book(k, pos, px, int(time.time() * 1000), 'manual')


async def run():
    from telethon import TelegramClient, events
    cfg = json.load(open(os.path.join(PRIV, 'api.json')))
    src, dst = os.path.join(PRIV, 'user.session'), os.path.join(PRIV, 'cx.session')
    if not os.path.exists(dst) and os.path.exists(src):
        import shutil
        shutil.copy(src, dst); os.chmod(dst, 0o600)
    client = TelegramClient(dst[:-8], int(cfg['api_id']), cfg['api_hash'])
    await client.connect()
    if not await client.is_user_authorized():
        SLOTS['iso'].log('нет входа в Telegram — один раз запустите на сервере: cd /root/brk20 && bash tg.sh')
        await client.disconnect(); await asyncio.sleep(600); return
    chan = None
    async for d in client.iter_dialogs():
        if getattr(d.entity, 'id', None) == CHANNEL_ID:
            chan = d.entity; break
    if chan is None:
        SLOTS['iso'].log('канал CryptoX | ProTrading не найден среди ваших подписок'); await asyncio.sleep(600); return
    bot = Bot()
    last = bot.st['iso'].get('last_msg_id')
    if last:                                    # что вышло, пока бот не работал: заметки сохраняем, по сигналам не входим
        async for m in client.iter_messages(chan, min_id=last, reverse=True):
            t = int(m.date.timestamp() * 1000)
            if parse_signal(m.message):
                bot.on_signal(m.id, m.message, t, live=(time.time() * 1000 - t) < 5 * 60000)
            else:
                bot.on_note(m.id, m.message, t)
            bot.st['iso']['last_msg_id'] = m.id
    else:
        async for m in client.iter_messages(chan, limit=1):
            bot.st['iso']['last_msg_id'] = m.id
    bot.save()
    SLOTS['iso'].log(f'=== CRYPTOX COPY PAPER BOT стартовал | читаю канал | 2 счёта по {EQ0:.0f}$ (изол/кросс) | бумажный режим ===')

    @client.on(events.NewMessage(chats=chan))
    async def handler(ev):
        m = ev.message; t = int(m.date.timestamp() * 1000)
        try:
            if parse_signal(m.message):
                await asyncio.to_thread(bot.on_signal, m.id, m.message, t)
            else:
                await asyncio.to_thread(bot.on_note, m.id, m.message, t)
            bot.st['iso']['last_msg_id'] = max(bot.st['iso'].get('last_msg_id') or 0, m.id)
        except Exception as e:
            SLOTS['iso'].log(f'ERROR сигнал #{m.id}: {e}')

    async def loop():
        while True:
            try:
                await asyncio.to_thread(bot.manage)
            except Exception as e:
                SLOTS['iso'].log(f'ERROR manage: {e}')
            await asyncio.sleep(30)

    task = asyncio.ensure_future(loop())
    await client.run_until_disconnected()
    task.cancel()


if __name__ == '__main__':
    while True:
        try:
            asyncio.run(run())
        except Exception as e:
            SLOTS['iso'].log(f'ERROR: {e} — перезапуск через 60 с')
        time.sleep(60)
