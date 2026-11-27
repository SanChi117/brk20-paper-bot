#!/usr/bin/env python3
# BRK-20 WEB TERMINAL v4 — браузерный терминал для бумажного бота (лейаут биржи)
# Графики (15м/30м/1ч/4ч/1д), рисование и заметки на графике, журнал сделок,
# управление (пауза/закрыть/отменить/риск/equity), ручные заявки (заглушка),
# слоты стратегий (state изолирован per-стратегия), котировки пула 24ч,
# постоянные стакан и лента сделок по выбранной монете.
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
DRAW_F = os.path.join(DIR, 'drawings.json')   # рисунки на графиках
NOTES_F = os.path.join(DIR, 'notes.json')     # заметки к сделкам
MANUAL_F = os.path.join(DIR, 'manual.json')   # ручной бумажный учёт (web-терминал)

# живые стратегии: свои файлы состояния/журнала/команд, своя сторона
LIVE = {
    'brk20': {'state': STATE_F, 'journal': JOURNAL_F, 'control': CONTROL_F,
              'side': 'long', 'eq0': 1000.0},
    'slot2': {'state': os.path.join(DIR, 'state_slot2.json'),
              'journal': os.path.join(DIR, 'journal_slot2.log'),
              'control': os.path.join(DIR, 'control_slot2.json'),
              'side': 'short', 'eq0': 1000.0},
    'slot3': {'state': os.path.join(DIR, 'state_slot3.json'),
              'journal': os.path.join(DIR, 'journal_slot3.log'),
              'control': os.path.join(DIR, 'control_slot3.json'),
              'side': 'both', 'eq0': 1000.0},
    'slot4': {'state': os.path.join(DIR, 'state_slot4.json'),
              'journal': os.path.join(DIR, 'journal_slot4.log'),
              'control': os.path.join(DIR, 'control_slot4.json'),
              'side': 'short', 'eq0': 1000.0},
    'slot5': {'state': os.path.join(DIR, 'state_slot5.json'),
              'journal': os.path.join(DIR, 'journal_slot5.log'),
              'control': os.path.join(DIR, 'control_slot5.json'),
              'side': 'both', 'eq0': 100.0},
    'slot6': {'state': os.path.join(DIR, 'state_slot6.json'),
              'journal': os.path.join(DIR, 'journal_slot6.log'),
              'control': os.path.join(DIR, 'control_slot6.json'),
              'side': 'both', 'eq0': 100.0},
    'slot8': {'state': os.path.join(DIR, 'state_slot8.json'),
              'journal': os.path.join(DIR, 'journal_slot8.log'),
              'control': os.path.join(DIR, 'control_slot8.json'),
              'side': 'both', 'eq0': 1000.0},
    'slot7': {'state': os.path.join(DIR, 'state_slot7.json'),
              'journal': os.path.join(DIR, 'journal_slot7.log'),
              'control': os.path.join(DIR, 'control_slot7.json'),
              'side': 'both', 'eq0': 1000.0},
}
COPY_SLOTS = ('slot5', 'slot6', 'slot8')
EV_POOL = [s + 'USDT' for s in ('BTC ETH SOL BNB XRP DOGE ADA AVAX LINK DOT LTC BCH NEAR ATOM FIL ETC TRX UNI AAVE APT ARB OP '
                                'INJ SUI XLM EOS ALGO SAND MANA AXS CRV SNX COMP GRT ICP EGLD HBAR VET THETA RUNE DYDX GALA '
                                'CHZ KAVA 1INCH IMX LDO STX FET WLD SEI TIA ORDI').split()]

BASE = 'https://fapi.binance.com'
BYBIT = 'https://api.bybit.com'
SYMS = ['SOLUSDT','BNBUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','SUIUSDT','APTUSDT','NEARUSDT',
        'LINKUSDT','AAVEUSDT','ARBUSDT','OPUSDT','DOGEUSDT','TAOUSDT','ONDOUSDT','INJUSDT','SEIUSDT']
# пул V1.2 (слот 3): 24 монеты разработки + 30 монет независимой проверки бэктеста
POOL54 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT',
          'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'NEARUSDT', 'ATOMUSDT', 'FILUSDT', 'ETCUSDT', 'TRXUSDT', 'UNIUSDT',
          'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT', 'SUIUSDT',
          'XLMUSDT', 'EOSUSDT', 'ALGOUSDT', 'SANDUSDT', 'MANAUSDT', 'AXSUSDT', 'CRVUSDT', 'SNXUSDT', 'MKRUSDT',
          'COMPUSDT', 'GRTUSDT', 'ICPUSDT', 'EGLDUSDT', 'HBARUSDT', 'VETUSDT', 'THETAUSDT', 'RUNEUSDT', 'DYDXUSDT',
          'GALAUSDT', 'CHZUSDT', 'KAVAUSDT', '1INCHUSDT', 'IMXUSDT', 'LDOUSDT', 'STXUSDT', 'FETUSDT', 'WLDUSDT',
          'SEIUSDT', 'TIAUSDT', 'ORDIUSDT']
