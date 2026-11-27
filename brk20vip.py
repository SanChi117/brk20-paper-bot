#!/usr/bin/env python3
# VIP CRYPTOX — слот 8 терминала: бумажная копия ветки «Vip CryptoX» один в один с постом.
# Работает внутри процесса brk20cx.py (тот же клиент Telegram, отдельный счёт 1000$, файлы *_slot8.*).
# Из поста берётся: монета, сторона, плечо (первое число из «X10-15»), вход (по рынку, либо лимит/триггер от точки —
# ждём касания до 48 ч), стоп, точка переноса в безубыток («на точке X ставим стоп в б/у», иначе первый тейк),
# тейки и доли фиксации («фиксация 40 процентов», «полное закрытие»; без долей — 50% на первом, остаток на последнем).
# Размер: «риск 1-3% / на стоп 2/3 процента» → риск = нижняя граница; «маржи / от баланса / от депо N%» → маржа = нижняя
# граница; иначе маржа 3% баланса. Кросс-маржа: ликвидации по позиции нет, работает стоп из поста.
# Максимум 14 дней. Комиссии 0.05% вход/стоп, 0.02% тейки. Посты без стопа или без тейков пропускаются с пометкой.
import re
import time

from slotlib import Slot

CHANNEL_ID = 3951515847
ENGINE = 'vip-copy-1'
SLOT = None
TAKER, MAKER = 0.0005, 0.0002
DEF_MARGIN, MAX_HOURS, LIMIT_TTL = 0.03, 14 * 24, 48 * 3600 * 1000
H = 3600 * 1000


def make_slot(directory):
    global SLOT
    SLOT = Slot('slot8', 'VIP CRYPTOX', directory, eq0=1000.0, risk=0.02, engine=ENGINE)
    return SLOT

KW = re.compile(r'(?i)(stop\s*loss|стоп[- ]?лосс|stop|стоп|t\s*/\s*p|тейк|target|\btp\d?\b|🎯|от точки|от цены|на точке|точка|вход(?: в рамках| от|:)?|мой вход)')
NUMRE = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)(?![\d.,]*\s*(?:%|процент|х\b|x\b|-\s*\d))')


def _n(s):
    try:
        return float(s.replace(',', '.'))
    except Exception:
        return None


