#!/usr/bin/env python3
"""Скрининг Telegram-каналов с сигналами: разбор сигналов + проверка каждого по 15-минутным свечам Binance USDT-M.
python tgscreen/screen.py tg_raw kl_cache out
Правила копирования (механические, без суждений):
  вход:  M «по рынку» — открытие ближайшей 15m-свечи после поста;  L «лимит» — середина зоны входа, 48 ч, отмена если TP1 раньше;
  выход: «TP1» — вся позиция на первом тейке;  «лестница» — равные части на каждый тейк (до 4), после TP1 стоп в безубыток;
  стоп из сигнала; внутри одной свечи стоп раньше тейка (консервативно); максимум 14 дней; издержки 0.08%/сторону (рынок), 0.02% (лимит).
Сигнал засчитывается, только если в нём есть монета, сторона, стоп и хотя бы один тейк, а уровни согласуются с ценой рынка."""
import csv
import glob
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

RAW, KL, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(KL, exist_ok=True); os.makedirs(OUT, exist_ok=True)
M15 = 900_000
DATA_END = '2026-11-23'
LIST = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/'
VISION = 'https://data.binance.vision/data/futures/um'

# ------------------------------------------------------------------ список фьючерсов
def perps():
    if os.environ.get('TGS_STUB'):
        return {x + 'USDT' for x in 'BTC ETH SOL XRP XLM OP LDO C HAEDAL LINEA VANA SLP XVG 1000PEPE DOGE'.split()}
    fn = os.path.join(KL, '_perps.json')
    if os.path.exists(fn):
        return set(json.load(open(fn)))
    out, marker = [], ''
    while True:
        x = urllib.request.urlopen(LIST + (f'&marker={marker}' if marker else ''), timeout=60).read().decode()
        ps = re.findall(r'<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>', x)
        out += ps
        if '<IsTruncated>true' not in x or not ps:
            break
        marker = f'data/futures/um/monthly/klines/{ps[-1]}/'
    out = [s for s in out if s.endswith('USDT')]
    json.dump(out, open(fn, 'w'))
    return set(out)


PERPS = perps()
BASES = {}
for s in PERPS:
    b = s[:-4]
    m = re.match(r'^(1000000|10000|1000|1M)([A-Z0-9]+)$', b)
    if m:
        BASES.setdefault(m.group(2), (s, {'1000000': 1e6, '10000': 1e4, '1000': 1e3, '1M': 1e6}[m.group(1)]))
    BASES[b] = (s, 1.0)
STOPWORDS = {'USDT', 'USD', 'TP', 'SL', 'LONG', 'SHORT', 'ENTRY', 'VIP', 'TA', 'BE', 'ROE', 'PNL', 'X', 'AND', 'THE', 'FOR',
             'NOW', 'BUY', 'SELL', 'TARGET', 'STOP', 'LOSS', 'ON', 'IN', 'TO', 'OF', 'AT', 'IS', 'IT', 'A', 'I', 'MY', 'UP',
             'ALL', 'NEW', 'ONE', 'TOP', 'CAT', 'DOG', 'GAS', 'AI', 'HIGH', 'LOW', 'OPEN', 'MAX', 'ETF', 'CEO', 'RSI', 'EMA',
             'MA', 'OB', 'FVG', 'BOS', 'ATH', 'DCA', 'ID', 'GO', 'WIN', 'TRUE', 'REAL', 'PEOPLE', 'SUN', 'ONE', 'MOVE', 'PUMP'}

# ------------------------------------------------------------------ разбор сигнала
KW_ENTRY = r'(entry\s*zone|entries|entry|enter|buy\s*between|buy\s*zone|buy\s*now\s*or\s*buy\s*on|sell\s*now\s*or\s*sell\s*on|buy\s*on|sell\s*on|' \
           r'цена\s*входа|точка\s*входа|зона\s*входа|вход|open\s*price|ордер|limit|лимит|маркет|market|по\s*рынку)'
