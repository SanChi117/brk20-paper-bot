"""Тесты бумажных ботов слотов 3 (V1.2) и 4 (листинги). Без сети: биржевые вызовы подменяются."""
import importlib
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.modules.setdefault('requests', types.ModuleType('requests'))

H = 3600 * 1000
T0 = (1_760_000_000_000 // H) * H


def fresh(name):
    mod = importlib.reload(importlib.import_module(name))
    mod.SLOT.set_dir(tempfile.mkdtemp())
    return mod


class V12Replay(unittest.TestCase):
    def setUp(self):
        self.b = fresh('brk20v12')

    def bars(self, closes, hi_add=0.2, lo_add=0.2):
        out = []
        prev = closes[0]
        for k, c in enumerate(closes):
            o = prev
            out.append((T0 + k * H, o, max(o, c) + hi_add, min(o, c) - lo_add, c))
            prev = c
        return out

    def test_add_then_ema_exit_next_open(self):
        b = self.b
        closes = [100] * 30 + [101, 102.5, 104, 105, 104.5, 103, 101, 99, 98]
        bars = self.bars(closes)
        em = b.ema([x[4] for x in bars])
        j0 = 30; e1 = bars[j0][1]; stop = 98.0; risk = e1 - stop
        r = b.replay(bars, em, j0, 1, e1, stop, 99.5, risk)
        self.assertTrue(r['done'])
        self.assertEqual(r['out'], 'ema20')
        self.assertEqual(len(r['units']), 2)                       # добор на +1R случился
        self.assertAlmostEqual(r['units'][1], max(bars[31][1], e1 + risk))   # уровень +1R пробит на баре 31
        jx = r['j_exit']
        self.assertEqual(r['px'], bars[jx][1])                     # выход по открытию бара после сигнала
        self.assertLess(bars[jx - 1][4], em[jx - 1])

    def test_stop_gap_fills_at_open(self):
        b = self.b
        bars = [(T0, 100, 100.5, 99.8, 100.2), (T0 + H, 96, 96.5, 95, 95.5)]
        em = [100, 100]
        r = b.replay(bars, [0, 0], 0, 1, 100.0, 98.0, 99.0, 2.0)
        self.assertEqual((r['out'], r['px']), ('stop', 96))

    def test_time_exit_72h_and_pnl(self):
        b = self.b
        bars = [(T0 + k * H, 100 + k * 0.01, 100.3 + k * 0.01, 99.9 + k * 0.01, 100.1 + k * 0.01) for k in range(80)]
        em = [0] * 80
        r = b.replay(bars, em, 0, 1, 100.0, 95.0, 99.0, 5.0)
        self.assertEqual((r['out'], r['j_exit']), ('time72', 71))
        pnl = b.pnl_of(1, [100.0], 2.0, 101.0)
        self.assertAlmostEqual(pnl, 2.0 - b.K * 100 * 2 - b.K * 101 * 2)

    def test_short_is_mirror(self):
        b = self.b
        up = [(T0 + k * H, 100, 100.4, 99.6, 100) for k in range(3)]
        dn = [(t, 200 - o, 200 - l, 200 - h, 200 - c) for t, o, h, l, c in up]
        r1 = b.replay(up, [101] * 3, 0, 1, 100, 99.7, 99.8, 0.3)
        r2 = b.replay(dn, [99] * 3, 0, -1, 100, 100.3, 100.2, 0.3)
        self.assertEqual(r1['out'], r2['out'])

    def test_breakout_needs_compression_and_pause(self):
        b = self.b
        rows = []
        for k in range(1700):
            c = 100 + (0.5 if k % 2 else -0.5) * (1 if k < 1550 else 0.05)
            if k == 1650:
                c = 103
            rows.append((T0 + k * H, 100, max(100, c) + 0.01, min(100, c) - 0.01, c, 1e6, 5e5))
        S = b.Series(rows)
        ev = b.breakouts(S, start=1461)
        self.assertIn(1650, [e[0] for e in ev])
        self.assertTrue(all(ev[k + 1][0] - ev[k][0] >= 24 for k in range(len(ev) - 1)))


class Listings(unittest.TestCase):
    def setUp(self):
        self.b = fresh('brk20l')

    def k(self, path):
        out, prev = [], path[0]
        for j, c in enumerate(path):
            out.append((T0 + j * H, prev, max(prev, c) * 1.001, min(prev, c) * 0.999, c)); prev = c
        return out

    def test_signal_after_pump_breaks_below_open(self):
        k = self.k([1.0, 1.08, 1.12, 1.1, 1.05, 1.02, 1.01, 0.98, 0.95])
        i, stop = self.b.find_signal(k)
        self.assertEqual(i, 8)
        self.assertAlmostEqual(stop, max(b[2] for b in k[:8]) * 1.02)

    def test_no_signal_without_pump_and_expiry(self):
        k = self.k([1.0, 1.02, 1.01] + [0.95] * 200)
        self.assertIsNone(self.b.find_signal(k))
        self.assertEqual(self.b.find_signal(self.k([1.0, 1.02, 0.99])), 'wait')

    def test_take_profit_and_stop_first(self):
        b = self.b
        k = [(T0, 1.0, 1.0, 0.99, 0.99), (T0 + H, 0.99, 0.995, 0.70, 0.72)]
        r = b.simulate(k, 0, 1.2)
        self.assertEqual(r['out'], 'tp')
        self.assertAlmostEqual(r['R'], ((1 - 0.75) - 2 * b.COST) / 0.2)
        k2 = [(T0, 1.0, 1.25, 0.70, 0.8)]
        self.assertEqual(b.simulate(k2, 0, 1.2)['out'], 'stop')     # стоп раньше тейка в одном баре

    def test_full_cycle_with_fake_exchange(self):
        b = self.b
        now = T0 + 20 * H
        path = [1.0, 1.08, 1.12, 1.1, 1.05, 1.02, 1.01, 0.98, 0.95, 0.9, 0.8, 0.7, 0.69]
        bars = self.k(path)
        b.new_listings = lambda since: [('NEWUSDT', T0, True), ('AAPLUSDT', T0, False)]
        cut = {'n': 5}

        def bars_since(sym, t0):
            ks = [x for x in bars[:cut['n']] if x[0] >= t0]
            nxt = bars[cut['n']] if cut['n'] < len(bars) else None
            return ks, nxt
        b.bars_since = bars_since
        st = b.SLOT.default_state()
        b.step(st, T0 + 5 * H)                         # бот видит листинг с первых часов
        self.assertEqual(st['watch']['AAPLUSDT']['state'], 'skip')
        self.assertEqual(len(st['pendings']), 1)        # наблюдение показывается как «заявка»
        cut['n'] = 9
        b.step(st, T0 + 9 * H)
        self.assertEqual(len(st['positions']), 1)
        pos = st['positions'][0]
        self.assertEqual(pos['entry'], bars[8][1])
        cut['n'] = len(bars)
        b.step(st, now + 10 * H)
        self.assertEqual(len(st['positions']), 0)
        self.assertEqual(st['trades'][-1]['out'], 'tp')
        self.assertGreater(st['eq'], 1000)


if __name__ == '__main__':
    unittest.main()