def _parse_core(text):
    """Сигнал VIP-ветки -> dict(sym, side, stop, tps, be, entry) или None. Цены как в посте (масштаб сверяется с рынком)."""
    if not text:
        return None
    t = text.replace('Cтоп', 'Стоп').replace(' ', ' ').replace('Tейк', 'Тейк').replace('Т/p', 'T/p').replace('Т/р', 'T/p')
    m = re.search(r'(?i)\b([A-Za-z0-9]{2,15})\s*/?\s*USDT\b', t)
    if m:
        sym = m.group(1).upper()
    else:
        names = [(r'(?i)битк|биток|биткоин|\bbtc\b', 'BTC'), (r'(?i)\bэфир|\beth\b', 'ETH'), (r'(?i)солан|\bsol\b', 'SOL'),
                 (r'(?i)лайткоин|\bltc\b', 'LTC')]
        sym = next((v for pat, v in names if re.search(pat, t)), None)
        if not sym:
            return None
    ls = [x.start() for x in re.finditer(r'(?i)\b(long|лонг)', t)]
    ss = [x.start() for x in re.finditer(r'(?i)\b(short|шорт)', t)]
    if not ls and not ss:
        return None
    sd = 1 if ls and (not ss or ls[0] < ss[0]) else -1
    marks = [(x.start(), x.group(1).lower()) for x in KW.finditer(t)]
    stop = None; tps = []; be = None; entry = None
    for i, (pos, kw) in enumerate(marks):
        seg = t[pos + len(kw):(marks[i + 1][0] if i + 1 < len(marks) else len(t))]
        seg = re.sub(r'^\s*\d\s*(?::|\)|\.\s)\s*', ' ', seg)     # «Тейк 1: 0.06» — номер цели
        seg = re.sub(r'^\s*\d\s+(?=\d)', ' ', seg)
        nm = NUMRE.search(seg[:40])
        if not nm:
            continue
        v = _n(nm.group(1))
        if v is None:
            continue
        if kw.startswith(('stop', 'стоп')):
            head = seg[:nm.start()]
            if stop is None and '\n' not in head and 'б/у' not in head and not re.search(r'[A-Za-zА-Яа-яЁё]{3,}', head.replace('лосс', '').replace('loss', '')):
                stop = v
        elif kw in ('на точке', 'точка'):
            be = v
        elif kw.startswith(('вход', 'мой вход', 'от точки', 'от цены')):
            if entry is None:
                entry = v
        else:
            tps.append(v)
    for line in t.split('\n'):                                # «60.955 ставим стоп в б/у», «57.400 полное закрытие»
        lm = re.match(r'\s*[^\d\w]*\s*' + r'(\d+(?:[.,]\d+)?)\s*\$?\s*(.*)', line)
        if lm:
            v, rest = _n(lm.group(1)), lm.group(2).lower()
            if 'б/у' in rest or 'безубыт' in rest:
                be = be or v
            elif re.search(r'фикс|закрыт|тейк', rest):
                tps.append(v)
        if stop is None and re.search(r'(?i)стоп|stop', line) and not re.search(r'б/у|безубыт', line):
            nums = [x for x in NUMRE.findall(line)]
            if nums:
                stop = _n(nums[-1])
    if stop is None or not tps:
        return None

    def norm(v):                                             # «65.500» у BTC = 65 500; «1635» у NEAR = 1.635
        if v is None:
            return None
        for f in (1, 1000, 0.001, 1e6, 1e-6):
            if 0.6 <= v * f / stop <= 1.6:
                return v * f
        return None
    tps = [x for x in (norm(v) for v in tps) if x is not None and abs(x / stop - 1) > 1e-9]
    tps = [x for x in tps if sd * (x - stop) > 0]
    be, entry = norm(be), norm(entry)
    if not tps:
        return None
    return {'sym': sym, 'side': sd, 'stop': stop, 'tps': tps, 'be': be, 'entry': entry}


def _lowpct(m):
    v = [float(x.replace(',', '.')) for x in re.findall(r'\d+(?:[.,]\d+)?', m)]
    return min(v) / 100 if v else None


def parse_vip(text):
    """Полный разбор поста: уровни + плечо, размер, лимит, доли фиксации."""
    core = _parse_core(text)
    if not core:
        return None
    t = text.replace('\u00a0', ' ')
    lev = re.search(r'(?i)(?:\b[xх]\s?(\d{1,3})\b|плеч[оае]\s*:?\s*[xх]?(\d{1,3})|(\d{1,3})\s?[xх]\b)', t)
    lev = int(next(g for g in lev.groups() if g)) if lev else 10
    risk = re.search(r'(?i)(?:риск[^\n%]{0,30}?|на стоп[^\n%]{0,30}?)(\d+(?:[.,]\d+)?\s*(?:[-/]\s*\d+(?:[.,]\d+)?)?)\s*(?:%|процент)', t)
    marg = re.search(r'(?i)(\d+(?:[.,]\d+)?\s*(?:[-/]\s*\d+(?:[.,]\d+)?)?)\s*(?:%|процент\w*)\s*(?:от\s*(?:баланса|депо\w*|депозита))', t) \
        or re.search(r'(?i)(?:маржи|вкладываю|выделяю|вложу)[^\n%]{0,20}?(\d+(?:[.,]\d+)?\s*(?:[-/]\s*\d+(?:[.,]\d+)?)?)\s*(?:%|процент)', t)
    size = ('risk', _lowpct(risk.group(1))) if risk else (('margin', _lowpct(marg.group(1))) if marg else ('margin', DEF_MARGIN))
    if not size[1] or size[1] > 0.5:
        size = ('margin', DEF_MARGIN)
    limit = bool(re.search(r'(?i)лимит|триггер|отложк|условн', t)) and core.get('entry') is not None
    # доли фиксации: что написано сразу после каждого тейка («фиксация 40 процентов», «полное закрытие», «стоп в б/у»)
    hints = [None] * len(core['tps'])
    for mm in re.finditer(r'(?<![\w.])(\d+(?:[.,]\d+)?)', t):
        raw = float(mm.group(1).replace(',', '.'))
        for k, tp in enumerate(core['tps']):
            if hints[k] is not None:
                continue
            if any(abs(raw * f / tp - 1) < 1e-6 for f in (1, 1000, 0.001, 1e6, 1e-6)):
                after = t[mm.end():mm.end() + 70].split('\n')[0].lower()
                after = re.split(r't\s*/\s*p|тейк|target|🎯|stop|стоп(?! в)', after)[0] if not after.strip().startswith('ставим') else after
                fx = re.search(r'(?:фикс\w*|закрыва\w*|закрываем)[^\d]{0,15}(\d{1,3})(?:\s*-\s*\d{1,3})?\s*(?:%|процент)', after)
                if fx:
                    hints[k] = int(fx.group(1)) / 100
                elif re.search(r'полн\w* закрыт|закрытие|полностью', after):
                    hints[k] = 'full'
                elif re.search(r'б/у|безубыт', after):
                    hints[k] = 'be'
                break
    want = hints
    core.update(lev=max(1, min(lev, 125)), size=size, limit=limit, fix_hints=want)
    return core