KW_STOP = r'(stop[\s\-]*loss|stoploss|стоп[\s\-]*лосс|стоп|stop|\bsl\b|invalidation)'
KW_TP = r'(take[\s\-]*profit|тейк[\s\-]*профит|targets?|тейки|тейк|цели|цель|\btp\d*\b|\btgt\b|🎯)'
LONG = r'\blong\b|лонг|\bbuy\b|покупк|купил|📈|🟢|⬆'
SHORT = r'\bshort\b|шорт|\bsell\b|продаж|продал|📉|🔴|⬇'


def to_num(tok):
    t = tok
    if ',' in t and '.' in t:
        t = t.replace(',', '')
    elif t.count(',') == 1:
        t = t.replace(',', '.')
    elif t.count(',') > 1:
        t = t.replace(',', '')
    try:
        return float(t)
    except ValueError:
        return None


def numbers(seg):
    seg = re.sub(r'(?i)\b\d+\s*[-–]?\s*(й|ый|ой|st|nd|rd|th)\b', ' ', seg)           # «1й тейк», «2nd»
    seg = re.sub(r'(?i)(tp|target|тейк|цель|entry|вход)\s*\d\s*[:.)\-–]?', r'\1 ', seg)   # «TP1:», «Target 2»
    seg = re.sub(r'(?i)[xх×]\s*\d+|\d+\s*[xх×]|\d+\s*-\s*\d+\s*[xх×]', ' ', seg)       # плечо
    seg = re.sub(r'\d+(?:[.,]\d+)?\s*%', ' ', seg)                                       # проценты
    seg = re.sub(r'\b\d{1,2}:\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b', ' ', seg)            # время / даты
    vals = [to_num(x) for x in re.findall(r'\d+(?:[.,]\d+)*', seg)]
    return [v for v in vals if v is not None and v > 0]


def find_symbol(text):
    up = text.upper()
    pats = [r'([A-Z0-9]{2,15})\s*/?\s*-?\s*USDT\b', r'[#$]([A-Z0-9]{2,15})\b', r'\b([A-Z][A-Z0-9]{1,14})\b']
    for p in pats:
        for t in re.findall(p, up):
            t = t.replace('1000', '') if t not in BASES and t.startswith('1000') else t
            if t in BASES and t not in STOPWORDS:
                return t
    return None


def parse_signal(text):
    if not text or len(text) > 2500:
        return None
    low = text.lower()
    sym = find_symbol(text)
    if not sym:
        return None
    ml, ms = re.search(LONG, low), re.search(SHORT, low)
    if not ml and not ms:
        return None
    side = 1 if ml and (not ms or ml.start() < ms.start()) else -1
    # раскладка текста по ключевым словам
    marks = []
    for kind, kw in (('e', KW_ENTRY), ('s', KW_STOP), ('t', KW_TP)):
        for m in re.finditer(kw, low):
            marks.append((m.start(), m.end(), kind))
    marks.sort()
    if not any(k == 's' for _, _, k in marks) or not any(k == 't' for _, _, k in marks):
        return None
    seg = defaultdict(list); market = bool(re.search(r'маркет|market|по\s*рынку|buy\s*now|sell\s*now', low))
    for k, (a, b, kind) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(low)
        if kind == 't' and k + 1 < len(marks) and marks[k + 1][2] == 't':
            end = marks[k + 1][0]
        nums = numbers(text[b:end])
        if kind == 's':
            nums = nums[:1]
        if kind == 'e':
            nums = nums[:2]
        seg[kind] += nums
    stop = seg['s'][0] if seg['s'] else None
    tps = []
    for v in seg['t']:
        if v not in tps:
            tps.append(v)
    entry = seg['e'][:2]
    if not stop or not tps:
        return None
    return {'sym': sym, 'side': side, 'entry': entry, 'stop': stop, 'tps': tps[:6], 'market': market or not entry}


