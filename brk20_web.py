#!/usr/bin/env python3
# BRK-20 WEB TERMINAL v3 — браузерный терминал для бумажного бота (лейаут биржи)
# Графики (15м/30м/1ч/4ч/1д), рисование и заметки на графике, журнал сделок,
# управление (пауза/закрыть/отменить/риск/equity), ручные заявки (заглушка),
# слоты стратегий (state изолирован per-стратегия), котировки пула 24ч,
# постоянные стакан и лента сделок по выбранной монете.
# Авторизация: токен в URL (?key=...). Только бумажный режим — ключей биржи нет.
import json, os, time, csv, secrets, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import requests

DIR = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(DIR, 'state.json')
TRADES_F = os.path.join(DIR, 'trades.csv')
JOURNAL_F = os.path.join(DIR, 'journal.log')
CONTROL_F = os.path.join(DIR, 'control.json')
CONF_F = os.path.join(DIR, 'web_config.json')
INDEX_F = os.path.join(DIR, 'web', 'index.html')
DRAW_F = os.path.join(DIR, 'drawings.json')   # рисунки на графиках
NOTES_F = os.path.join(DIR, 'notes.json')     # заметки к сделкам

# живые стратегии: свои файлы состояния/журнала/команд, своя сторона
LIVE = {
    'brk20': {'state': STATE_F, 'journal': JOURNAL_F, 'control': CONTROL_F,
              'side': 'long', 'eq0': 1000.0},
    'slot2': {'state': os.path.join(DIR, 'state_slot2.json'),
              'journal': os.path.join(DIR, 'journal_slot2.log'),
              'control': os.path.join(DIR, 'control_slot2.json'),
              'side': 'short', 'eq0': 1000.0},
}

BASE = 'https://fapi.binance.com'
BYBIT = 'https://api.bybit.com'
SYMS = ['SOLUSDT','BNBUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','SUIUSDT','APTUSDT','NEARUSDT',
        'LINKUSDT','AAVEUSDT','ARBUSDT','OPUSDT','DOGEUSDT','TAOUSDT','ONDOUSDT','INJUSDT','SEIUSDT']
TFS = {'15m': ('15m', '15', 900000), '30m': ('30m', '30', 1800000),
       '1h': ('1h', '60', 3600000), '4h': ('4h', '240', 4*3600000),
       '1d': ('1d', 'D', 86400000)}

STRATEGIES = [
    {'id': 'brk20', 'name': 'BRK-20', 'kind': 'Пробой 20-дневного хая (4h)',
     'status': 'forward-gate', 'risk': '1%', 'pool': len(SYMS),
     'desc': 'Лонг-пробой Donchian-120 на 4h. Стоп −1.75×ATR, BE на +2R, трейл по пивотам. Форвард-гейт: 30 сделок, PF>1.2.'},
    {'id': 'slot2', 'name': 'BRK-20 SHORT', 'kind': 'Пробой 20-дневного лоу (4h), шорт',
     'status': 'forward-gate', 'risk': '1%', 'pool': len(SYMS),
     'desc': 'Зеркало BRK-20: пробой Donchian-120 вниз, sell-stop, стоп +1.75×ATR, BE на +2R, трейл по пивот-хаям. Бэктест: 117 сделок, PF 1.834, стресс 1.785. Свой счёт, свой гейт: 30 сделок, PF>1.2.'},
    {'id': 'slot3', 'name': 'Слот 3', 'kind': '—', 'status': 'empty', 'risk': '—', 'pool': 0,
     'desc': 'Пустой слот под третью стратегию.'},
    {'id': 'slot4', 'name': 'Слот 4', 'kind': '—', 'status': 'empty', 'risk': '—', 'pool': 0,
     'desc': 'Пустой слот под четвёртую стратегию.'},
]

EMPTY_STATE = {'eq': 0.0, 'positions': [], 'pendings': [], 'trades': [],
               'n': 0, 'pf': None, 'wr': 0.0, 'gate': '—', 'paused': False,
               'risk': 0.01, 'eq_curve': [], 'journal': [], 'started': None}

