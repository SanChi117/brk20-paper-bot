#!/usr/bin/env python3
# BRK-20 WEB TERMINAL v1.0 — браузерный терминал для бумажного бота
# Графики, позиции, заявки, сделки, управление (пауза/закрыть/отменить/риск).
# Авторизация: токен в URL (?key=...). Только бумажный режим — ключей биржи нет.
import json, os, time, csv, secrets
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

BASE = 'https://fapi.binance.com'
BYBIT = 'https://api.bybit.com'
SYMS = ['SOLUSDT','BNBUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','SUIUSDT','APTUSDT','NEARUSDT',
        'LINKUSDT','AAVEUSDT','ARBUSDT','OPUSDT','DOGEUSDT','TAOUSDT','ONDOUSDT','INJUSDT','SEIUSDT']

def load_conf():
    if not os.path.exists(CONF_F):
        with open(CONF_F, 'w') as f:
            json.dump({'token': secrets.token_urlsafe(16), 'port': 8080}, f)
    with open(CONF_F) as f:
        return json.load(f)

def load_state():
    try:
        with open(STATE_F) as f:
            return json.load(f)
    except Exception:
        return {'eq': 1000.0, 'positions': [], 'pendings': [], 'trades': [], 'paused': False}

def klines(sym, interval, limit=300):
    now = int(time.time()*1000)
    ms = {'1h': 3600000, '4h': 4*3600000}[interval]
    try:
        r = requests.get(f'{BASE}/fapi/v1/klines',
                         params={'symbol': sym, 'interval': interval, 'limit': limit}, timeout=15)
        if r.status_code == 200:
            out = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r.json()]
            return [x for x in out if x[0] + ms <= now]
        raise RuntimeError(f'HTTP {r.status_code}')
    except Exception:
        pass
    try:
        r = requests.get(f'{BYBIT}/v5/market/kline',
                         params={'category': 'linear', 'symbol': sym,
                                 'interval': '60' if interval == '1h' else '240',
                                 'limit': min(limit, 1000)}, timeout=15)
        j = r.json()
        if j.get('retCode') == 0:
            out = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                   for x in reversed(j['result']['list'])]
            return [x for x in out if x[0] + ms <= now]
    except Exception:
        pass
    return []

def api_state():
    st = load_state()
    trs = st.get('trades', [])
    gw = sum(t['r_usd'] for t in trs if t['r_usd'] > 0)
    gl = -sum(t['r_usd'] for t in trs if t['r_usd'] < 0)
    pf = gw/gl if gl > 0 else None
    wr = sum(1 for t in trs if t['r_usd'] > 0)/max(1, len(trs))
    n = len(trs)
    gate = 'ИДЁТ' if n < 30 else ('ПРОЙДЕН' if (pf or 0) > 1.2 else 'ПРОВАЛЕН')
    eq_curve = []
    eq = 1000.0
    for t in trs:
        eq += t['r_usd']
        eq_curve.append(round(eq, 2))
    journal_tail = []
    if os.path.exists(JOURNAL_F):
        with open(JOURNAL_F) as f:
            journal_tail = f.readlines()[-50:]
    return {'eq': round(st['eq'], 2), 'positions': st.get('positions', []),
            'pendings': st.get('pendings', []), 'trades': trs[-200:],
            'n': n, 'pf': round(pf, 3) if pf else None, 'wr': round(wr*100, 1),
            'gate': gate, 'paused': st.get('paused', False),
            'risk': st.get('risk', 0.01), 'eq_curve': eq_curve,
            'journal': [x.strip() for x in journal_tail],
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

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
            self._send(200, json.dumps(api_state()))
        elif u.path == '/api/klines':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in SYMS:
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            tf = q.get('tf', ['1h'])[0]
            if tf not in ('1h', '4h'):
                tf = '1h'
            rows = klines(sym, tf, 300)
            self._send(200, json.dumps({'symbol': sym, 'tf': tf,
                                        'candles': [{'t': t//1000, 'o': o, 'h': h, 'l': l, 'c': c}
                                                    for t, o, h, l, c in rows]}))
        else:
            self._send(404, json.dumps({'error': 'not found'}))

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._ok(q):
            self._send(403, json.dumps({'error': 'bad token'}))
            return
        if u.path != '/api/control':
            self._send(404, json.dumps({'error': 'not found'})); return
        ln = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(ln) or b'{}')
        except Exception:
            self._send(400, json.dumps({'error': 'bad json'})); return
        cmd = body.get('cmd')
        if cmd not in ('pause', 'resume', 'close', 'cancel', 'set_risk', 'set_equity'):
            self._send(400, json.dumps({'error': 'bad cmd'})); return
        cmds = []
        if os.path.exists(CONTROL_F):
            try:
                with open(CONTROL_F) as f:
                    cmds = json.load(f)
            except Exception:
                cmds = []
        body['ts'] = int(time.time())
        cmds.append(body)
        with open(CONTROL_F, 'w') as f:
            json.dump(cmds, f)
        self._send(200, json.dumps({'ok': True, 'queued': body}))

def main():
    conf = load_conf()
    Handler.token = conf['token']
    port = conf.get('port', 8080)
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'BRK-20 WEB TERMINAL на порту {port}', flush=True)
    print(f'URL для браузера: http://<IP-сервера>:{port}/?key={conf["token"]}', flush=True)
    srv.serve_forever()

if __name__ == '__main__':
    main()