def plan_parts(tps, hints):
    """Доли закрытия по тейкам (сумма 1). Подсказки из поста, иначе 50% на первом и остаток на последнем."""
    n = len(tps)
    hints = (list(hints) + [None] * n)[:n]
    if not any(isinstance(h, float) for h in hints):
        if n == 1:
            return [1.0]
        parts = [0.0] * n; parts[0] = 0.5; parts[-1] = 0.5
        return parts
    parts = [0.0] * n
    for k, h in enumerate(hints):
        left = 1 - sum(parts)
        if left <= 1e-9:
            break
        if isinstance(h, float):
            parts[k] = min(h, left)
        elif h == 'full':
            parts[k] = left
            break
    if 1 - sum(parts) > 1e-9:
        parts[-1] += 1 - sum(parts)
    return [round(x, 4) for x in parts]


def open_vip(sig, price, now_ms, equity):
    s = sig['side']
    stop = sig['stop']
    if s * (price - stop) <= 0:
        return None, 'цена уже за стопом'
    tps = sorted([x for x in sig['tps'] if s * (x - price) > 0], key=lambda x: s * x)
    if not tps:
        return None, 'цена уже прошла тейки'
    dist = abs(price - stop) / price
    kind, v = sig['size']
    if kind == 'risk':
        notional = equity * v / dist
        notional = min(notional, equity * sig['lev'])
    else:
        notional = equity * v * sig['lev']
    margin = notional / sig['lev']
    parts = plan_parts(tps, sig.get('fix_hints', []))[:len(tps)]
    be = sig.get('be') if sig.get('be') and s * (sig['be'] - price) > 0 else tps[0]
    return {'sym': None, 'side': 'long' if s == 1 else 'short', 's': s, 'entry': price, 'entry_t': now_ms, 'stop': stop,
            'be_at': be, 'tps': tps, 'parts': parts, 'hit': 0, 'tp': tps[0], 'rem': 1.0, 'notional': notional, 'margin': margin,
            'lev': sig['lev'], 'dist': dist, 'r_usd': notional * dist, 'realized': -notional * TAKER, 'last_t': now_ms}, None