# ------------------------------------------------------------------ свечи
def months_between(t0, t1):
    a = datetime.fromtimestamp(t0 / 1000, timezone.utc); b = datetime.fromtimestamp(t1 / 1000, timezone.utc)
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield f'{y:04d}-{m:02d}'
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _zip(url):
    try:
        data = urllib.request.urlopen(url, timeout=60).read()
    except Exception:
        return None
    try:
        chk = urllib.request.urlopen(url + '.CHECKSUM', timeout=60).read().decode().split()[0]
        if hashlib.sha256(data).hexdigest() != chk:
            return None
    except Exception:
        pass
    z = zipfile.ZipFile(io.BytesIO(data))
    return z.read(z.namelist()[0]).decode()


def rows_from(txt):
    out = []
    for line in (txt or '').splitlines():
        q = line.split(',')
        if q and q[0].strip().isdigit():
            out.append((int(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4])))
    return out


def month_file(sym, mo):
    fn = os.path.join(KL, f'{sym}_{mo}.npy')
    if os.path.exists(fn):
        return fn
    rows = rows_from(_zip(f'{VISION}/monthly/klines/{sym}/15m/{sym}-15m-{mo}.zip'))
    if not rows and mo >= DATA_END[:7]:
        y, m = map(int, mo.split('-'))
        for d in range(1, 32):
            day = f'{mo}-{d:02d}'
            if day > DATA_END:
                break
            rows += rows_from(_zip(f'{VISION}/daily/klines/{sym}/15m/{sym}-15m-{day}.zip'))
    np.save(fn, np.array(rows if rows else np.zeros((0, 5)), dtype=float))
    return fn


_K = {}


