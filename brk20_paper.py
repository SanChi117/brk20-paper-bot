#!/usr/bin/env python3
# BRK-20 PAPER BOT v1.1 — форвард-гейт BRK-20 на пуле владельца (17 альтов)
# Логика 1:1 из bt_candidates19.py (бэктест: 191 сделка, PF 1.723, стресс 1.682)
# Режим: БУМАЖНЫЙ. Никаких ордеров на бирже, ключи API не нужны и не используются.
# v1.1: команды из веб-терминала (control.json), пауза, ручное закрытие, риск из state.
import json, time, os, sys, csv
from datetime import datetime, timezone
import requests

SYMS = ['SOLUSDT','BNBUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','SUIUSDT','APTUSDT','NEARUSDT',
        'LINKUSDT','AAVEUSDT','ARBUSDT','OPUSDT','DOGEUSDT','TAOUSDT','ONDOUSDT','INJUSDT','SEIUSDT']
H4 = 4*3600*1000
FEE_IN, FEE_STOP, SLIP = 0.0002, 0.0004, 0.0002
RISK, MAXPOS = 0.01, 2
TTL_BRK = 24*3600*1000
EQ0 = 1000.0
BASE = 'https://fapi.binance.com'
BYBIT = 'https://api.bybit.com'
SOURCE = {'name': 'binance'}  # авто-fallback на bybit, если binance недоступен с IP сервера
DIR = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(DIR, 'state.json')
TRADES_F = os.path.join(DIR, 'trades.csv')
STATUS_F = os.path.join(DIR, 'status.txt')
JOURNAL_F = os.path.join(DIR, 'journal.log')
CONTROL_F = os.path.join(DIR, 'control.json')  # команды из веб-терминала

def log(msg):
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | {msg}"
    print(line, flush=True)
    with open(JOURNAL_F, 'a') as f:
        f.write(line + '\n')

def klines(sym, interval, limit):
    now = int(time.time()*1000)
    ms = {'1h': 3600000, '4h': 4*3600000}[interval]
    for attempt in range(3):
        try:
            if SOURCE['name'] == 'binance':
                r = requests.get(f'{BASE}/fapi/v1/klines',
                                 params={'symbol': sym, 'interval': interval, 'limit': limit},
                                 timeout=15)
                if r.status_code == 200:
                    out = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r.json()]
                    return [x for x in out if x[0] + ms <= now]  # только закрытые свечи
                raise RuntimeError(f'HTTP {r.status_code} {r.text[:120]}')
            else:  # bybit linear perp, тот же набор символов
                r = requests.get(f'{BYBIT}/v5/market/kline',
                                 params={'category': 'linear', 'symbol': sym,
                                         'interval': '60' if interval == '1h' else '240',
                                         'limit': min(limit, 1000)},
                                 timeout=15)
                j = r.json()
                if j.get('retCode') == 0:
                    out = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                           for x in reversed(j['result']['list'])]
                    return [x for x in out if x[0] + ms <= now]
                raise RuntimeError(f"bybit retCode {j.get('retCode')} {j.get('retMsg', '')[:120]}")
        except Exception as e:
            log(f"WARN klines {sym} [{SOURCE['name']}]: {e}")
            if attempt == 1 and SOURCE['name'] == 'binance':
                SOURCE['name'] = 'bybit'
                log('ВНИМАНИЕ: Binance API недоступен с этого IP — переключился на Bybit (бумажный режим, цены ~идентичны)')
        time.sleep(3)
    return None

def build4h(rows1h):
    agg = {}
    for t, o, h, l, c in rows1h:
        k = t // H4
        if k not in agg:
            agg[k] = [o, h, l, c]
        else:
            a = agg[k]; a[1] = max(a[1], h); a[2] = min(a[2], l); a[3] = c
    return [[k*H4, *v] for k, v in sorted(agg.items())]

class SymData:
    """Индикаторы по символу, пересчитываются на каждом новом закрытом 4h-баре."""
    def __init__(self, b4):
        self.b4 = b4
        n = len(b4)
        self.closes = [x[4] for x in b4]
        self.atr = [None]*n
        for i in range(1, n):
            tr = max(b4[i][2]-b4[i][3], abs(b4[i][2]-b4[i-1][4]), abs(b4[i][3]-b4[i-1][4]))
            self.atr[i] = tr if self.atr[i-1] is None else self.atr[i-1]*13/14 + tr/14
        # подтверждённые пивот-лоу (лаг 3): (i+3, i, low)
        self.pl = []
        for i in range(3, n-3):
            lo = b4[i][3]
            if all(lo <= b4[j][3] for j in range(i-3, i+4)):
                self.pl.append((i+3, i, lo))
        self.hi20 = [None]*n
        for i in range(120, n):
            self.hi20[i] = max(x[2] for x in b4[i-120:i])
        self.idx4 = {x[0]: i for i, x in enumerate(b4)}

def default_state():
    return {'eq': EQ0, 'positions': [], 'pendings': [], 'trades': [],
            'last_1h': {}, 'last_4h_signal': 0, 'started': int(time.time()*1000),
            'paused': False, 'risk': RISK}