TFS = {'15m': ('15m', '15', 900000), '30m': ('30m', '30', 1800000),
       '1h': ('1h', '60', 3600000), '4h': ('4h', '240', 4*3600000),
       '1d': ('1d', 'D', 86400000)}

# эталон бэктеста для панели «факт против бэктеста» (n, WR %, PF, средний R; None — не считалось)
BT_REF = {
    'brk20': {'n': 191, 'wr': None, 'pf': 1.723, 'avg_r': None, 'src': 'bt_candidates19.py, 2021–2025'},
    'slot2': {'n': 117, 'wr': None, 'pf': 1.834, 'avg_r': None, 'src': 'bt_short1.py, 2021–2025'},
    'slot3': {'n': 2918, 'wr': 26.0, 'pf': 1.37, 'avg_r': 0.38, 'src': '54 монеты, 2022 – май 2026; июнь–окт 2026: PF 2.89'},
    'slot4': {'n': 301, 'wr': 56.0, 'pf': 1.48, 'avg_r': 0.21, 'src': 'все листинги USDT-M 2020 – май 2026'},
    'slot5': {'n': 1920, 'wr': 73.5, 'pf': 0.58, 'avg_r': -0.112, 'src': 'сигналы канала 03.2024 – 11.2026 по свечам Binance'},
    'slot6': {'n': 1807, 'wr': 78.0, 'pf': 0.61, 'avg_r': -0.071, 'src': 'сигналы канала 03.2024 – 11.2026 по свечам Binance'},
    'slot8': {'n': 37, 'wr': 49.0, 'pf': 0.52, 'avg_r': -0.21, 'src': 'сигналы ветки 06–11.2026 по свечам Binance (мало сделок)'},
    'slot7': {'n': 6146, 'wr': 59.5, 'pf': 1.9, 'avg_r': 1.09,
              'src': '2023 – окт 2026; R = доходность сделки в %; июнь–окт 2026 слабее (+0.5…+0.8% на сделку)'},
}

STRATEGIES = [
    {'id': 'brk20', 'name': 'BRK-20', 'kind': 'Пробой 20-дневного хая (4h)',
     'status': 'forward-gate', 'risk': '1%', 'pool': len(SYMS),
     'desc': 'Лонг-пробой Donchian-120 на 4h. Стоп −1.75×ATR, BE на +2R, трейл по пивотам. Форвард-гейт: 30 сделок, PF>1.2.'},
    {'id': 'slot2', 'name': 'BRK-20 SHORT', 'kind': 'Пробой 20-дневного лоу (4h), шорт',
     'status': 'forward-gate', 'risk': '1%', 'pool': len(SYMS),
     'desc': 'Зеркало BRK-20: пробой Donchian-120 вниз, sell-stop, стоп +1.75×ATR, BE на +2R, трейл по пивот-хаям. Бэктест: 117 сделок, PF 1.834, стресс 1.785. Свой счёт, свой гейт: 30 сделок, PF>1.2.'},
    {'id': 'slot3', 'name': 'V1.2 ПРОБОЙ', 'kind': 'Сильный пробой после сжатия (1h), лонг и шорт',
     'status': 'forward-gate', 'risk': '1%', 'pool': len(POOL54),
     'desc': 'Сжатие Боллинджера → пробой 36-часового диапазона с баллом силы ≥5/6 → вход по рынку, стоп за уровнем −0.5 ATR, '
             'добор на +1R, выход за EMA20 или через 72 ч. До 8 позиций. Бот сверен с бэктестом: 3199 из 3199 сделок совпали.'},
    {'id': 'slot4', 'name': 'ЛИСТИНГИ', 'kind': 'Шорт новых фьючерсов Binance после слома пампа',
     'status': 'forward-gate', 'risk': '5%', 'pool': 0,
     'desc': 'Новый листинг: памп ≥ +5% к первой цене, затем час закрылся ниже неё → шорт. Стоп над хаем +2%, тейк −25%, '
             'максимум 14 дней. Только крипто-контракты, акции и TradFi исключены.'},
    {'id': 'slot5', 'name': 'CRYPTOX ИЗОЛ', 'kind': 'Копия сигналов канала CryptoX | ProTrading, изолированная маржа',
     'status': 'forward-gate', 'risk': '10% банка маржой', 'pool': 0,
     'desc': 'Бот читает канал и входит по рынку в момент поста: размер как у канала («Входим на X$ / Банк Y$», иначе 10%), '
             'плечо из поста. 50% на первом тейке и стоп в безубыток, остаток на втором, максимум 14 дней. Изолированная '
             'маржа: если стоп канала дальше ликвидации, выход по ликвидации. Старт 100$, как марафон канала.'},
    {'id': 'slot6', 'name': 'CRYPTOX КРОСС', 'kind': 'Те же сигналы CryptoX, кросс-маржа (ликвидации нет)',
     'status': 'forward-gate', 'risk': '10% банка маржой', 'pool': 0,
     'desc': 'То же, что слот 5, но кросс-маржа: позицию держит весь счёт, работает стоп канала; сигналы без стопа держатся '
             'до тейка или 14 дней. Старт 100$.'},
    {'id': 'slot8', 'name': 'VIP CRYPTOX', 'kind': 'Копия ветки Vip CryptoX один в один с постом',
     'status': 'forward-gate', 'risk': 'как в посте', 'pool': 0,
     'desc': 'Бот читает ветку и повторяет пост: плечо, кросс-маржа, размер (риск или маржа из поста, иначе 3% маржи), '
             'вход по рынку или лимит/триггер от точки (ждёт 48 ч), стоп, перенос в безубыток на указанной точке, частичные '
             'фиксации в процентах из поста. Посты без стопа или тейков в тексте пропускаются. Старт 1000$.'},
    {'id': 'slot7', 'name': 'СОБЫТИЯ', 'kind': 'Метка мониторинга Binance (шорт 24 ч) + листинг Upbit (шорт 3 дня) + отскок после пролива (лонг 1 ч)',
     'status': 'forward-gate', 'risk': '5% / 20% капитала на сделку', 'pool': 53,
     'desc': 'Шорт монет, на которые Binance вешает метку мониторинга: через 5 минут после объявления, выход через сутки, '
             'аварийный стоп +20%. Шорт монеты через час после её листинга на Upbit, стоп +10%, выход через 3 дня. Лонг после 5-минутной свечи −3% и ниже или разворота (−5% от максимума 2 ч и свеча +2%), '
             'выход через час, до 5 сделок одновременно. Обе идеи прошли проверку на 2023–2026 (p2/PREREG.md), на свежих '
             'месяцах эффект слабее.'},
]

