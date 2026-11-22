#!/usr/bin/env python3
"""SMC MTF model backtest (см. smc/PREREG.md). Без заглядывания вперёд: всё, что известно о баре ТФ,
используется только после его закрытия; исполнение — по 15m барам."""
import bisect, csv, glob, heapq, math, os, sys, datetime as dt
from concurrent.futures import ProcessPoolExecutor
from zoneinfo import ZoneInfo
import numpy as np

M15 = 900_000
TF = {'15m': M15, '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000}
HOLD = int(dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
KYIV = ZoneInfo('Europe/Kyiv')
HORIZON = {M15: 192, 3_600_000: 720, 14_400_000: 360, 86_400_000: 120}
FEE_MAKER, FEE_TAKER, SLIP = 0.0002, 0.0005, 0.0005


# ---------------------------------------------------------------- data
def load(path):
    a = np.loadtxt(path, delimiter=',', skiprows=1, usecols=(0, 1, 2, 3, 4))
    a = a[np.argsort(a[:, 0])]
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4]


def agg(t, o, h, l, c, P):
    g = t // P * P
    keys, start, cnt = np.unique(g, return_index=True, return_counts=True)
    full = cnt == P // M15
    end = start + cnt - 1
    O = o[start]; C = c[end]
    H = np.maximum.reduceat(h, start); L = np.minimum.reduceat(l, start)
    return keys[full], O[full], H[full], L[full], C[full]


class Bars:
    def __init__(self, t, o, h, l, c, P):
        self.t, self.o, self.h, self.l, self.c, self.P = t, o, h, l, c, P
        self.tc = t + P  # момент закрытия = момент, когда информация доступна
        n = len(t)
        tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
        cs = np.cumsum(tr)
        self.atr = np.where(np.arange(n) >= 14, (cs - np.r_[np.zeros(14), cs[:-14]]) / 14, tr)
        self.structure()
        self.zones()

    # -------- фракталы, свипы, рейды, BOS-тренд
    def structure(self):
        h, l, c, tc = self.h, self.l, self.c, self.tc
        n = len(h)
        self.fh, self.fl = [], []          # (price, k, t_conf, t_swept)
        self.raids = {1: [], -1: []}       # side -> list (t_known, level, extreme, j)
        self.trend = np.zeros(n, np.int8); self.pda_mid = np.full(n, np.nan)
        hh, hl = [], []                    # heaps of unswept: (price,idx) / (-price,idx)
        lastfh = lastfl = None             # (price, k)
        tr, strong, ext = 0, np.nan, np.nan
        for j in range(n):
            # свипы фракталов баром j
            while hh and hh[0][0] < h[j]:
                p, i = heapq.heappop(hh); self.fh[i][3] = tc[j]
                if c[j] < p:
                    self.raids[-1].append((tc[j], p, h[j], j))
            while hl and -hl[0][0] > l[j]:
                p, i = heapq.heappop(hl); p = -p; self.fl[i][3] = tc[j]
                if c[j] > p:
                    self.raids[1].append((tc[j], p, l[j], j))
            # BOS
            if lastfh is not None and c[j] > lastfh[0]:
                tr = 1; strong = l[lastfh[1]:j + 1].min(); ext = h[j]; lastfh = None
            elif lastfl is not None and c[j] < lastfl[0]:
                tr = -1; strong = h[lastfl[1]:j + 1].max(); ext = l[j]; lastfl = None
            if tr == 1:
                ext = max(ext, h[j])
            elif tr == -1:
                ext = min(ext, l[j])
            self.trend[j] = tr
            if tr:
                self.pda_mid[j] = (strong + ext) / 2
            # фрактал на j-1 подтверждается закрытием j
            k = j - 1
            if k >= 1:
                if h[k] > h[k - 1] and h[k] > h[j]:
                    self.fh.append([h[k], k, tc[j], None]); heapq.heappush(hh, (h[k], len(self.fh) - 1)); lastfh = (h[k], k)
                if l[k] < l[k - 1] and l[k] < l[j]:
                    self.fl.append([l[k], k, tc[j], None]); heapq.heappush(hl, (-l[k], len(self.fl) - 1)); lastfl = (l[k], k)
        self.fh_t = [f[2] for f in self.fh]; self.fl_t = [f[2] for f in self.fl]
        self.raid_t = {s: [r[0] for r in self.raids[s]] for s in (1, -1)}
        self.raid_bar = {s: {r[3] for r in self.raids[s]} for s in (1, -1)}
        self.sweep_bar = {1: set(), -1: set()}  # бар, на котором снята ликвидность (тенью или телом)
        for f in self.fl:
            if f[3] is not None:
                self.sweep_bar[1].add(int(np.searchsorted(tc, f[3])))
        for f in self.fh:
            if f[3] is not None:
                self.sweep_bar[-1].add(int(np.searchsorted(tc, f[3])))

    # -------- POI зоны
    def zones(self):
        o, h, l, c, tc, atr = self.o, self.h, self.l, self.c, self.tc, self.atr
        n = len(h); Z = []
        for j in range(2, n):
            a = atr[j]
            # FVG / IMB
            if l[j] > h[j - 2] and l[j] - h[j - 2] >= 0.1 * a:
                liq = bool({j - 2, j - 1, j} & self.sweep_bar[1])
                Z.append((1, 'FVG', h[j - 2], l[j], j, liq))
            if h[j] < l[j - 2] and l[j - 2] - h[j] >= 0.1 * a:
                liq = bool({j - 2, j - 1, j} & self.sweep_bar[-1])
                Z.append((-1, 'FVG', h[j], l[j - 2], j, liq))
            i = j - 1
            # пара sell+buy (bull) / buy+sell (bear)
            if c[i] < o[i] and c[j] > o[j]:
                lo = min(l[i], l[j])
                if {i, j} & self.raid_bar[1]:
                    top = min(min(o[i], c[i]), min(o[j], c[j]))
                    if top > lo:
                        Z.append((1, 'RB', lo, top, j, True))
                elif {i, j} & self.sweep_bar[1] and c[j] > o[i]:
                    Z.append((1, 'OB', lo, o[i], j, True))
            if c[i] > o[i] and c[j] < o[j]:
                hi = max(h[i], h[j])
                if {i, j} & self.raid_bar[-1]:
                    bot = max(max(o[i], c[i]), max(o[j], c[j]))
                    if hi > bot:
                        Z.append((-1, 'RB', bot, hi, j, True))
                elif {i, j} & self.sweep_bar[-1] and c[j] < o[i]:
                    Z.append((-1, 'OB', o[i], hi, j, True))
        # инвалидация закрытием на своём ТФ за дальней границей
        self.Z = []
        Hn = HORIZON[self.P]
        for s, typ, lo, hi, j, liq in Z:
            e = min(n, j + 1 + Hn); dead = tc[e - 1]   # за горизонтом зона считается отработанной
            seg = c[j + 1:e]
            bad = np.nonzero(seg < lo if s == 1 else seg > hi)[0]
            inv = tc[j + 1 + bad[0]] if len(bad) else dead
            # первое касание на этом ТФ (для FTA встречных зон)
            touch = np.nonzero(l[j + 1:e] <= hi if s == 1 else h[j + 1:e] >= lo)[0]
            ttf = tc[j + 1 + touch[0]] - self.P if len(touch) else dead  # начало бара касания
            self.Z.append(dict(s=s, typ=typ, lo=lo, hi=hi, t=tc[j], inv=inv, touch_tf=ttf, liq=liq, atr=self.atr[j]))
        self.Zt = {s: [z['t'] for z in self.Z if z['s'] == s] for s in (1, -1)}
        self.Zs = {s: [z for z in self.Z if z['s'] == s] for s in (1, -1)}

    def idx_at(self, T):
        """индекс последнего бара, закрытого не позже T"""
        return int(np.searchsorted(self.tc, T, side='right')) - 1

    def fta(self, s, T, entry):
        """ближайшая проблемная зона по направлению: неснятый фрактал или непротестированная встречная зона"""
        best = math.inf if s == 1 else -math.inf
        F = self.fh if s == 1 else self.fl
        ft = self.fh_t if s == 1 else self.fl_t
        b = bisect.bisect_right(ft, T)
        for f in F[max(0, b - 400):b]:
            if f[2] <= T and (f[3] is None or f[3] > T):
                p = f[0]
                if (s == 1 and entry < p < best) or (s == -1 and best < p < entry):
                    best = p
        zt = self.Zt[-s]; b = bisect.bisect_right(zt, T)
        for z in self.Zs[-s][max(0, b - 600):b]:
            if z['t'] <= T and z['touch_tf'] > T and z['inv'] > T:
                p = z['lo'] if s == 1 else z['hi']
                if (s == 1 and entry < p < best) or (s == -1 and best < p < entry):
                    best = p
        return None if math.isinf(best) else best


# ---------------------------------------------------------------- 15m utilities
class Exec:
    def __init__(self, t, o, h, l, c):
        self.t, self.o, self.h, self.l, self.c = t, o, h, l, c
        self.tc = t + M15
        hrs = np.array([dt.datetime.fromtimestamp(x / 1000, KYIV).hour for x in t])
        self.sess = (hrs >= 9) & (hrs < 17)

    def i_after(self, T):  # первый 15m бар, открывающийся не раньше T
        return int(np.searchsorted(self.t, T, side='left'))

    def first_touch(self, s, lo, hi, T0, T1):
        a, b = self.i_after(T0), self.i_after(T1)
        if a >= b:
            return None
        hit = np.nonzero(self.l[a:b] <= hi if s == 1 else self.h[a:b] >= lo)[0]
        return a + hit[0] if len(hit) else None

    def extreme(self, s, T0, T1):
        a, b = self.i_after(T0), self.i_after(T1)
        if a >= b:
            return None
        return self.l[a:b].min() if s == 1 else self.h[a:b].max()


DEFAULT = dict(daily=True, pda=False, h4trend=False, models='AB', z4=('FVG', 'OB', 'RB'), z1=('FVG', 'OB', 'RB'),
               z15=('FVG', 'OB'), fvg_liq=False, rr=2.0, session=True)
VARIANTS = {
    'base': {},
    'no_daily': dict(daily=False),
    'pda': dict(pda=True),
    'h4trend': dict(h4trend=True),
    'only_A': dict(models='A'),
    'only_B': dict(models='B'),
    'z4_fvg': dict(z4=('FVG',)),
    'z4_ob_rb': dict(z4=('OB', 'RB')),
    'fvg_liq': dict(fvg_liq=True),
    'rr3': dict(rr=3.0),
    'no_session': dict(session=False),
}


def ok_zone(z, types, cfg):
    return z['typ'] in types and (z['typ'] != 'FVG' or not cfg['fvg_liq'] or z['liq'])


def vc15(B15, s, T0, T1, cfg):
    """15m VC (IMB/SNR) по направлению, созданные в (T0,T1]"""
    ts = B15.Zt[s]; zs = B15.Zs[s]
    a = bisect.bisect_right(ts, T0); b = bisect.bisect_right(ts, T1)
    for z in zs[a:b]:
        if ok_zone(z, cfg['z15'], cfg):
            yield z


def simulate(sym, D, B4, B1, B15, E, cfg):
    trades = []
    busy_until = 0
    cands = []
    for s in (1, -1):
        for Z in B4.Zs[s]:
            if not ok_zone(Z, cfg['z4'], cfg):
                continue
            end_life = min(Z['inv'], Z['t'] + 30 * TF['1d'])
            it = E.first_touch(s, Z['lo'], Z['hi'], Z['t'], end_life)
            if it is None:
                continue
            T0 = E.t[it]                       # начало 15m бара первого касания
            Tend = min(T0 + 48 * TF['1h'], Z['inv'])
            di = D.idx_at(T0)
            if di < 0:
                continue
            if cfg['daily'] and D.trend[di] != s:
                continue
            if cfg['pda']:
                mid = D.pda_mid[di]
                if not (D.trend[di] == s and not np.isnan(mid) and ((s == 1 and E.o[it] < mid) or (s == -1 and E.o[it] > mid))):
                    continue
            if cfg['h4trend']:
                hi4 = B4.idx_at(T0)
                if hi4 < 0 or B4.trend[hi4] != s:
                    continue
            orders = []
            # ---- модель A: 1H рейд внутри зоны
            if 'A' in cfg['models']:
                rt = B1.raid_t[s]; a = bisect.bisect_right(rt, T0); b = bisect.bisect_right(rt, Tend)
                for r in B1.raids[s][a:b]:
                    if (s == 1 and r[2] <= Z['hi']) or (s == -1 and r[2] >= Z['lo']):
                        T1 = r[0]
                        o = make_order(s, B1, B4, B15, E, T0, T1, min(T1 + 12 * TF['1h'], Tend), 'A', cfg)
                        if o:
                            orders.append(o)
                        break
            # ---- модель B: 1H VC = новая POI → её первый тест → 15m VC
            if 'B' in cfg['models']:
                zt = B1.Zt[s]; a = bisect.bisect_right(zt, T0); b = bisect.bisect_right(zt, Tend)
                for z1 in B1.Zs[s][a:b]:
                    if not ok_zone(z1, cfg['z1'], cfg):
                        continue
                    if (s == 1 and z1['lo'] < Z['lo'] - Z['atr']) or (s == -1 and z1['hi'] > Z['hi'] + Z['atr']):
                        continue
                    lim = min(z1['t'] + 24 * TF['1h'], z1['inv'], Z['inv'])
                    i2 = E.first_touch(s, z1['lo'], z1['hi'], z1['t'], lim)
                    if i2 is not None:
                        T2 = E.t[i2]
                        o = make_order(s, B1, B1, B15, E, T2, T2, min(T2 + 8 * TF['1h'], lim), 'B', cfg)
                        if o:
                            orders.append(o)
                    break
            if orders:
                cands.append(min(orders, key=lambda x: x['t_place']))
    cands.sort(key=lambda x: x['t_place'])
    for od in cands:
        if od['t_place'] < busy_until:
            continue
        tr = run_order(E, od, cfg)
        if tr:
            tr['sym'] = sym
            trades.append(tr)
            busy_until = tr['t_exit']
        else:
            busy_until = od['t_dead']
    return trades


def make_order(s, B1, Btgt, B15, E, Tstart, T1, Tlim, model, cfg):
    """ищем 15m VC после T1, ставим лимит на его ближней границе"""
    for z in vc15(B15, s, T1, Tlim, cfg):
        ext = E.extreme(s, Tstart, z['t'])     # Strong Low/High: экстремум с начала теста до появления VC
        if ext is None:
            continue
        if (s == 1 and z['lo'] < ext) or (s == -1 and z['hi'] > ext):
            continue
        a1 = B1.atr[max(B1.idx_at(z['t']), 0)]
        buf = max(0.1 * z['atr'], 0.0005 * z['hi'])
        entry = z['hi'] if s == 1 else z['lo']
        stop = ext - buf if s == 1 else ext + buf
        risk = abs(entry - stop)
        if risk <= 0 or risk > 3 * a1:
            continue
        tp = Btgt.fta(s, z['t'], entry)
        if tp is None:
            continue
        cost = entry * (FEE_MAKER * 2)
        rr = (abs(tp - entry) - cost) / (risk + entry * (FEE_MAKER + FEE_TAKER + SLIP))
        if rr < cfg['rr']:
            continue
        return dict(s=s, entry=entry, stop=stop, tp=tp, t_place=z['t'], t_dead=z['t'] + 4 * TF['1h'],
                    model=model, typ=z['typ'], risk=risk)
    return None


def run_order(E, od, cfg):
    s, entry, stop, tp = od['s'], od['entry'], od['stop'], od['tp']
    i = E.i_after(od['t_place']); iend = E.i_after(od['t_dead'])
    n = len(E.t)
    fill = None
    while i < min(iend, n):
        hi, lo, op = E.h[i], E.l[i], E.o[i]
        if (s == 1 and hi >= tp) or (s == -1 and lo <= tp):
            if not ((s == 1 and lo <= entry) or (s == -1 and hi >= entry)):
                return None                      # тейк раньше входа — отмена
        if (not cfg['session'] or E.sess[i]) and ((s == 1 and lo <= entry) or (s == -1 and hi >= entry)):
            px = min(op, entry) if s == 1 else max(op, entry)
            fill = (i, px); break
        if (s == 1 and lo <= stop) or (s == -1 and hi >= stop):
            return None
        i += 1
    if fill is None:
        return None
    i, px = fill
    risk = abs(px - stop)
    if risk <= 0:
        return None
    # бар исполнения: только стоп
    if (s == 1 and E.l[i] <= stop) or (s == -1 and E.h[i] >= stop):
        return close(od, px, stop, E.tc[i], 'SL', E.t[i], risk)
    lim = E.t[i] + 7 * TF['1d']
    j = i + 1
    while j < n:
        if E.t[j] >= lim:
            return close(od, px, E.o[j], E.t[j], 'TIME', E.t[i], risk)
        if (s == 1 and E.l[j] <= stop) or (s == -1 and E.h[j] >= stop):
            ex = min(E.o[j], stop) if s == 1 else max(E.o[j], stop)
            return close(od, px, ex, E.tc[j], 'SL', E.t[i], risk)
        if (s == 1 and E.h[j] >= tp) or (s == -1 and E.l[j] <= tp):
            return close(od, px, tp, E.tc[j], 'TP', E.t[i], risk)
        j += 1
    return None


def close(od, px, ex, t_exit, why, t_entry, risk):
    s = od['s']
    fee = px * FEE_MAKER + ex * (FEE_MAKER if why == 'TP' else FEE_TAKER + (SLIP if why == 'SL' else 0))
    R = (s * (ex - px) - fee) / risk
    return dict(t_entry=int(t_entry), t_exit=int(t_exit), s=s, R=R, why=why, model=od['model'], typ=od['typ'],
                rr_plan=abs(od['tp'] - px) / risk, risk_pct=risk / px)


# ---------------------------------------------------------------- driver
def prep(path):
    t, o, h, l, c = load(path)
    E = Exec(t, o, h, l, c)
    B15 = Bars(t, o, h, l, c, M15)
    B1 = Bars(*agg(t, o, h, l, c, TF['1h']), TF['1h'])
    B4 = Bars(*agg(t, o, h, l, c, TF['4h']), TF['4h'])
    D = Bars(*agg(t, o, h, l, c, TF['1d']), TF['1d'])
    return D, B4, B1, B15, E


def work(path):
    sym = os.path.basename(path).split('_')[0]
    D, B4, B1, B15, E = prep(path)
    out = {}
    for name, v in VARIANTS.items():
        cfg = dict(DEFAULT); cfg.update(v)
        out[name] = simulate(sym, D, B4, B1, B15, E, cfg)
    print(sym, {k: len(x) for k, x in out.items()}, flush=True)
    return out


def stats(tr):
    if not tr:
        return dict(n=0)
    R = np.array([x['R'] for x in tr])
    w = R[R > 0].sum(); ls = -R[R < 0].sum()
    order = np.argsort([x['t_exit'] for x in tr]); cum = np.cumsum(R[order])
    dd = (np.maximum.accumulate(np.r_[0, cum]) - np.r_[0, cum]).max()
    top5 = np.sort(R)[::-1][:5].sum()
    return dict(n=len(R), wr=(R > 0).mean(), avgR=R.mean(), PF=w / ls if ls else float('inf'), sumR=R.sum(),
                ddR=dd, sum_ex_top5=R.sum() - top5)


def equity(tr, risk=0.01):
    ev = sorted(tr, key=lambda x: x['t_entry'])
    eq = 1.0; peak = 1.0; mdd = 0.0
    openp = []  # (t_exit, stake, R)
    for x in ev:
        openp.sort()
        while openp and openp[0][0] <= x['t_entry']:
            te, st, R = openp.pop(0); eq += st * R; peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
        openp.append((x['t_exit'], eq * risk, x['R']))
    for te, st, R in sorted(openp):
        eq += st * R; peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
    return eq, mdd


def fmt(d):
    if not d.get('n'):
        return '| 0 | – | – | – | – | – | – |'
    return (f"| {d['n']} | {d['wr']*100:.0f}% | {d['avgR']:+.2f} | {d['PF']:.2f} | {d['sumR']:+.0f} | "
            f"{d['ddR']:.0f} | {d['sum_ex_top5']:+.0f} |")


def main():
    data = sys.argv[1]; os.makedirs('smc_out', exist_ok=True)
    files = sorted(glob.glob(f'{data}/*_15m.csv'))
    with ProcessPoolExecutor(os.cpu_count()) as ex:
        res = list(ex.map(work, files))
    allv = {k: [t for r in res for t in r[k]] for k in VARIANTS}
    L = ['# SMC MTF — результаты', '', 'R после издержек. dev = до 2026-06-01, hold = 2026-06…10 (holdout).', '',
         '| вариант | выборка | сделок | WR | avgR | PF | ΣR | maxDD R | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|---|']
    for k, tr in allv.items():
        dev = [x for x in tr if x['t_entry'] < HOLD]; hold = [x for x in tr if x['t_entry'] >= HOLD]
        L.append(f'| {k} | dev ' + fmt(stats(dev)))
        L.append(f'| {k} | hold ' + fmt(stats(hold)))
    base = allv['base']
    L += ['', '## base по годам', '| год | сделок | WR | avgR | PF | ΣR | maxDD R | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    for y in range(2021, 2027):
        a = int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        b = int(dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        L.append(f'| {y} ' + fmt(stats([x for x in base if a <= x['t_entry'] < b])))
    L += ['', '## base по звеньям (dev)', '| срез | сделок | WR | avgR | PF | ΣR | maxDD R | ΣR без топ-5 |', '|---|---|---|---|---|---|---|---|']
    dev = [x for x in base if x['t_entry'] < HOLD]
    for lab, f in [('модель A', lambda x: x['model'] == 'A'), ('модель B', lambda x: x['model'] == 'B'),
                   ('15m VC = FVG', lambda x: x['typ'] == 'FVG'), ('15m VC = OB', lambda x: x['typ'] == 'OB'),
                   ('лонг', lambda x: x['s'] == 1), ('шорт', lambda x: x['s'] == -1)]:
        L.append(f'| {lab} ' + fmt(stats([x for x in dev if f(x)])))
    eq, mdd = equity([x for x in base if x['t_entry'] < HOLD])
    eqh, mddh = equity([x for x in base if x['t_entry'] >= HOLD])
    L += ['', f'Капитал при риске 1%/сделку (base): dev ×{eq:.2f}, maxDD {mdd*100:.0f}%; holdout ×{eqh:.2f}, maxDD {mddh*100:.0f}%.']
    why = {}
    for x in base:
        why[x['why']] = why.get(x['why'], 0) + 1
    rr = np.median([x['rr_plan'] for x in base]) if base else 0
    rp = np.median([x['risk_pct'] for x in base]) * 100 if base else 0
    L += [f'Выходы base: {why}. Медиана планового RR {rr:.2f}, медиана стопа {rp:.2f}% цены.']
    open('smc_out/REPORT.md', 'w').write('\n'.join(L) + '\n')
    with open('smc_out/trades_base.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(base[0].keys()) if base else ['sym']); w.writeheader(); w.writerows(base)
    print('\n'.join(L))


if __name__ == '__main__':
    main()
