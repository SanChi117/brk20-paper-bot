"""Тесты бумажного копи-бота CryptoX (слоты 5–6): разбор постов и ведение сделки. Без сети и без Telegram."""
import importlib
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.modules.setdefault('requests', types.ModuleType('requests'))

POST = """🚀 #APT LONG

Плечо: 25-30x🔵

⏺Диапазон входа: по рынку
⏺Тейки: 0.3769 0.3822 0.3980
⏺Cтоп: 0.3524

Входим на 5000$
🏦Банк: 45010.76$

📈BingX— забрать $59за регистрацию с KYC"""


class Parse(unittest.TestCase):
    def setUp(self):
        self.b = importlib.import_module('brk20cx')

    def test_signal(self):
        s = self.b.parse_signal(POST)
        self.assertEqual((s['sym'], s['side'], s['lev']), ('APT', 1, 25))
        self.assertEqual(s['tps'], [0.3769, 0.3822, 0.398])
        self.assertEqual(s['stop'], 0.3524)          # «Cтоп» с латинской C
        self.assertIsNone(s['entry'])
        self.assertAlmostEqual(s['frac'], 5000 / 45010.76)
        self.assertAlmostEqual(s['claimed_bank'], 45010.76)

    def test_short_without_stop_and_entry_price(self):
        s = self.b.parse_signal("🚀 #ORDI SHORT\n\nПлечо: 25x\n\n⏺Диапазон входа: 28.316 - 28.809\n⏺Тейки: 28.087 27.805 26.641\n⏺Cтоп: пока не ставлю")
        self.assertEqual((s['sym'], s['side'], s['stop']), ('ORDI', -1, None))
        self.assertAlmostEqual(s['entry'], (28.316 + 28.809) / 2)

    def test_not_signals(self):
        for t in ('Заполняю SUSHI long', '#SUSHI полетела сделка. Забрали два тейка, закрыл её фулл.', '', None):
            self.assertIsNone(self.b.parse_signal(t))
        self.assertAlmostEqual(self.b.claimed_bank('+2.9$ | Банк: 195.5$'), 195.5)


class Trade(unittest.TestCase):
    def setUp(self):
        self.b = importlib.import_module('brk20cx')
        self.sig = {'side': 1, 'lev': 25, 'tps': [101.0, 102.0, 105.0], 'stop': 94.5, 'frac': 0.1}

    def run_bars(self, pos, bars):
        for k, x in enumerate(bars):
            done, out, px = self.b.step_bar(pos, *x, first=(k == 0))
            if done:
                return out, px
        return None, None

    def test_iso_liquidates_before_channel_stop(self):
        pos, _ = self.b.open_position(self.sig, 100.0, 0, 100.0, 'iso')
        self.assertAlmostEqual(pos['stop'], 100 * (1 - (1 / 25 - 0.005)))      # 96.5 — выше стопа канала 94.5
        out, px = self.run_bars(pos, [(100, 100.5, 99.5, 100), (100, 100.2, 96.0, 96.2), (96.2, 102, 96, 101.5)])
        self.assertEqual(out, 'liquidation')
        self.assertAlmostEqual(pos['realized'], -10.0)                           # вся маржа 10$ (10% от 100$)

    def test_cross_uses_channel_stop(self):
        pos, _ = self.b.open_position(self.sig, 100.0, 0, 100.0, 'cross')
        self.assertEqual(pos['stop'], 94.5)
        out, px = self.run_bars(pos, [(100, 100.5, 99.5, 100), (100, 100.2, 96.0, 96.2), (96.2, 96.3, 94.0, 94.2)])
        self.assertEqual(out, 'stop')
        self.assertAlmostEqual(pos['realized'], 250 * (-0.055 - 0.0005) - 250 * 0.0005)

    def test_tp1_half_then_breakeven(self):
        pos, _ = self.b.open_position(self.sig, 100.0, 0, 100.0, 'cross')
        out, px = self.run_bars(pos, [(100, 100.5, 99.5, 100), (100, 101.2, 99.8, 101), (101, 101.5, 99.9, 100)])
        self.assertEqual((out, px, pos['hit']), ('be', 100.0, 1))
        self.assertAlmostEqual(pos['realized'], -0.125 + 125 * (0.01 - 0.0002) + 125 * (-0.0005))

    def test_two_takes_close_all(self):
        pos, _ = self.b.open_position(self.sig, 100.0, 0, 100.0, 'iso')
        out, _ = self.run_bars(pos, [(100, 100.5, 99.5, 100), (100, 102.3, 99.9, 102)])
        self.assertEqual(out, 'tp2')
        self.assertAlmostEqual(pos['rem'], 0)

    def test_stop_before_take_in_same_bar(self):
        pos, _ = self.b.open_position(self.sig, 100.0, 0, 100.0, 'cross')
        out, _ = self.run_bars(pos, [(100, 100.1, 99.9, 100), (100, 101.5, 94.0, 100)])
        self.assertEqual(out, 'stop')

    def test_rejects_moved_price(self):
        pos, why = self.b.open_position(self.sig, 101.5, 0, 100.0, 'iso')
        self.assertIsNotNone(pos)                                                 # TP1 уже позади — работаем с оставшимися
        self.assertEqual(pos['tps'], [102.0, 105.0])
        pos, why = self.b.open_position(self.sig, 106.0, 0, 100.0, 'iso')
        self.assertIsNone(pos)

    def test_bot_books_into_both_slots(self):
        b = importlib.reload(importlib.import_module('brk20cx'))
        d = tempfile.mkdtemp()
        for sl in b.SLOTS.values():
            sl.set_dir(d)
        b.resolve = lambda base, ref: (('APTUSDT', 1.0, 0.3735), None)
        bot = b.Bot()
        bot.on_signal(13099, POST, 1_795_000_000_000)
        for k in ('iso', 'cross'):
            self.assertEqual(len(bot.st[k]['positions']), 1)
            p = bot.st[k]['positions'][0]
            self.assertAlmostEqual(p['margin'], 100 * 5000 / 45010.76)
        self.assertEqual(bot.st['iso']['positions'][0]['stop_kind'], 'ликвидация (раньше стопа канала)')
        self.assertEqual(bot.st['cross']['positions'][0]['stop'], 0.3524)
        bot.on_note(13110, '#APT а тут забрали два тейка, закрыл 50% позиции. Банк: 53045.96$', 1_795_000_100_000)
        self.assertEqual(len(bot.st['iso']['positions'][0]['ctx']['notes']), 1)
        self.assertEqual(bot.st['iso']['claimed'][-1][1], 53045.96)
        self.assertTrue(os.path.exists(os.path.join(d, 'state_slot5.json')))
        self.assertTrue(os.path.exists(os.path.join(d, 'state_slot6.json')))


if __name__ == '__main__':
    unittest.main()