def pool_of(strat):
    if strat in ('brk20', 'slot2'):
        return list(SYMS)
    if strat == 'slot3':
        st = load_state(LIVE['slot3']['state']) or {}
        return st.get('pool') or list(POOL54)
    if strat == 'slot4':
        st = load_state(LIVE['slot4']['state']) or {}
        syms = [p['sym'] for p in st.get('positions', [])] + [p['sym'] for p in st.get('pendings', [])]
        syms += [t['sym'] for t in st.get('trades', [])[-20:]]
        return list(dict.fromkeys(syms))
    if strat in COPY_SLOTS:
        st = load_state(LIVE[strat]['state']) or {}
        syms = [p['sym'] for p in st.get('positions', [])] + [t['sym'] for t in st.get('trades', [])[-20:]]
        return list(dict.fromkeys(syms))
    if strat == 'slot7':
        st = load_state(LIVE[strat]['state']) or {}
        extra = [p['sym'] for p in st.get('positions', [])] + [t['sym'] for t in st.get('trades', [])[-20:]]
        return list(dict.fromkeys(extra + EV_POOL))
    return list(SYMS)

def allowed_syms():
    out = set(SYMS) | set(POOL54)
    for k in ('slot4', 'slot7') + COPY_SLOTS:
        out |= set(pool_of(k))
    out |= {p['sym'] for p in manual_book().get('positions', [])}
    return out

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
    biv, bbiv, ms = TFS[tf]
    try:
        r = requests.get(f'{BASE}/fapi/v1/klines',
                         params={'symbol': sym, 'interval': biv, 'limit': limit}, timeout=6)
        if r.status_code == 200:
            # БЕЗ фильтра "x[0]+ms<=now": последняя (формирующаяся) свеча нужна графику —
            # иначе цена на 1h/4h «замораживается» на закрытии прошлого бара.
            return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in r.json()]
        raise RuntimeError(f'HTTP {r.status_code}')
    except Exception:
        pass
    try:
        r = requests.get(f'{BYBIT}/v5/market/kline',
                         params={'category': 'linear', 'symbol': sym, 'interval': bbiv,
                                 'limit': min(limit, 1000)}, timeout=6)
        j = r.json()
        if j.get('retCode') == 0:
            return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6]))
                    for x in reversed(j['result']['list'])]
    except Exception:
        pass
    return []

def ema_list(x, n=20):
    a = 2 / (n + 1); out = []
    for v in x:
        out.append(v if not out else a * v + (1 - a) * out[-1])
    return out