def apply_controls(st):
    """Команды из веб-терминала: пауза, закрытие, отмена, риск, equity."""
    if not os.path.exists(CONTROL_F):
        return
    try:
        with open(CONTROL_F) as f:
            cmds = json.load(f)
        os.remove(CONTROL_F)
    except Exception:
        return
    for c in cmds:
        cmd = c.get('cmd'); sym = c.get('sym')
        if cmd == 'pause':
            st['paused'] = True; log('WEB: пауза включена (сигналы и входы заморожены, стопы работают)')
        elif cmd == 'resume':
            st['paused'] = False; log('WEB: пауза снята')
        elif cmd == 'cancel' and sym:
            n0 = len(st['pendings'])
            st['pendings'] = [p for p in st['pendings'] if p['sym'] != sym]
            if len(st['pendings']) < n0:
                log(f'WEB: заявка {sym} отменена вручную')
        elif cmd == 'close' and sym:
            for pos in list(st['positions']):
                if pos['sym'] != sym:
                    continue
                rows = klines(sym, '1h', 2)
                px = rows[-1][4] if rows else pos['entry']
                r_multiple = (px-pos['entry'])/pos['entry']/pos['dist']
                pnl = r_multiple*pos['r_usd']*pos['rem'] - FEE_STOP*pos['notional']*pos['rem']
                st['eq'] += pnl
                tr = {'sym': sym, 'entry': pos['entry'], 'exit': px,
                      'r': pos['r_tot'] + pnl/pos['r_usd'], 'r_usd': pnl, 'out': 'manual'}
                st['trades'].append(tr); append_trade(tr)
                log(f"WEB: {sym} закрыта вручную @ {px:.6g} | R {tr['r']:+.2f} | eq {st['eq']:.2f}")
                st['positions'].remove(pos)
        elif cmd == 'set_risk':
            v = max(0.0025, min(0.02, float(c.get('value', RISK))))
            st['risk'] = v
            log(f'WEB: риск на сделку изменён на {v*100:.2f}% (гейт рассчитан на 1% — изменение фиксируется)')
        elif cmd == 'set_equity':
            if st['positions']:
                log('WEB: отказ в set_equity — есть открытые позиции')
            else:
                st['eq'] = float(c.get('value', st['eq']))
                log(f"WEB: equity установлен вручную: {st['eq']:.2f}")

def load_state():
    if os.path.exists(STATE_F):
        try:
            with open(STATE_F) as f:
                return json.load(f)
        except Exception:
            pass
    return default_state()

def save_state(st):
    tmp = STATE_F + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(st, f)
    os.replace(tmp, STATE_F)

def append_trade(tr):
    new = not os.path.exists(TRADES_F)
    with open(TRADES_F, 'a', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['close_time_utc', 'sym', 'entry', 'exit', 'r', 'r_usd', 'out'])
        w.writerow([datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'),
                    tr['sym'], f"{tr['entry']:.8g}", f"{tr['exit']:.8g}",
                    f"{tr['r']:.3f}", f"{tr['r_usd']:.2f}", tr['out']])

def stats(st):
    trs = st['trades']
    gw = sum(t['r_usd'] for t in trs if t['r_usd'] > 0)
    gl = -sum(t['r_usd'] for t in trs if t['r_usd'] < 0)
    pf = gw/gl if gl > 0 else float('inf')
    wr = sum(1 for t in trs if t['r_usd'] > 0)/max(1, len(trs))
    return len(trs), pf, wr