def load_conf():
    if not os.path.exists(CONF_F):
        with open(CONF_F, 'w') as f:
            json.dump({'token': secrets.token_urlsafe(16), 'port': 8080}, f)
    with open(CONF_F) as f:
        return json.load(f)

def load_state(path=None):
    try:
        with open(path or STATE_F) as f:
            return json.load(f)
    except Exception:
        return None

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)

_KL_CACHE = {}   # (sym, tf, limit) -> (ts, rows): кэш, чтобы переключения были мгновенными

def klines(sym, tf, limit=300):
    key = (sym, tf, limit)
    hit = _KL_CACHE.get(key)
    if hit and time.time() - hit[0] < 20:
        return hit[1]
    rows = _fetch_klines(sym, tf, limit)
    if rows:
        _KL_CACHE[key] = (time.time(), rows)
        return rows
    return hit[1] if hit else []   # биржа лежит — отдаём последнее живое, а не пустоту

def _fetch_klines(sym, tf, limit=300):
    now = int(time.time()*1000)
    biv, bbiv, ms = TFS[tf]
    try:
        r = requests.get(f'{BASE}/fapi/v1/klines',
                         params={'symbol': sym, 'interval': biv, 'limit': limit}, timeout=6)
        if r.status_code == 200:
            out = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r.json()]
            return [x for x in out if x[0] + ms <= now]
        raise RuntimeError(f'HTTP {r.status_code}')
    except Exception:
        pass
    try:
        r = requests.get(f'{BYBIT}/v5/market/kline',
                         params={'category': 'linear', 'symbol': sym, 'interval': bbiv,
                                 'limit': min(limit, 1000)}, timeout=6)
        j = r.json()
        if j.get('retCode') == 0:
            out = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                   for x in reversed(j['result']['list'])]
            return [x for x in out if x[0] + ms <= now]
    except Exception:
        pass
    return []