def step_vip(pos, o, h, l, c, first=False):
    s, e = pos['s'], pos['entry']
    if (s == 1 and l <= pos['stop']) or (s == -1 and h >= pos['stop']):
        st = pos['stop']
        px = st if first else (min(o, st) if s == 1 else max(o, st))
        pos['realized'] += pos['rem'] * pos['notional'] * (s * (px - e) / e - TAKER); pos['rem'] = 0
        return True, ('be' if abs(st - e) < 1e-12 else 'stop'), px
    if (s == 1 and h >= pos['be_at']) or (s == -1 and l <= pos['be_at']):
        pos['stop'] = e
    while pos['hit'] < len(pos['tps']) and ((s == 1 and h >= pos['tps'][pos['hit']]) or (s == -1 and l <= pos['tps'][pos['hit']])):
        k = pos['hit']; px = pos['tps'][k]; part = min(pos['parts'][k], pos['rem'])
        if part > 0:
            pos['realized'] += part * pos['notional'] * (s * (px - e) / e - MAKER); pos['rem'] = round(pos['rem'] - part, 9)
        pos['hit'] += 1; pos['stop'] = e
        if pos['hit'] < len(pos['tps']):
            pos['tp'] = pos['tps'][pos['hit']]
        if pos['rem'] <= 1e-9:
            return True, 'tp' + str(pos['hit']), px
    return False, None, None


