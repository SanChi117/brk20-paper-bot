#!/usr/bin/env python3
"""Новые листинги USDT-M Binance: шорт/лонг после листинга + автоматические фильтры «анализа проекта».
python listings/study.py all_data listings_out
"""
import csv, glob, json, os, re, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DATA = sys.argv[1] if len(sys.argv) > 1 else 'all_data'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'listings_out'
os.makedirs(OUT, exist_ok=True)
DAY = 86400000
ARCH0 = datetime(2020, 9, 6, tzinfo=timezone.utc).timestamp() * 1000
HOLD0 = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000
COST = 0.0015
MEMES = set('DOGE SHIB PEPE FLOKI BONK WIF BOME MEW POPCAT BRETT NEIRO NEIROETH MOODENG GOAT PNUT ACT CHILLGUY TRUMP '
            'MELANIA FARTCOIN PENGU MOG SPX TURBO MEME PEOPLE BABYDOGE DOGS HIPPO BAN SATS RATS LADYS MYRO SLERF '
            'MEMEFI PONKE DEGEN AIDOGE COQ WEN MUMU BUTTHOLE GRIFFAIN ZEREBRO AI16Z PIPPIN BROCCOLI714 TUT MUBARAK '
            'BANANAS31 KOMA PUMP USELESS PEPE2 TOSHI SKYAI DOOD WHY CHEEMS MILK BIO LUCE'.split())


def base(sym):
    b = sym[:-4]
    return re.sub(r'^(1000000|10000|1000|1M)(?=[A-Z])', '', b)


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def load(sym):
    k = []
    with open(f'{DATA}/{sym}_1d.csv') as f:
        for r in csv.DictReader(f):
            k.append((int(r['t']), float(r['o']), float(r['h']), float(r['l']), float(r['c']), float(r['qv'])))
    fr = []
    p = f'{DATA}/{sym}_funding.csv'
    if os.path.exists(p):
        with open(p) as f:
            fr = [(int(r['t']), float(r['rate'])) for r in csv.DictReader(f)]
    return k, fr


def meta(syms):
    p = f'{OUT}/meta.json'
    if os.path.exists(p):
        return json.load(open(p))
    m = {'spot0': {}, 'cg_meme': []}
    for s in syms:
        b = base(s)
        for cand in {b + 'USDT', s}:
            try:
                x = urllib.request.urlopen('https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix='
                                           f'data/spot/monthly/klines/{cand}/1d/', timeout=60).read().decode()
            except Exception:
                continue
            ms = re.findall(r'-1d-(\d{4}-\d{2})\.zip<', x)
            if ms:
                m['spot0'][s] = min(ms); break
    try:
        for page in range(1, 6):
            req = urllib.request.Request('https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=meme-token'
                                         f'&per_page=250&page={page}', headers={'User-Agent': 'Mozilla/5.0'})
            js = json.load(urllib.request.urlopen(req, timeout=60))
            if not js:
                break
            m['cg_meme'] += [c['symbol'].upper() for c in js]
    except Exception as e:
        print('coingecko fail', e)
    json.dump(m, open(p, 'w'))
    return m


def trade(k, fr, side, E, H, stop):
    """side: -1 шорт / +1 лонг. Вход open дня E, выход close дня E+H-1, стоп stop (доля)."""
    if len(k) <= E:
        return None
    t0, ent = k[E][0], k[E][1]
    sp = ent * (1 - side * stop)
    ex, last, how = None, min(E + H - 1, len(k) - 1), 'time'
    for i in range(E, last + 1):
        _, o, h, l, c, _ = k[i]
        if side == -1 and h >= sp:
            ex, how = max(o, sp) if i > E else sp, 'stop'; last = i; break
        if side == 1 and l <= sp:
            ex, how = min(o, sp) if i > E else sp, 'stop'; last = i; break
    if ex is None:
        ex = k[last][4]
        if last < E + H - 1:
            how = 'delist'
    t1 = k[last][0] + DAY
    fund = sum(r for t, r in fr if t0 <= t < t1)
    r = side * (ex - ent) / ent - 2 * COST - side * fund
    return dict(t0=t0, t1=t1, r=r, R=r / stop, how=how)