def donchian(sym, side='long', both=False):
    """Индикатор стратегии: граница Donchian-120 на 4h (20 дней) + ATR(14) Wilder.
    Лонг (brk20): HI20 = макс хаёв за 120 баров — вход пробоем вверх.
    Шорт (slot2): LO20 = мин лоу за 120 баров — вход пробоем вниз.
    both=True: обе границы канала сразу (hi20 + lo20) для визуального сравнения."""
    rows = klines(sym, '4h', 300)
    if len(rows) < 130:
        return {'pts': [], 'atr': None, 'line': 'HI20' if side == 'long' else 'LO20'}
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    hi20 = [[rows[i][0] // 1000, max(highs[i-120:i])] for i in range(120, len(rows))]
    lo20 = [[rows[i][0] // 1000, min(lows[i-120:i])] for i in range(120, len(rows))]
    atr = None
    trs = [max(rows[i][2]-rows[i][3], abs(rows[i][2]-rows[i-1][4]), abs(rows[i][3]-rows[i-1][4]))
           for i in range(1, len(rows))]
    if len(trs) >= 14:
        atr = sum(trs[:14]) / 14
        for tr in trs[14:]:
            atr = (atr * 13 + tr) / 14
    atr = round(atr, 8) if atr else None
    if both:
        return {'hi20': hi20[-150:], 'lo20': lo20[-150:], 'atr': atr}
    if side == 'short':
        return {'pts': lo20[-150:], 'atr': atr, 'line': 'LO20'}
    return {'pts': hi20[-150:], 'atr': atr, 'line': 'HI20'}

def depth(sym, limit=20):
    """Стакан: Binance фьючи, фолбэк Bybit. bids/asks = [[цена, кол-во], ...]"""
    try:
        r = requests.get(f'{BASE}/fapi/v1/depth',
                         params={'symbol': sym, 'limit': limit}, timeout=5)
        if r.status_code == 200:
            j = r.json()
            return {'bids': [[float(p), float(q)] for p, q in j['bids']],
                    'asks': [[float(p), float(q)] for p, q in j['asks']],
                    'src': 'binance'}
    except Exception:
        pass
    try:
        r = requests.get(f'{BYBIT}/v5/market/orderbook',
                         params={'category': 'linear', 'symbol': sym, 'limit': 25}, timeout=5)
        j = r.json()
        if j.get('retCode') == 0:
            res = j['result']
            return {'bids': [[float(p), float(q)] for p, q in res['b']],
                    'asks': [[float(p), float(q)] for p, q in res['a']],
                    'src': 'bybit'}
    except Exception:
        pass
    return {'bids': [], 'asks': [], 'src': None}

_Q_CACHE = {'ts': 0.0, 'rows': []}

def quotes():
    """Котировки пула: последняя цена, % за 24ч, OHLC 24ч и оборот — для сайдбара
    и стат-полосы над графиком. Кэш 5с: фронт опрашивает часто, биржу не дёргаем."""
    if time.time() - _Q_CACHE['ts'] < 5 and _Q_CACHE['rows']:
        return _Q_CACHE['rows']
    rows = []
    try:
        r = requests.get(f'{BASE}/fapi/v1/ticker/24hr',
                         params={'symbols': json.dumps(SYMS)}, timeout=6)
        if r.status_code == 200:
            for x in r.json():
                rows.append({'sym': x['symbol'], 'px': float(x['lastPrice']),
                             'chg': float(x['priceChangePercent']),
                             'o': float(x['openPrice']), 'h': float(x['highPrice']),
                             'l': float(x['lowPrice']), 'vol': float(x['quoteVolume']),
                             'src': 'binance'})
    except Exception:
        pass
    if not rows:
        try:
            r = requests.get(f'{BYBIT}/v5/market/tickers',
                             params={'category': 'linear'}, timeout=8)
            j = r.json()
            if j.get('retCode') == 0:
                for x in j['result']['list']:
                    if x['symbol'] in SYMS:
                        rows.append({'sym': x['symbol'], 'px': float(x['lastPrice']),
                                     'chg': float(x.get('price24hPcnt') or 0) * 100,
                                     'o': float(x.get('prevPrice24h') or 0),
                                     'h': float(x['highPrice24h']),
                                     'l': float(x['lowPrice24h']),
                                     'vol': float(x['turnover24h']), 'src': 'bybit'})
        except Exception:
            pass
    if rows:
        rows.sort(key=lambda x: SYMS.index(x['sym']) if x['sym'] in SYMS else 99)
        _Q_CACHE['ts'] = time.time()
        _Q_CACHE['rows'] = rows
    return _Q_CACHE['rows']

def trades_tape(sym, limit=30):
    """Лента последних сделок по монете: цена, кол-во, время, сторона
    (side=buy — покупка агрессором, рисуем зелёным; sell — красным)."""
    try:
        r = requests.get(f'{BASE}/fapi/v1/trades',
                         params={'symbol': sym, 'limit': limit}, timeout=5)
        if r.status_code == 200:
            return {'trades': [{'px': float(x['price']), 'q': float(x['qty']),
                                't': int(x['time']),
                                'side': 'sell' if x['isBuyerMaker'] else 'buy'}
                               for x in reversed(r.json())], 'symbol': sym, 'src': 'binance'}
    except Exception:
        pass
    try:
        r = requests.get(f'{BYBIT}/v5/market/recent-trade',
                         params={'category': 'linear', 'symbol': sym, 'limit': limit}, timeout=5)
        j = r.json()
        if j.get('retCode') == 0:
            return {'trades': [{'px': float(x['price']), 'q': float(x['size']),
                                't': int(x['time']),
                                'side': 'buy' if x['side'] == 'Buy' else 'sell'}
                               for x in j['result']['list']], 'symbol': sym, 'src': 'bybit'}
    except Exception:
        pass
    return {'trades': [], 'symbol': sym, 'src': None}

def api_state(strat='brk20'):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    base = {'strategies': STRATEGIES, 'syms': SYMS, 'strat': strat, 'ts': ts}
    if strat not in LIVE:
        # пустой слот: свой «нулевой» стейт, ничего общего с живыми стратегиями
        out = dict(EMPTY_STATE); out.update(base); out['live'] = False
        return out
    cfg = LIVE[strat]
    st = load_state(cfg['state'])
    if st is None:
        # бот ещё не создал state (не запущен) — показываем свежий нулевой гейт
        out = dict(EMPTY_STATE); out.update(base)
        out.update({'eq': cfg['eq0'], 'gate': 'ИДЁТ', 'live': True, 'side': cfg['side']})
        return out
    trs = st.get('trades', [])
    gw = sum(t['r_usd'] for t in trs if t['r_usd'] > 0)
    gl = -sum(t['r_usd'] for t in trs if t['r_usd'] < 0)
    pf = gw/gl if gl > 0 else None
    wr = sum(1 for t in trs if t['r_usd'] > 0)/max(1, len(trs))
    n = len(trs)
    gate = 'ИДЁТ' if n < 30 else ('ПРОЙДЕН' if (pf or 0) > 1.2 else 'ПРОВАЛЕН')
    eq_curve = []
    eq = cfg['eq0']
    for t in trs:
        eq += t['r_usd']
        eq_curve.append(round(eq, 2))
    journal_tail = []
    if os.path.exists(cfg['journal']):
        with open(cfg['journal']) as f:
            journal_tail = f.readlines()[-60:]
    notes = load_json(NOTES_F, {})
    for t in trs:
        if t.get('id') is not None and str(strat) + ':' + str(t['id']) in notes:
            t['note'] = notes[str(strat) + ':' + str(t['id'])]
    out = {'eq': round(st['eq'], 2), 'positions': st.get('positions', []),
           'pendings': st.get('pendings', []), 'trades': trs[-300:],
           'n': n, 'pf': round(pf, 3) if pf else None, 'wr': round(wr*100, 1),
           'gate': gate, 'paused': st.get('paused', False),
           'risk': st.get('risk', 0.01), 'eq_curve': eq_curve,
           'journal': [x.strip() for x in journal_tail],
           'started': st.get('started'),
           'live': True, 'side': cfg['side']}
    out.update(base)
    return out

def queue_control(body):
    strat = body.get('strat', 'brk20')
    if strat not in LIVE:
        return False
    path = LIVE[strat]['control']
    cmds = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                cmds = json.load(f)
        except Exception:
            cmds = []
    body['ts'] = int(time.time())
    cmds.append(body)
    save_json(path, cmds)
    return True

class Handler(BaseHTTPRequestHandler):
    token = None

    def log_message(self, *a):
        pass

    def _ok(self, q):
        return q.get('key', [''])[0] == self.token

    def _send(self, code, body, ctype='application/json'):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._ok(q):
            self._send(403, json.dumps({'error': 'bad token'}))
            return
        if u.path in ('/', '/index.html'):
            with open(INDEX_F, 'rb') as f:
                self._send(200, f.read(), 'text/html; charset=utf-8')
        elif u.path == '/api/state':
            self._send(200, json.dumps(api_state(q.get('strat', ['brk20'])[0]), ensure_ascii=False))
        elif u.path == '/api/klines':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in SYMS:
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            tf = q.get('tf', ['1h'])[0]
            if tf not in TFS:
                tf = '1h'
            rows = klines(sym, tf, 500)
            self._send(200, json.dumps({'symbol': sym, 'tf': tf,
                                        'candles': [{'t': t//1000, 'o': o, 'h': h, 'l': l, 'c': c}
                                                    for t, o, h, l, c in rows]}))
        elif u.path == '/api/drawings':
            sym = q.get('symbol', [''])[0]
            d = load_json(DRAW_F, {})
            self._send(200, json.dumps({'symbol': sym, 'items': d.get(sym, [])}, ensure_ascii=False))
        elif u.path == '/api/depth':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in SYMS:
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            d = depth(sym)
            d['symbol'] = sym
            self._send(200, json.dumps(d))
        elif u.path == '/api/quotes':
            self._send(200, json.dumps({'rows': quotes()}))
        elif u.path == '/api/trades':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in SYMS:
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            self._send(200, json.dumps(trades_tape(sym)))
        elif u.path == '/api/indicator':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in SYMS:
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            strat = q.get('strat', ['brk20'])[0]
            side = LIVE.get(strat, {}).get('side', 'long')
            both = q.get('both', ['0'])[0] == '1'
            d = donchian(sym, side, both)
            d['symbol'] = sym
            self._send(200, json.dumps(d))
        else:
            self._send(404, json.dumps({'error': 'not found'}))

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._ok(q):
            self._send(403, json.dumps({'error': 'bad token'}))
            return
        ln = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(ln) or b'{}')
        except Exception:
            self._send(400, json.dumps({'error': 'bad json'})); return

        if u.path == '/api/control':
            cmd = body.get('cmd')
            allowed = ('pause', 'resume', 'close', 'cancel', 'set_risk', 'set_equity',
                       'manual_order', 'close_all', 'cancel_all')
            if cmd not in allowed:
                self._send(400, json.dumps({'error': 'bad cmd'})); return
            if cmd in ('close', 'cancel') and body.get('sym') not in SYMS:
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            if cmd == 'manual_order':
                if body.get('sym') not in SYMS:
                    self._send(400, json.dumps({'error': 'bad symbol'})); return
                if body.get('otype') not in ('market', 'limit', 'stop'):
                    body['otype'] = 'market'
                if body.get('side') not in ('long', 'short'):
                    body['side'] = 'long'
            if not queue_control(body):
                self._send(400, json.dumps({'error': 'слот пуст — некому исполнять команду'})); return
            self._send(200, json.dumps({'ok': True, 'queued': body}, ensure_ascii=False))

        elif u.path == '/api/drawings':
            sym = body.get('symbol')
            if sym not in SYMS:
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            d = load_json(DRAW_F, {})
            items = d.get(sym, [])
            act = body.get('action', 'add')
            if act == 'add':
                item = body.get('item') or {}
                item['id'] = int(time.time()*1000)
                item['tf'] = body.get('tf', '1h')
                if item.get('type') not in ('hline', 'trend', 'note', 'marker',
                                            'rect', 'brush', 'measure'):
                    self._send(400, json.dumps({'error': 'bad drawing type'})); return
                items.append(item)
            elif act == 'del':
                iid = body.get('id')
                items = [x for x in items if x.get('id') != iid]
            elif act == 'clear':
                tf = body.get('tf')
                items = [x for x in items if tf and x.get('tf') != tf]
            d[sym] = items[-200:]
            save_json(DRAW_F, d)
            self._send(200, json.dumps({'ok': True, 'items': d[sym]}, ensure_ascii=False))

        elif u.path == '/api/note':
            tid = str(body.get('id', ''))
            strat = str(body.get('strat', 'brk20'))
            text = str(body.get('text', ''))[:1000]
            if not tid:
                self._send(400, json.dumps({'error': 'no id'})); return
            key = strat + ':' + tid
            notes = load_json(NOTES_F, {})
            if text:
                notes[key] = text
            else:
                notes.pop(key, None)
            save_json(NOTES_F, notes)
            self._send(200, json.dumps({'ok': True}))

        else:
            self._send(404, json.dumps({'error': 'not found'}))

def main():
    conf = load_conf()
    Handler.token = conf['token']
    port = conf.get('port', 8080)
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'BRK-20 WEB TERMINAL v3 на порту {port}', flush=True)
    print(f'URL для браузера: http://<IP-сервера>:{port}/?key={conf["token"]}', flush=True)
    srv.serve_forever()

if __name__ == '__main__':
    main()