def write_status(st):
    n, pf, wr = stats(st)
    gate = 'ИДЁТ' if n < 30 else ('ПРОЙДЕН' if pf > 1.2 else 'ПРОВАЛЕН')
    pz = ' | ПАУЗА' if st.get('paused') else ''
    txt = (f"BRK-20 PAPER | equity {st['eq']:.2f}$ | сделок {n}/30 | PF {pf:.2f} | WR {wr*100:.0f}% | "
           f"риск {st.get('risk', RISK)*100:.2f}% | позиций {len(st['positions'])} | заявок {len(st['pendings'])} | гейт: {gate}{pz} | "
           f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n")
    with open(STATUS_F, 'w') as f:
        f.write(txt)

def process_bar_1h(st, sym, t, o, h, l, c, symdata):
    """Управление позициями и исполнение заявок на закрытом 1h-баре. 1:1 из бэктеста."""
    # --- manage ---
    for pos in list(st['positions']):
        if pos['sym'] != sym:
            continue
        stop = pos['stop']
        if l <= stop:
            px = stop*(1-SLIP)
            r_multiple = (px-pos['entry'])/pos['entry']/pos['dist']
            pnl = r_multiple*pos['r_usd']*pos['rem'] - FEE_STOP*pos['notional']*pos['rem']
            st['eq'] += pnl
            tr = {'sym': sym, 'entry': pos['entry'], 'exit': px,
                  'r': pos['r_tot'] + pnl/pos['r_usd'], 'r_usd': pnl,
                  'out': 'stop' if pos['stage'] == 0 else 'trail'}
            st['trades'].append(tr); append_trade(tr)
            log(f"EXIT {sym} stop-hit @ {px:.6g} | R {tr['r']:+.2f} | eq {st['eq']:.2f}")
            st['positions'].remove(pos)
            continue
        if pos['stage'] == 0 and h >= pos['be_trigger']:
            pos['stop'] = pos['entry']*(1+FEE_IN+FEE_STOP)
            pos['stage'] = 1
            log(f"BE {sym}: +2R достигнут, стоп в безубыток {pos['stop']:.6g}")
        if pos['stage'] == 1 and symdata is not None and t in symdata.idx4:
            i4 = symdata.idx4[t]
            a = symdata.atr[i4]
            cands = [p for p in symdata.pl
                     if symdata.b4[p[0]][0] <= t and p[2] > pos['entry']
                     and symdata.b4[p[1]][0] > pos['entry_t4']]
            if cands and a:
                newstop = cands[-1][2] - 0.5*a
                if newstop > pos['stop']:
                    pos['stop'] = newstop
                    log(f"TRAIL {sym}: стоп поднят до {newstop:.6g}")
    # --- fill ---
    if st.get('paused'):
        return
    risk = st.get('risk', RISK)
    for p in list(st['pendings']):
        if p['sym'] != sym:
            continue
        if t > p['exp']:
            st['pendings'].remove(p)
            log(f"TTL {sym}: заявка {p['entry']:.6g} истекла без исполнения")
            continue
        if any(q['sym'] == sym for q in st['positions']):
            st['pendings'].remove(p)
            continue
        if len(st['positions']) >= MAXPOS:
            continue
        e = p['entry']
        if h < e*(1+SLIP):
            continue
        notional = min((st['eq']*risk)/p['sf'], st['eq'])
        pos = {'sym': sym, 'entry': e, 'stop': p['stop'], 'dist': p['sf'],
               'notional': notional, 'r_usd': st['eq']*risk, 'rem': 1.0,
               'r_tot': -FEE_IN*notional/(st['eq']*risk), 'stage': 0,
               'entry_t4': t//H4*H4,
               'be_trigger': e*(1+2.0*p['sf'])}
        st['positions'].append(pos)
        st['eq'] -= FEE_IN*notional
        st['pendings'].remove(p)
        log(f"FILL {sym} buy-stop @ {e:.6g} | стоп {p['stop']:.6g} | риск {pos['r_usd']:.2f}$ | eq {st['eq']:.2f}")

def scan_signals(st):
    """Сигналы на закрытии 4h-бара по всему пулу. Вызывается раз в 4 часа."""
    t4 = (int(time.time()*1000)//H4 - 1)*H4  # время открытия последнего ЗАКРЫТОГО 4h-бара
    if st['last_4h_signal'] >= t4:
        return
    st['last_4h_signal'] = t4
    for sym in SYMS:
        if any(p['sym'] == sym for p in st['pendings']) or any(p['sym'] == sym for p in st['positions']):
            continue
        rows = klines(sym, '1h', 720)
        if not rows or len(rows) < 600:
            log(f'WARN {sym}: мало данных для сигнала ({0 if not rows else len(rows)} 1h-баров)')
            continue
        sd = build4h(rows)
        data = SymData(sd)
        i = data.idx4.get(t4)
        if i is None or i < 130:
            continue
        a = data.atr[i]
        if not a:
            continue
        hi = data.hi20[i]
        if hi and data.closes[i] > hi:
            entry = hi
            stop = entry - 1.75*a
            sf = (entry-stop)/entry
            if sf > 0.20 or sf <= 0:
                continue
            st['pendings'].append({'sym': sym, 'entry': entry, 'stop': stop, 'sf': sf,
                                   'exp': t4 + TTL_BRK})
            log(f"SIGNAL {sym}: 4h close {data.closes[i]:.6g} > hi20 {hi:.6g} | buy-stop {entry:.6g}, SL {stop:.6g}, TTL 24h")

def main():
    log('=== BRK-20 PAPER BOT стартовал | пул 17 альтов | риск 1% | бумажный режим ===')
    st = load_state()
    while True:
        try:
            apply_controls(st)
            if not st.get('paused'):
                scan_signals(st)
            # закрытые 1h-бары по символам с позициями/заявками
            active = {p['sym'] for p in st['positions']} | {p['sym'] for p in st['pendings']}
            for sym in active:
                rows = klines(sym, '1h', 3)
                if not rows:
                    continue
                sd = build4h(klines(sym, '1h', 720)) if any(p['sym'] == sym and p['stage'] == 1 for p in st['positions']) else None
                symdata = SymData(sd) if sd else None
                for t, o, h, l, c in rows:
                    if st['last_1h'].get(sym, 0) >= t:
                        continue
                    process_bar_1h(st, sym, t, o, h, l, c, symdata)
                    st['last_1h'][sym] = t
            save_state(st)
            write_status(st)
        except Exception as e:
            log(f'ERROR main loop: {e}')
        time.sleep(60)

if __name__ == '__main__':
    main()