def stats(tr):
    if not tr:
        return dict(n=0)
    R = [x['R'] for x in tr]
    w = [x for x in R if x > 0]; l = [x for x in R if x <= 0]
    pf = sum(w) / -sum(l) if l and sum(l) < 0 else 99
    top5 = sum(sorted(R)[:-5]) if len(R) > 5 else 0
    yrs = defaultdict(float)
    for x in tr:
        yrs[ts(x['t0']).year] += x['R']
    rr = sorted(x['r'] for x in tr)
    return dict(n=len(R), wr=round(len(w) / len(R) * 100), pf=round(pf, 2), sumR=round(sum(R), 1), noTop5=round(top5, 1),
                med_ret=round(rr[len(rr) // 2] * 100, 1), years={y: round(v, 1) for y, v in sorted(yrs.items())})


def equity(tr, risk):
    ev = sorted(tr, key=lambda x: x['t0'])
    eq, open_, peak, dd = 1.0, [], 1.0, 0
    for x in ev:
        for o in [o for o in open_ if o['t1'] <= x['t0']]:
            eq += o['size'] * o['R']; open_.remove(o)
        peak = max(peak, eq); dd = max(dd, 1 - eq / peak)
        if eq <= 0.01:
            return 0, 1
        x = dict(x); x['size'] = eq * risk; open_.append(x)
    for o in open_:
        eq += o['size'] * o['R']
    return round(eq, 2), round(max(dd, 1 - eq / max(peak, eq)), 2)


def main():
    syms = sorted(os.path.basename(p)[:-7] for p in glob.glob(f'{DATA}/*_1d.csv'))
    L = []
    for s in syms:
        k, fr = load(s)
        if len(k) < 3 or k[0][0] < ARCH0:
            continue
        L.append((s, k, fr))
    print('listings', len(L))
    M = meta([s for s, _, _ in L])
    meme = MEMES | set(M['cg_meme'])
    feats = {}
    for s, k, fr in L:
        t0 = k[0][0]; mo = ts(t0).strftime('%Y-%m')
        sp = M['spot0'].get(s)
        f1 = sum(r for t, r in fr if t0 <= t < t0 + 2 * DAY)
        feats[s] = dict(new=(sp is None or sp >= mo), meme=base(s) in meme, d0=k[0][4] / k[0][1] - 1, qv0=k[0][5], f1=f1,
                        hold=t0 >= HOLD0)
    rep = ['# Листинги USDT-M Binance — результаты', '', f'Листингов в выборке: {len(L)} '
           f'(dev {sum(not f["hold"] for f in feats.values())}, holdout {sum(f["hold"] for f in feats.values())}); '
           f'новых токенов {sum(f["new"] for f in feats.values())}, мемов {sum(f["meme"] for f in feats.values())}', '']

    def run(side, E, H, stop, flt=lambda f: True, hold=False):
        out = []
        for s, k, fr in L:
            f = feats[s]
            if f['hold'] != hold or not flt(f):
                continue
            x = trade(k, fr, side, E, H, stop)
            if x:
                x['s'] = s; out.append(x)
        return out

    def line(name, tr):
        st = stats(tr)
        if not st['n']:
            return f'| {name} | 0 |||||||'
        e1, d1 = equity(tr, 0.01); e2, d2 = equity(tr, 0.02)
        return (f"| {name} | {st['n']} | {st['wr']}% | {st['pf']} | {st['sumR']} | {st['noTop5']} | {st['med_ret']}% | "
                f"{' '.join(f'{y % 100}:{v}' for y, v in st['years'].items())} | ×{e1} dd{int(d1 * 100)}% / ×{e2} dd{int(d2 * 100)}% |")

    H = '| вариант | n | WR | PF | ΣR | без топ-5 | медиана сделки | R по годам | капитал 1% / 2% риска |\n|---|---|---|---|---|---|---|---|---|'
    rep += ['## Основной: шорт, вход день 1, 14 дней, стоп 40%', H, line('dev', run(-1, 1, 14, 0.4)),
            line('holdout', run(-1, 1, 14, 0.4, hold=True)), '']
    rep += ['## Сетка (dev, разведка)', H]
    for side in (-1, 1):
        for E in (1, 3, 7):
            for Hd in (7, 14, 30):
                for st in (0.2, 0.4, 1.0):
                    rep.append(line(f"{'шорт' if side < 0 else 'лонг'} d{E} {Hd}д стоп{int(st * 100)}%", run(side, E, Hd, st)))
    rep += ['', '## «Анализ проекта»: фильтры к основному шорту (dev / holdout)', H]
    q = sorted(f['d0'] for f in feats.values()); hi, lo = q[len(q) * 2 // 3], q[len(q) // 3]
    qv = sorted(f['qv0'] for f in feats.values()); qvm = qv[len(qv) // 2]
    F = [('новый токен', lambda f: f['new']), ('старый токен (спот был)', lambda f: not f['new']),
         ('мем', lambda f: f['meme']), ('не мем', lambda f: not f['meme']),
         ('день0 сильный рост (верх 1/3)', lambda f: f['d0'] >= hi), ('день0 падение (низ 1/3)', lambda f: f['d0'] <= lo),
         ('оборот дня0 выше медианы', lambda f: f['qv0'] >= qvm), ('оборот дня0 ниже медианы', lambda f: f['qv0'] < qvm),
         ('фандинг 2 суток > 0.05%', lambda f: f['f1'] > 0.0005), ('фандинг 2 суток < 0', lambda f: f['f1'] < 0),
         ('новый мем', lambda f: f['new'] and f['meme']), ('новый не мем', lambda f: f['new'] and not f['meme'])]
    for name, fl in F:
        rep.append(line(name + ' / dev', run(-1, 1, 14, 0.4, fl)))
        rep.append(line(name + ' / holdout', run(-1, 1, 14, 0.4, fl, hold=True)))
    rep += ['', '## Мемы в лонг (dev / holdout), вход день 1', H]
    for Hd in (7, 14, 30):
        rep.append(line(f'лонг мем {Hd}д стоп40%', run(1, 1, Hd, 0.4, lambda f: f['meme'])))
        rep.append(line(f'лонг мем {Hd}д стоп40% holdout', run(1, 1, Hd, 0.4, lambda f: f['meme'], hold=True)))
    open(f'{OUT}/REPORT.md', 'w').write('\n'.join(rep) + '\n')
    print('\n'.join(rep))
    with open(f'{OUT}/listings.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['sym', 'date', 'new', 'meme', 'd0', 'qv0', 'f1', 'holdout'])
        for s, k, _ in L:
            x = feats[s]
            w.writerow([s, ts(k[0][0]).date(), int(x['new']), int(x['meme']), round(x['d0'], 4), round(x['qv0']), x['f1'], int(x['hold'])])


if __name__ == '__main__':
    main()
