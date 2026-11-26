"""Тесты слота 7 «СОБЫТИЯ»: разбор объявлений о метке мониторинга, сигналы пролива, расчёт результата, сопровождение."""
import importlib
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.modules.setdefault('requests', types.ModuleType('requests'))
M5 = 300000


class Parse(unittest.TestCase):
    def setUp(self):
        self.b = importlib.import_module('brk20ev')

    def test_titles(self):
        p = self.b.parse_tag_title
        self.assertEqual(p('Binance Will Extend the Monitoring Tag to Include BANK, INIT, MITO, ONE, THE & ZIL, '
                           'Remove the Seed Tag for MORPHO & HYPE on 2026-11-20'), ['BANK', 'INIT', 'MITO', 'ONE', 'THE', 'ZIL'])
        self.assertEqual(p('Binance Will Extend the Monitoring Tag to Include BICO & CVC on 2026-10-09'), ['BICO', 'CVC'])
        self.assertEqual(p('Binance Will Delist AE & HC on 2020/12/30'), [])
        self.assertEqual(p('Binance Will Remove the Monitoring Tag for ABC on 2026-01-01'), [])
        self.assertEqual(p(None), [])


class Signals(unittest.TestCase):
    def setUp(self):
        self.b = importlib.import_module('brk20ev')

    def bars(self, last):
        base = [(i * M5, 100.0, 100.5, 99.5, 100.0) for i in range(24)]
        return base + [last]

    def test_dip(self):
        self.assertIn('пролив', self.b.dip_signal(self.bars((24 * M5, 100.0, 100.0, 96.0, 96.9))))
        self.assertIsNone(self.b.dip_signal(self.bars((24 * M5, 100.0, 100.0, 98.0, 98.0))))

    def test_reversal(self):
        bars = [(i * M5, 100.0, 106.0 if i == 3 else 100.5, 99.5, 100.0) for i in range(24)]
        bars.append((24 * M5, 100.0, 102.5, 99.9, 102.2))       # −5.7% от максимума 106, свеча +2.2%
        self.assertIn('разворот', self.b.dip_signal(bars))

    def test_short_history(self):
        self.assertIsNone(self.b.dip_signal([(0, 1, 1, 1, 1)] * 10))

    def test_pnl(self):
        pos = self.b.new_position('XUSDT', 'short', 100.0, 0, 50.0, 120.0, 0, 'tag', 'x')
        self.assertAlmostEqual(self.b.pnl_of(pos, 95.0), 50 * (0.05 - 0.0016))
        self.assertAlmostEqual(pos['r_usd'], 0.5)
        lg = self.b.new_position('XUSDT', 'long', 100.0, 0, 200.0, None, 0, 'dip', 'x')
        self.assertAlmostEqual(self.b.pnl_of(lg, 101.0), 200 * (0.01 - 0.0016))


class Bot(unittest.TestCase):
    def test_tag_flow(self):
        b = importlib.reload(importlib.import_module('brk20ev'))
        d = tempfile.mkdtemp(); b.SLOT.set_dir(d)
        now = 1_795_000_000_000
        anns = [(now - 10 * 60000, 'Binance Will Extend the Monitoring Tag to Include BICO & CVC on 2026-10-09', 'a2')]
        b.announcements = lambda: anns
        b.perp_of = lambda c: c + 'USDT' if c == 'BICO' else None
        b.price = lambda s: 0.10
        st = b.SLOT.load_state()
        st['ann_init'] = True
        b.step_tags(st, now)
        self.assertEqual([p['sym'] for p in st['positions']], ['BICOUSDT'])
        p = st['positions'][0]
        self.assertAlmostEqual(p['notional'], 1000 * 0.05)
        self.assertAlmostEqual(p['stop'], 0.12)
        b.step_tags(st, now + 60000)                              # то же объявление второй раз не торгуется
        self.assertEqual(len(st['positions']), 1)
        b.klines5 = lambda s, limit=30: [(now, 0.10, 0.125, 0.099, 0.12)]
        b.step_exits(st, now + 60000)
        self.assertEqual(st['trades'][-1]['out'], 'stop')
        self.assertAlmostEqual(st['trades'][-1]['r_usd'], 50 * (-0.20 - 0.0016))

    def test_first_run_ignores_old(self):
        b = importlib.reload(importlib.import_module('brk20ev'))
        d = tempfile.mkdtemp(); b.SLOT.set_dir(d)
        now = 1_795_000_000_000
        b.announcements = lambda: [(now - 6 * 60000, 'Binance Will Extend the Monitoring Tag to Include BICO on 2026-10-09', 'a1')]
        b.perp_of = lambda c: c + 'USDT'; b.price = lambda s: 1.0
        st = b.SLOT.load_state()
        b.step_tags(st, now)
        self.assertEqual(st['positions'], [])

    def test_dip_flow_and_time_exit(self):
        b = importlib.reload(importlib.import_module('brk20ev'))
        d = tempfile.mkdtemp(); b.SLOT.set_dir(d)
        now = 1_795_000_000_000 // M5 * M5 + 20000
        bar_t = now // M5 * M5 - M5
        base = [(bar_t - (24 - i) * M5, 100.0, 100.5, 99.5, 100.0) for i in range(24)]
        b.POOL = ['AUSDT', 'BUSDT']
        b.klines5 = lambda s, limit=30: base + [(bar_t, 100.0, 100.0, 96.0, 96.5)] if s == 'AUSDT' else base + [(bar_t, 100, 100, 100, 100)]
        b.price = lambda s: 96.6
        st = b.SLOT.load_state()
        b.step_dips(st, now)
        self.assertEqual([p['sym'] for p in st['positions']], ['AUSDT'])
        self.assertEqual(st['positions'][0]['exit_t'], bar_t + 13 * M5)
        b.step_dips(st, now + 1000)                              # та же свеча — повторно не входим
        self.assertEqual(len(st['positions']), 1)
        b.price = lambda s: 97.6
        b.step_exits(st, bar_t + 13 * M5)
        self.assertEqual(st['trades'][-1]['out'], 'time')
        self.assertGreater(st['trades'][-1]['r_usd'], 0)


if __name__ == '__main__':
    unittest.main()