class VipBot:
    def __init__(self, resolve, klines_1m, get_price):
        import threading
        self.lock = threading.RLock()
        self.resolve, self.klines, self.price = resolve, klines_1m, get_price
        self.st = SLOT.load_state()
        self.st.setdefault('claimed', []); self.st.setdefault('skips', []); self.st.setdefault('pendings', [])

    def save(self):
        SLOT.save_state(self.st); SLOT.write_status(self.st)

    def on_post(self, msg_id, text, t_ms, live=True):
        with self.lock:
            sig = parse_vip(text)
            if not sig:
                if text and re.search(r'(?i)(long|short|лонг|шорт)', text) and re.search(r'(?i)стоп|stop|вход', text):
                    self.st['skips'].append({'id': msg_id, 't': t_ms, 'why': 'нет стопа или тейков в тексте (вероятно, «на скрине»)'})
                    SLOT.log(f'SKIP пост #{msg_id}: нет стопа или тейков в тексте')
                self._note(msg_id, text, t_ms); self.save(); return
            if not live:
                self.st['skips'].append({'id': msg_id, 'sym': sig['sym'], 't': t_ms, 'why': 'пост вышел, пока бот был выключен'})
                self.save(); return
            res, why = self.resolve(sig['sym'], sig['entry'] or sig['stop'])
            if not res:
                self.st['skips'].append({'id': msg_id, 'sym': sig['sym'], 't': t_ms, 'why': why}); SLOT.log(f"SKIP {sig['sym']}: {why}")
                self.save(); return
            sym, f, px = res
            ff = next((k for k in (f, f * 1000, f / 1000) if abs(sig['stop'] * k / px - 1) < 0.25), None)
            if ff is None:
                self.st['skips'].append({'id': msg_id, 'sym': sym, 't': t_ms, 'why': 'уровни поста не сходятся с ценой'})
                SLOT.log(f'SKIP {sym}: уровни не сходятся с ценой'); self.save(); return
            sig = dict(sig, stop=sig['stop'] * ff, tps=[x * ff for x in sig['tps']], be=sig['be'] * ff if sig['be'] else None,
                       entry=sig['entry'] * ff if sig['entry'] else None)
            if any(p['sym'] == sym for p in self.st['positions']):
                SLOT.log(f'SKIP {sym}: позиция уже открыта'); self.save(); return
            if sig['limit']:
                self.st['pendings'].append({'sym': sym, 'sig': sig, 'msg_id': msg_id, 'expire': t_ms + LIMIT_TTL, 'entry': sig['entry'],
                                            'stop': sig['stop'], 'side': 'long' if sig['side'] == 1 else 'short', 'kind': 'limit'})
                SLOT.log(f"ЛИМИТ {sym} {'лонг' if sig['side'] == 1 else 'шорт'} от {sig['entry']:.6g}, ждём до 48 ч")
            else:
                self._open(sym, sig, px, t_ms, msg_id)
            self.save()

    def _open(self, sym, sig, px, t_ms, msg_id):
        pos, why = open_vip(sig, px, t_ms, self.st['eq'])
        if not pos:
            self.st['skips'].append({'id': msg_id, 'sym': sym, 't': t_ms, 'why': why}); SLOT.log(f'SKIP {sym}: {why}'); return
        pos['sym'] = sym
        pos['ctx'] = {'msg_id': msg_id, 'base': sig['sym'], 'notes': [],
                      'reason': f"пост #{msg_id}: {sig['sym']} {pos['side']} x{sig['lev']}, стоп {sig['stop']:.6g}, тейки "
                                f"{' '.join(f'{x:.6g}' for x in pos['tps'])}, доли {pos['parts']}, б/у на {pos['be_at']:.6g}, "
                                f"размер {sig['size'][0]} {sig['size'][1] * 100:.1f}%"}
        self.st['positions'].append(pos)
        SLOT.log(f"FILL {sym} {pos['side']} @ {px:.6g} | x{pos['lev']} маржа {pos['margin']:.2f}$ | стоп {pos['stop']:.6g} | "
                 f"тейки {' '.join(f'{x:.6g}' for x in pos['tps'])}")

    def _note(self, msg_id, text, t_ms):
        tags = {x.upper() for x in re.findall(r'\b([A-Za-z]{2,12})\s*/?\s*USDT', text or '')}
        for p in self.st['positions']:
            if p['ctx'].get('base') in tags or (not tags and len(self.st['positions']) == 1):
                p['ctx']['notes'].append({'t': t_ms, 'text': (text or '')[:400]})

    def on_note(self, msg_id, text, t_ms):
        with self.lock:
            self._note(msg_id, text, t_ms); self.save()

    def manage(self):
        with self.lock:
            now = int(time.time() * 1000)
            SLOT.apply_controls(self.st, lambda st_, pos, note=None: self._close(pos, self.price(pos['sym']) or pos['entry'], now, 'manual'))
            for pd in list(self.st['pendings']):
                px = self.price(pd['sym'])
                s = 1 if pd['side'] == 'long' else -1
                if now > pd['expire']:
                    self.st['pendings'].remove(pd); SLOT.log(f"ЛИМИТ {pd['sym']} не исполнился за 48 ч — отменён"); continue
                if px and s * (px - pd['entry']) <= 0:
                    self.st['pendings'].remove(pd); self._open(pd['sym'], pd['sig'], px, now, pd['msg_id'])
            for pos in list(self.st['positions']):
                bars = self.klines(pos['sym'], pos['last_t'] // 60000 * 60000)
                done = False
                for b in bars:
                    if b[0] < pos['last_t'] // 60000 * 60000:
                        continue
                    first = b[0] <= pos['entry_t'] < b[0] + 60000
                    if first:
                        done, out, px = step_vip(pos, pos['entry'], max(pos['entry'], b[4]), min(pos['entry'], b[4]), b[4], True)
                    else:
                        done, out, px = step_vip(pos, *b[1:5])
                    pos['last_t'] = b[0] + 60000
                    if done:
                        self._book(pos, px, b[0], out); break
                if not done and now - pos['entry_t'] >= MAX_HOURS * H:
                    px = self.price(pos['sym'])
                    if px:
                        self._close(pos, px, now, 'time14d')
            self.save()

    def _close(self, pos, px, t, out):
        s, e = pos['s'], pos['entry']
        pos['realized'] += pos['rem'] * pos['notional'] * (s * (px - e) / e - TAKER); pos['rem'] = 0
        self._book(pos, px, t, out)

    def _book(self, pos, px, t, out):
        SLOT.book_trade(self.st, pos, px, t, out, pos['realized'],
                        {'hit': pos['hit'], 'lev': pos['lev'], 'margin': round(pos['margin'], 2),
                         'roe': round(pos['realized'] / pos['margin'] * 100, 1) if pos['margin'] else 0,
                         'chan_notes': pos['ctx'].get('notes', [])[-6:]})