def bars(sym, t0, t1):
    key = (sym, t0 // (86400_000 * 20))
    arrs = [np.load(month_file(sym, mo)) for mo in months_between(t0, t1)]
    arrs = [a for a in arrs if len(a)]
    if not arrs:
        return None
    a = np.concatenate(arrs)
    a = a[(a[:, 0] >= t0) & (a[:, 0] < t1)]
    return a if len(a) else None


# ------------------------------------------------------------------ сделка
def sim(sig, t_post):
    sym, factor = BASES[sig['sym']]
    a = bars(sym, t_post, t_post + 16 * 86400_000)
    if a is None or len(a) < 10:
        return {'res': 'нет свечей'}
    s = sig['side']
    p0 = a[0, 1]
    lv = [x for x in [*sig['entry'], sig['stop'], *sig['tps']]]
    best = None
    raw = float(np.mean(sig['entry'])) if sig['entry'] else (sig['stop'] + sig['tps'][0]) / 2
    for f in (factor, 1.0, 1000.0, 0.001, factor * 1000, factor / 1000):        # «4,630» и 1000PEPE — подбираем масштаб по рынку
        ref = raw * f
        d = abs(ref / p0 - 1)
        if best is None or d < best[0]:
            best = (d, f)
    if best[0] > 0.25:
        return {'res': 'уровни не сходятся с ценой'}
    f = best[1]
    stop = sig['stop'] * f; tps = [x * f for x in sig['tps']]
    ent = [x * f for x in sig['entry']]
    tps = [x for x in tps if s * (x - stop) > 0]
    if not tps:
        return {'res': 'тейки по ту сторону стопа'}
    out = {}
    for mode in ('M', 'L'):
        if mode == 'M' or sig['market'] or not ent:
            i0, e, cin = 0, p0, 0.0008
            if mode == 'L' and (sig['market'] or not ent):
                out['L'] = out.get('M'); continue
        else:
            L = float(np.mean(ent)); i0 = None
            for k in range(min(len(a), 48 * 4)):
                if (s == 1 and a[k, 2] >= tps[0]) or (s == -1 and a[k, 3] <= tps[0]):
                    if not ((s == 1 and a[k, 3] <= L) or (s == -1 and a[k, 2] >= L)):
                        break
                if (s == 1 and a[k, 3] <= L) or (s == -1 and a[k, 2] >= L):
                    i0 = k; e = min(a[k, 1], L) if s == 1 else max(a[k, 1], L); break
            cin = 0.0002
            if i0 is None:
                out['L'] = {'res': 'лимит не исполнился'}; continue
        if s * (e - stop) <= 0:
            out[mode] = {'res': 'вход за стопом'}; continue
        tp_ok = [x for x in tps if s * (x - e) > 0]
        if not tp_ok:
            out[mode] = {'res': 'цена уже за тейком'}; continue
        risk = abs(e - stop)
        r = {}
        for ex in ('TP1', 'LAD'):
            targets = tp_ok[:1] if ex == 'TP1' else tp_ok[:4]
            part = 1 / len(targets); left = 1.0; st = stop; pnl = 0.0; hit = 0; how = 'time'
            end = min(len(a), i0 + 14 * 96)
            for k in range(i0, end):
                o, h, l, c = a[k, 1:5]
                if (s == 1 and l <= st) or (s == -1 and h >= st):
                    px = (min(o, st) if s == 1 else max(o, st)) if k > i0 else st
                    pnl += left * (s * (px - e) - 0.0008 * px); left = 0; how = 'stop' if hit == 0 else 'stop после тейка'; break
                while hit < len(targets) and ((s == 1 and h >= targets[hit]) or (s == -1 and l <= targets[hit])):
                    px = targets[hit]; pnl += part * (s * (px - e) - 0.0002 * px); left -= part; hit += 1
                    if ex == 'LAD' and hit == 1:
                        st = e
                if left <= 1e-9:
                    how = 'все тейки'; break
            if left > 1e-9:
                px = a[end - 1, 4]; pnl += left * (s * (px - e) - 0.0008 * px)
            pnl -= cin * e
            r[ex] = {'R': pnl / risk, 'how': how, 'stop_pct': risk / e * 100}
        out[mode] = {'res': 'ok', 'e': e, 'r': r, 't_in': int(a[i0, 0])}
    return out


# ------------------------------------------------------------------ отчёт
def stats(R):
    if not R:
        return {}
    R = np.array(R); w = R[R > 0].sum(); l = -R[R <= 0].sum()
    streak = cur = 0
    for x in R:
        cur = cur + 1 if x <= 0 else 0; streak = max(streak, cur)
    eq = {}
    for risk in (0.01, 0.02, 0.05):
        e = 1.0
        for x in R:
            e *= max(0.0, 1 + risk * x)
        eq[risk] = e
    return {'n': len(R), 'wr': (R > 0).mean() * 100, 'avg': R.mean(), 'pf': w / l if l else 99, 'sum': R.sum(),
            'no5': np.sort(R)[:-5].sum() if len(R) > 5 else 0, 'streak': streak, 'eq': eq}


def main():
    chans = []
    for fn in sorted(glob.glob(os.path.join(RAW, '*.json'))):
        d = json.load(open(fn))
        if not d.get('ok'):
            chans.append({'ch': d['channel'], 'ok': False, 'why': d.get('why')}); continue
        msgs = [m for m in d['messages'] if m.get('date')]
        sigs, seen = [], {}
        for m in msgs:
            sg = parse_signal(m['text'])
            if not sg:
                continue
            t = int(datetime.fromisoformat(m['date']).timestamp() * 1000)
            if t > int(datetime.fromisoformat(DATA_END + 'T00:00:00+00:00').timestamp() * 1000) - 15 * 86400_000:
                continue
            k = (sg['sym'], sg['side'], round(sg['stop'], 8))
            if k in seen and t - seen[k] < 12 * 3600_000:
                continue
            seen[k] = t
            sg.update(t=t, id=m['id'], fwd=m.get('fwd'), text=m['text'][:300]); sigs.append(sg)
        ids = [m['id'] for m in d['messages']]
        gap = 1 - len(ids) / (max(ids) - min(ids) + 1) if ids else 0
        chans.append({'ch': d['channel'], 'title': d.get('title', ''), 'ok': True, 'msgs': len(msgs), 'sigs': sigs, 'gap': gap,
                      'from': msgs[0]['date'][:10] if msgs else '', 'to': msgs[-1]['date'][:10] if msgs else ''})
    jobs = [(c, s) for c in chans if c['ok'] for s in c['sigs']]
    print('сигналов к проверке', len(jobs), flush=True)
    need = {(BASES[s['sym']][0], mo) for _, s in jobs for mo in months_between(s['t'], s['t'] + 16 * 86400_000)}
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda x: month_file(*x), sorted(need)))
    for c, s in jobs:
        s['bt'] = sim(s, s['t'])
    rows, detail = [], []
    for c in chans:
        if not c['ok']:
            rows.append((-1e9, f"| @{c['ch']} | — | {c['why']} |||||||||")); continue
        sg = c['sigs']
        res = defaultdict(int)
        for s in sg:
            m = s['bt'].get('M') if 'M' in s['bt'] else None
            res[s['bt'].get('res') or (m or {}).get('res', '?')] += 1
        R = {(mo, ex): [s['bt'][mo]['r'][ex]['R'] for s in sg if (s['bt'].get(mo) or {}).get('res') == 'ok'] for mo in 'ML' for ex in ('TP1', 'LAD')}
        st = {k: stats(v) for k, v in R.items()}
        months = max(1, (datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])).days / 30.4) if c['from'] else 1
        best = st[('M', 'LAD')]
        key = best.get('avg', -9) if best.get('n', 0) >= 20 else -5 + best.get('n', 0) / 100
        f = lambda x: (f"{x['n']} · {x['wr']:.0f}% · {x['avg']:+.2f}R · PF {x['pf']:.2f}" if x else '—')
        rows.append((key, f"| @{c['ch']} | {c['title'][:28]} | {c['from']}…{c['to']} | {c['msgs']} | {len(sg)} ({len(sg) / months:.1f}/мес) | "
                          f"{c['gap'] * 100:.0f}% | {f(st[('M', 'TP1')])} | {f(best)} | {f(st[('L', 'LAD')])} | "
                          f"{best.get('streak', '—')} | {('×%.2f' % best['eq'][0.02]) if best else '—'} |"))
        detail.append({'ch': c['ch'], 'title': c['title'], 'res': dict(res), 'stats': {f'{a}_{b}': {k: (v if not isinstance(v, dict) else {str(kk): vv for kk, vv in v.items()}) for k, v in x.items()} for (a, b), x in st.items()},
                       'examples': [{k: s[k] for k in ('sym', 'side', 'entry', 'stop', 'tps', 'text')} | {'date': datetime.fromtimestamp(s['t'] / 1000, timezone.utc).isoformat()[:16],
                                     'M': s['bt'].get('M')} for s in sg[-6:]]})
    rows.sort(key=lambda x: -x[0])
    L = ['# Скрининг Telegram-каналов с сигналами', '',
         'Колонки сделок: «сделок · доля прибыльных · средний R · PF». M = вход по рынку сразу после поста, L = лимитом по зоне входа (48 ч).',
         'TP1 = вся позиция на первом тейке; лестница = равные части на тейки, после TP1 стоп в безубыток. Пропуски = доля номеров постов, которых нет в ленте (удалённые/служебные).', '',
         '| канал | название | период | постов | сигналов | пропуски | M · TP1 | M · лестница | L · лестница | худшая серия | капитал при 2% риска |',
         '|---|---|---|---|---|---|---|---|---|---|---|'] + [r for _, r in rows]
    open(os.path.join(OUT, 'REPORT.md'), 'w').write('\n'.join(L) + '\n')
    json.dump(detail, open(os.path.join(OUT, 'detail.json'), 'w'), ensure_ascii=False, default=float, indent=1)
    print('\n'.join(L))


if __name__ == '__main__':
    main()