def indicator_lines(sym, strat, both=False):
    """Линии стратегии на графике: [{name, color, width, step, pts:[[t_sec, v], ...]}]."""
    if strat in ('brk20', 'slot2'):
        d = donchian(sym, LIVE[strat]['side'], True)
        if not d.get('hi20'):
            return {'lines': [], 'info': ''}
        mine = 'LO20' if strat == 'slot2' else 'HI20'
        lines = []
        for name, pts, col in (('HI20', d['hi20'], '#f0b90b'), ('LO20', d['lo20'], '#5b8cff')):
            if both or name == mine:
                lines.append({'name': name, 'color': col, 'width': 2 if name == mine else 1, 'step': True, 'pts': pts})
        info = ' · '.join(f"{l['name']} {l['pts'][-1][1]:.6g}" for l in lines) + (f" · ATR(4h) {d['atr']:.6g}" if d.get('atr') else '')
        return {'lines': lines, 'info': info}
    if strat == 'slot3':
        rows = klines(sym, '1h', 500)
        if len(rows) < 60:
            return {'lines': [], 'info': ''}
        closes = [r[4] for r in rows]; em = ema_list(closes)
        hi = [[rows[i][0] // 1000, max(r[2] for r in rows[i - 36:i])] for i in range(36, len(rows))]
        lo = [[rows[i][0] // 1000, min(r[3] for r in rows[i - 36:i])] for i in range(36, len(rows))]
        lines = [{'name': 'EMA20', 'color': '#8a5cff', 'width': 2, 'step': False,
                  'pts': [[rows[i][0] // 1000, em[i]] for i in range(20, len(rows))]}]
        lines += [{'name': 'Хай 36ч', 'color': '#0ecb81', 'width': 1, 'step': True, 'pts': hi},
                      {'name': 'Лоу 36ч', 'color': '#f6465d', 'width': 1, 'step': True, 'pts': lo}]
        return {'lines': lines, 'info': f"EMA20(1h) {em[-1]:.6g} · диапазон 36ч {lo[-1][1]:.6g} – {hi[-1][1]:.6g}"}
    if strat == 'slot4':
        st = load_state(LIVE['slot4']['state']) or {}
        x = (st.get('watch') or {}).get(sym)
        if not x:
            return {'lines': [], 'info': ''}
        rows = klines(sym, '1h', 500)
        rows = [r for r in rows if r[0] >= x['onboard'] // 3600000 * 3600000]
        if not rows:
            return {'lines': [], 'info': ''}
        p0 = rows[0][1]; t0 = rows[0][0] // 1000; t1 = rows[-1][0] // 1000
        return {'lines': [{'name': 'P0', 'color': '#f0b90b', 'width': 2, 'step': True, 'pts': [[t0, p0], [t1, p0]]},
                          {'name': '+5%', 'color': '#5b8cff', 'width': 1, 'step': True, 'pts': [[t0, p0 * 1.05], [t1, p0 * 1.05]]}],
                'info': f'P0 (первая цена) {p0:.6g} · вход — закрытие часа ниже P0 после пампа ≥ +5%'}
    return {'lines': [], 'info': ''}

def donchian(sym, side='long', both=False):
    """Индикатор стратегии: граница Donchian-120 на 4h (20 дней) + ATR(14) Wilder.
    Лонг (brk20): HI20 = макс хаёв за 120 баров — вход пробоем вверх.
    Шорт (slot2): LO20 = мин лоу за 120 баров — вход пробоем вниз.
    both=True: обе границы канала сразу (hi20 + lo20) для визуального сравнения."""
    rows = klines(sym, '4h', 300)
    if len(rows) < 130:
        return {'pts': [], 'hi20': [], 'lo20': [], 'atr': None, 'line': 'HI20' if side == 'long' else 'LO20'}
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    hi20 = [[rows[i][0] // 1000, max(highs[i-120:i])] for i in range(120, len(rows))]
    lo20 = [[rows[i][0] // 1000, min(lows[i-120:i])] for i in range(120, len(rows))]
    atr = None
    # ATR — только по ЗАКРЫТЫМ барам (как у бота): формирующуюся свечу отбрасываем,
    # иначе значение «плывёт» внутри бара и расходится с расчётом стратегии.
    closed = rows[:-1] if rows[-1][0] + TFS['4h'][2] > int(time.time()*1000) else rows
    trs = [max(closed[i][2]-closed[i][3], abs(closed[i][2]-closed[i-1][4]), abs(closed[i][3]-closed[i-1][4]))
           for i in range(1, len(closed))]
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
_TRG_CACHE = {'ts': 0.0, 'data': {}}

def trigger_levels():
    """Границы канала (HI20/LO20 на 4h) по всему пулу — для дистанции до триггера
    в сайдбаре. Кэш 60с: 4h-уровни двигаются редко, а считать их на каждый
    опрос котировок (5с) расточительно."""
    if time.time() - _TRG_CACHE['ts'] < 60 and _TRG_CACHE['data']:
        return _TRG_CACHE['data']
    data = {}
    for sym in SYMS:
        try:
            rows = klines(sym, '4h', 300)
            if len(rows) < 130:
                continue
            # уровень текущего бара = экстремум 120 ЗАКРЫТЫХ баров перед ним —
            # ровно тот, по которому бот выставит стоп-заявку
            closed = rows[:-1] if rows[-1][0] + TFS['4h'][2] > int(time.time()*1000) else rows
            data[sym] = (max(r[2] for r in closed[-120:]), min(r[3] for r in closed[-120:]))
        except Exception:
            continue
    if data:
        _TRG_CACHE['ts'] = time.time()
        _TRG_CACHE['data'] = data
    return _TRG_CACHE['data']

_ALLQ = {'ts': 0.0, 'rows': {}}
_CH36 = {'ts': 0.0, 'data': {}}

def all_tickers():
    """Все тикеры фьючерсов за один запрос (кэш 5с): котировки любого пула без лишних вызовов."""
    if time.time() - _ALLQ['ts'] < 5 and _ALLQ['rows']:
        return _ALLQ['rows']
    rows = {}
    try:
        r = requests.get(f'{BASE}/fapi/v1/ticker/24hr', timeout=8)
        if r.status_code == 200:
            for x in r.json():
                rows[x['symbol']] = {'sym': x['symbol'], 'px': float(x['lastPrice']),
                                     'chg': float(x['priceChangePercent']), 'o': float(x['openPrice']),
                                     'h': float(x['highPrice']), 'l': float(x['lowPrice']),
                                     'vol': float(x['quoteVolume']), 'src': 'binance'}
    except Exception:
        pass
    if not rows:
        try:
            r = requests.get(f'{BYBIT}/v5/market/tickers', params={'category': 'linear'}, timeout=8)
            j = r.json()
            if j.get('retCode') == 0:
                for x in j['result']['list']:
                    rows[x['symbol']] = {'sym': x['symbol'], 'px': float(x['lastPrice']),
                                         'chg': float(x.get('price24hPcnt') or 0) * 100,
                                         'o': float(x.get('prevPrice24h') or 0), 'h': float(x['highPrice24h']),
                                         'l': float(x['lowPrice24h']), 'vol': float(x['turnover24h']), 'src': 'bybit'}
        except Exception:
            pass
    if rows:
        _ALLQ['ts'] = time.time(); _ALLQ['rows'] = rows
    return _ALLQ['rows']

def channel36():
    """Диапазон 36 закрытых часов по пулу V1.2 — дистанция до пробоя в сайдбаре (кэш 10 мин)."""
    if time.time() - _CH36['ts'] < 600 and _CH36['data']:
        return _CH36['data']
    data = {}
    for sym in pool_of('slot3'):
        rows = klines(sym, '1h', 40)
        closed = rows[:-1] if rows and rows[-1][0] + 3600000 > int(time.time() * 1000) else rows
        if len(closed) >= 36:
            data[sym] = (max(r[2] for r in closed[-36:]), min(r[3] for r in closed[-36:]))
    if data:
        _CH36['ts'] = time.time(); _CH36['data'] = data
    return _CH36['data']

def quotes(strat='brk20'):
    """Котировки пула стратегии: цена, % за 24ч, OHLC 24ч, оборот + дистанция до уровней входа."""
    allq = all_tickers()
    pool = pool_of(strat)
    rows = [dict(allq[s]) for s in pool if s in allq]
    if strat in ('brk20', 'slot2'):
        lv, names = trigger_levels(), ('HI20', 'LO20')
    elif strat == 'slot3':
        lv, names = channel36(), ('хай 36ч', 'лоу 36ч')
    elif strat == 'slot4':
        st = load_state(LIVE['slot4']['state']) or {}
        lv = {p['sym']: (p['stop'], p['entry']) for p in st.get('pendings', [])}
        names = ('стоп', 'P0')
    else:
        lv, names = {}, ('', '')
    for r in rows:
        if r['sym'] in lv and r['px']:
            hi, lo = lv[r['sym']]
            r['d_hi'] = round((hi / r['px'] - 1) * 100, 2)
            r['d_lo'] = round((lo / r['px'] - 1) * 100, 2)
            r['lv'] = names
    return rows

def _px_map():
    return {k: v['px'] for k, v in all_tickers().items() if v.get('px')}

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

# ---------- ручной бумажный учёт ----------
# Позиции/заявки, открытые руками из панели терминала. Своя книга (manual.json),
# к сделкам ботов не смешивается — но в интерфейсе показывается рядом.
MANUAL_EMPTY = {'positions': [], 'pendings': [], 'trades': [], 'seq': 1}

def manual_book():
    return load_json(MANUAL_F, dict(MANUAL_EMPTY))

def manual_save(b):
    save_json(MANUAL_F, b)

def manual_check_pendings(b, pxm):
    """Ленивое исполнение лимитных/стоп заявок: веб-сервер пассивен, поэтому
    проверяем пересечение цены при каждом обращении к API."""
    still = []
    for p in b['pendings']:
        px = pxm.get(p['sym'])
        hit = None
        if px:
            if p['otype'] == 'limit':
                hit = px <= p['price'] if p['side'] == 'long' else px >= p['price']
            elif p['otype'] == 'stop':
                hit = px >= p['price'] if p['side'] == 'long' else px <= p['price']
        if hit:
            b['positions'].append({'id': p['id'], 'sym': p['sym'], 'side': p['side'],
                                   'entry': px, 'qty_usd': p['qty_usd'], 'stop': p.get('stop'),
                                   'note': p.get('note', ''), 'entry_t': int(time.time()*1000)})
        else:
            still.append(p)
    b['pendings'] = still

def manual_order(body):
    """Открытие ручной позиции/заявки. Возвращает (ok, описание)."""
    b = manual_book()
    pxm = _px_map()
    sym, side, otype = body['sym'], body['side'], body.get('otype', 'market')
    qty = float(body.get('qty') or 0)
    price = body.get('price')
    price = float(price) if price not in (None, '', 'null') else None
    stop = body.get('stop')
    stop = float(stop) if stop not in (None, '', 'null') else None
    note = str(body.get('note') or '')[:200]
    mid = b.get('seq', 1); b['seq'] = mid + 1
    if qty <= 0:
        return False, 'укажи размер, $'
    if otype == 'market':
        px = pxm.get(sym)
        if not px:
            return False, 'нет цены по ' + sym
        b['positions'].append({'id': mid, 'sym': sym, 'side': side, 'entry': px,
                               'qty_usd': qty, 'stop': stop, 'note': note,
                               'entry_t': int(time.time()*1000)})
        manual_save(b)
        return True, f'открыта {"лонг" if side == "long" else "шорт"} {sym} по {px}'
    if not price:
        return False, 'укажи цену заявки'
    b['pendings'].append({'id': mid, 'sym': sym, 'side': side, 'otype': otype,
                          'price': price, 'qty_usd': qty, 'stop': stop, 'note': note,
                          'ts': int(time.time()*1000)})
    manual_save(b)
    return True, f'заявка {otype} {sym} по {price} ждёт цену'

def manual_close(mid):
    b = manual_book()
    pxm = _px_map()
    for p in list(b['positions']):
        if p['id'] == mid:
            px = pxm.get(p['sym'])
            if not px:
                return False, 'нет цены по ' + p['sym']
            pnl_pct = ((px / p['entry'] - 1) if p['side'] == 'long'
                       else (p['entry'] / px - 1)) * 100
            pnl = p['qty_usd'] * pnl_pct / 100
            b['positions'].remove(p)
            b['trades'].append({'id': p['id'], 'sym': p['sym'], 'side': p['side'],
                                'entry': p['entry'], 'exit': px, 'qty_usd': p['qty_usd'],
                                'entry_t': p['entry_t'], 'exit_t': int(time.time()*1000),
                                'pnl_usd': round(pnl, 2), 'pnl_pct': round(pnl_pct, 2),
                                'note': p.get('note', ''), 'out': 'manual'})
            manual_save(b)
            return True, f'закрыта {p["sym"]}: {pnl:+.2f}$ ({pnl_pct:+.2f}%)'
    return False, 'позиция не найдена'

def manual_cancel(mid):
    b = manual_book()
    n = len(b['pendings'])
    b['pendings'] = [p for p in b['pendings'] if p['id'] != mid]
    manual_save(b)
    return len(b['pendings']) < n

def manual_view():
    """Снимок ручной книги с живым PnL открытых позиций — для /api/state."""
    b = manual_book()
    pxm = _px_map()
    manual_check_pendings(b, pxm)
    if b != load_json(MANUAL_F, {}):
        manual_save(b)
    pos = []
    for p in b['positions']:
        px = pxm.get(p['sym'])
        pnl_pct = (((px / p['entry'] - 1) if p['side'] == 'long' else (p['entry'] / px - 1)) * 100) if px else None
        pos.append({**p, 'px': px,
                    'pnl_pct': round(pnl_pct, 2) if pnl_pct is not None else None,
                    'pnl_usd': round(p['qty_usd'] * pnl_pct / 100, 2) if pnl_pct is not None else None})
    notes = load_json(NOTES_F, {})
    trades = []
    for t in b['trades'][-300:]:
        t = dict(t)
        k = 'manual:' + str(t['id'])
        if k in notes:
            t['note2'] = notes[k]
        trades.append(t)
    pnl_sum = round(sum(t['pnl_usd'] for t in b['trades']), 2)
    return {'positions': pos, 'pendings': b['pendings'], 'trades': trades,
            'n': len(b['trades']), 'pnl': pnl_sum}

def parity(strat, trs):
    """Факт против бэктеста: те же метрики по закрытым сделкам бота (ручные закрытия не считаются)."""
    ref = BT_REF.get(strat, {})
    t = [x for x in trs if x.get('out') != 'manual']
    n = len(t)
    rs = [x.get('r', 0.0) for x in t]
    gw = sum(x['r_usd'] for x in t if x['r_usd'] > 0); gl = -sum(x['r_usd'] for x in t if x['r_usd'] < 0)
    live = {'n': n, 'wr': round(100 * sum(1 for x in t if x['r_usd'] > 0) / n, 1) if n else None,
            'pf': round(gw / gl, 2) if gl > 0 else None, 'avg_r': round(sum(rs) / n, 3) if n else None,
            'sum_r': round(sum(rs), 2)}
    if n < 20:
        verdict, level = f'мало сделок для вывода ({n}/20)', 'wait'
    elif strat in COPY_SLOTS:
        verdict, level = (('в плюсе — лучше, чем канал показал на истории', 'ok') if (live['pf'] or 0) > 1.0
                          else ('в минусе — как на истории канала', 'bad'))
    elif live['pf'] is not None and ref.get('pf') and live['pf'] >= 0.8 * ref['pf']:
        verdict, level = 'в пределах бэктеста', 'ok'
    elif live['pf'] is not None and live['pf'] >= 1.0:
        verdict, level = 'в плюсе, но хуже бэктеста — наблюдать', 'warn'
    else:
        verdict, level = 'хуже бэктеста — кандидат на остановку', 'bad'
    return {'ref': ref, 'live': live, 'verdict': verdict, 'level': level}

def api_state(strat='brk20'):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    base = {'strategies': STRATEGIES, 'syms': pool_of(strat), 'strat': strat, 'ts': ts,
            'pool': pool_of(strat), 'pool_missing': (load_state(LIVE[strat]['state']) or {}).get('pool_missing', [])
            if strat in LIVE else [], 'parity': parity(strat, [])}
    if strat not in LIVE:
        # пустой слот: свой «нулевой» стейт, ничего общего с живыми стратегиями
        out = dict(EMPTY_STATE); out.update(base); out['live'] = False
        out['manual'] = manual_view()
        return out
    cfg = LIVE[strat]
    st = load_state(cfg['state'])
    if st is None:
        # бот ещё не создал state (не запущен) — показываем свежий нулевой гейт
        out = dict(EMPTY_STATE); out.update(base)
        out.update({'eq': cfg['eq0'], 'gate': 'ИДЁТ', 'live': True, 'side': cfg['side'], 'not_started': True})
        out['manual'] = manual_view()
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
    out['parity'] = parity(strat, trs)
    if strat == 'slot4':
        out['watch'] = {k: {kk: vv for kk, vv in v.items() if kk in ('onboard', 'state', 'why')}
                        for k, v in (st.get('watch') or {}).items()}
    if strat in COPY_SLOTS:
        cl = st.get('claimed') or []
        out['copy'] = {'claimed': cl[-1][1] if cl else None, 'claimed_t': cl[-1][0] if cl else None,
                       'claimed_first': cl[0][1] if cl else None, 'skips': (st.get('skips') or [])[-30:]}
    out['manual'] = manual_view()
    return out

def overview():
    """Короткая сводка по всем слотам — для переключателя стратегий в шапке."""
    out = {}
    for k, cfg in LIVE.items():
        st = load_state(cfg['state'])
        if st is None:
            out[k] = {'live': False, 'eq': cfg['eq0'], 'n': 0, 'pos': 0}
            continue
        trs = [t for t in st.get('trades', []) if t.get('out') != 'manual']
        out[k] = {'live': True, 'eq': round(st.get('eq', cfg['eq0']), 2), 'eq0': cfg['eq0'], 'n': len(trs),
                  'pos': len(st.get('positions', [])), 'paused': st.get('paused', False)}
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

def trades_csv(strat='brk20'):
    """CSV-выгрузка сделок: ботские выбранного слота + ручные, одной таблицей."""
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['источник', 'id', 'монета', 'сторона', 'вход', 'выход',
                'время входа', 'время выхода', 'R', 'результат $', 'выход по', 'заметка'])
    fmt_t = lambda ms: datetime.fromtimestamp(ms/1000, timezone.utc).strftime('%Y-%m-%d %H:%M') if ms else ''
    if strat in LIVE:
        st = load_state(LIVE[strat]['state']) or {}
        side = LIVE[strat]['side']
        for t in st.get('trades', []):
            w.writerow([strat, t.get('id'), t.get('sym'), t.get('side', side), t.get('entry'), t.get('exit'),
                        fmt_t(t.get('entry_t')), fmt_t(t.get('exit_t')),
                        t.get('r'), t.get('r_usd'), t.get('out'), ''])
    for t in manual_book().get('trades', []):
        w.writerow(['ручная', t.get('id'), t.get('sym'), t.get('side'), t.get('entry'), t.get('exit'),
                    fmt_t(t.get('entry_t')), fmt_t(t.get('exit_t')),
                    '', t.get('pnl_usd'), 'вручную', t.get('note', '')])
    return buf.getvalue()

class Handler(BaseHTTPRequestHandler):
    token = None

    def log_message(self, *a):
        pass

    def _ok(self, q):
        # постоянное по времени сравнение; ключ можно передать заголовком X-Key вместо URL
        given = self.headers.get('X-Key') or q.get('key', [''])[0]
        return bool(self.token) and secrets.compare_digest(str(given), str(self.token))

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
        if u.path == '/favicon.ico':
            self._send(204, b'', 'image/x-icon'); return
        if u.path == '/static/lwc.js':          # библиотека графиков — своя копия, без внешнего CDN
            with open(os.path.join(DIR, 'web', 'lwc.js'), 'rb') as f:
                b = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript')
            self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers(); self.wfile.write(b)
            return
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
            if sym not in allowed_syms():
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            tf = q.get('tf', ['1h'])[0]
            if tf not in TFS:
                tf = '1h'
            rows = klines(sym, tf, 500)
            self._send(200, json.dumps({'symbol': sym, 'tf': tf,
                                        'candles': [{'t': r[0]//1000, 'o': r[1], 'h': r[2], 'l': r[3], 'c': r[4],
                                                     'v': r[5] if len(r) > 5 else 0} for r in rows]}))
        elif u.path == '/api/drawings':
            sym = q.get('symbol', [''])[0]
            d = load_json(DRAW_F, {})
            self._send(200, json.dumps({'symbol': sym, 'items': d.get(sym, [])}, ensure_ascii=False))
        elif u.path == '/api/depth':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in allowed_syms():
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            d = depth(sym)
            d['symbol'] = sym
            self._send(200, json.dumps(d))
        elif u.path == '/api/quotes':
            self._send(200, json.dumps({'rows': quotes(q.get('strat', ['brk20'])[0])}))
        elif u.path == '/api/overview':
            self._send(200, json.dumps(overview()))
        elif u.path == '/api/tgexports':
            d = os.path.join(DIR, 'tg_exports')
            files = sorted(os.listdir(d)) if os.path.isdir(d) else []
            self._send(200, json.dumps({'files': [{'name': f, 'size': os.path.getsize(os.path.join(d, f)),
                                                   'mtime': int(os.path.getmtime(os.path.join(d, f)) * 1000)}
                                                  for f in files if f.endswith('.json')]}))
        elif u.path == '/api/tgexport':
            f = os.path.basename(q.get('f', [''])[0])
            fp = os.path.join(DIR, 'tg_exports', f)
            if not f.endswith('.json') or not os.path.isfile(fp):
                self._send(404, json.dumps({'error': 'нет файла'})); return
            with open(fp, 'rb') as fh:
                b = fh.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Disposition', f'attachment; filename="{f}"')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers(); self.wfile.write(b)
            return
        elif u.path == '/api/trades_csv':
            strat = q.get('strat', ['brk20'])[0]
            self._send(200, trades_csv(strat), 'text/csv; charset=utf-8')
        elif u.path == '/api/trades':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in allowed_syms():
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            self._send(200, json.dumps(trades_tape(sym)))
        elif u.path == '/api/indicator':
            sym = q.get('symbol', ['SOLUSDT'])[0]
            if sym not in allowed_syms():
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            strat = q.get('strat', ['brk20'])[0]
            both = q.get('both', ['0'])[0] == '1'
            d = indicator_lines(sym, strat, both)
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
            # ручные команды исполняет сам веб-сервер (своя книга manual.json)
            if cmd == 'manual_order':
                if body.get('sym') not in allowed_syms():
                    self._send(400, json.dumps({'error': 'bad symbol'})); return
                if body.get('otype') not in ('market', 'limit', 'stop'):
                    body['otype'] = 'market'
                if body.get('side') not in ('long', 'short'):
                    body['side'] = 'long'
                ok, msg = manual_order(body)
                self._send(200 if ok else 400, json.dumps({'ok': ok, 'msg': msg}, ensure_ascii=False))
                return
            if cmd == 'manual_close':
                ok, msg = manual_close(int(body.get('id') or 0))
                self._send(200 if ok else 400, json.dumps({'ok': ok, 'msg': msg}, ensure_ascii=False))
                return
            if cmd == 'manual_cancel':
                ok = manual_cancel(int(body.get('id') or 0))
                self._send(200 if ok else 400, json.dumps({'ok': ok}, ensure_ascii=False))
                return
            allowed = ('pause', 'resume', 'close', 'cancel', 'set_risk', 'set_equity',
                       'close_all', 'cancel_all')
            if cmd not in allowed:
                self._send(400, json.dumps({'error': 'bad cmd'})); return
            if cmd in ('close', 'cancel') and body.get('sym') not in allowed_syms():
                self._send(400, json.dumps({'error': 'bad symbol'})); return
            if not queue_control(body):
                self._send(400, json.dumps({'error': 'слот пуст — некому исполнять команду'})); return
            self._send(200, json.dumps({'ok': True, 'queued': body}, ensure_ascii=False))

        elif u.path == '/api/drawings':
            sym = body.get('symbol')
            if sym not in allowed_syms():
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
    # BRK20_WEB_HOST=127.0.0.1 — доступ только через SSH-туннель или HTTPS-прокси (рекомендуется)
    host = os.environ.get('BRK20_WEB_HOST', '0.0.0.0')
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'BRK-20 WEB TERMINAL v4 на порту {port}', flush=True)
    print(f'URL для браузера: http://<IP-сервера>:{port}/?key={conf["token"]}', flush=True)
    srv.serve_forever()

if __name__ == '__main__':
    main()
