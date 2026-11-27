"""Тесты слота 8 «VIP CRYPTOX»: разбор поста (плечо, размер, лимит, доли), сделка, поток бота. Без сети и Telegram."""
import importlib
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.modules.setdefault('requests', types.ModuleType('requests'))

P_BTC = """BTCUSDT SHORT ‼️
X5/10
Кросс маржа
Маркет рыночный 
Стоп ставим за пин бар ! За тень свечи на риск выделяем 1-3 процента от имеющегося депозита ✅62.377

60.955 ставим стоп в б/у 🚨
60.418 фиксация 50 процентов 
57.400 полное закрытие , но локально буду смотреть ❗️"""
P_RAVE = """RAVEUSDT X10-15 LONG
Кросс маржа
Вход в рамках: 0.29210-0.29798
🛑 Стоп лосс : 0.28297
Точка 0.30007 стоп в б/у выставляем
Тейк 1: 0.30316 фиксация 40 процентов
Тейк 2: 0.31315 фиксация 30 процентов
Тейк 3 0.32714 полное закрытие
Маржи от баланса 1-5 процентов , не забываем использовать р/м по максимуму."""
P_LIM = """Выставляем лимитный ордер на покупку! Long 🟢
APTUSDT X10-15 Кросс маржа Лимитный ‼️ От точки 0.5965 вход ❗️
🟢T/p 0.6063 фиксация 20 процентов процентов части профита и стоп 🛑 в б/у
🟢Т/p 0.6295 фиксация 50 процентов
T/p 0.6566 полное закрытие!
Stop 🛑 0.5628"""


class Parse(unittest.TestCase):
    def setUp(self):
        self.v = importlib.import_module('brk20vip')

    def test_btc_line_format(self):
        p = self.v.parse_vip(P_BTC)
        self.assertEqual((p['sym'], p['side'], p['lev']), ('BTC', -1, 5))
        self.assertAlmostEqual(p['stop'], 62.377); self.assertAlmostEqual(p['be'], 60.955)
        self.assertEqual([round(x, 3) for x in p['tps']], [60.418, 57.4])
        self.assertEqual(p['size'], ('risk', 0.01))
        self.assertEqual(self.v.plan_parts(p['tps'], p['fix_hints']), [0.5, 0.5])

    def test_rave_fractions_and_margin(self):
        p = self.v.parse_vip(P_RAVE)
        self.assertEqual((p['sym'], p['side'], p['lev']), ('RAVE', 1, 10))
        self.assertEqual(p['size'], ('margin', 0.01))
        self.assertAlmostEqual(p['be'], 0.30007)
        self.assertEqual(self.v.plan_parts(p['tps'], p['fix_hints']), [0.4, 0.3, 0.3])
        self.assertFalse(p['limit'])

    def test_limit(self):
        p = self.v.parse_vip(P_LIM)
        self.assertTrue(p['limit']); self.assertAlmostEqual(p['entry'], 0.5965)
        self.assertEqual(self.v.plan_parts(p['tps'], p['fix_hints']), [0.2, 0.5, 0.3])

    def test_no_levels(self):
        self.assertIsNone(self.v.parse_vip('Открываем Лонг по эфиру ! Тейк и стоп на скрине ! Х10-15'))


class Trade(unittest.TestCase):
    def setUp(self):
        self.v = importlib.import_module('brk20vip')

    def test_risk_sizing_and_flow(self):
        sig = {'side': -1, 'stop': 62377.0, 'tps': [60418.0, 57400.0], 'be': 60955.0, 'lev': 5, 'size': ('risk', 0.01),
               'fix_hints': [0.5, 'full']}
        pos, _ = self.v.open_vip(sig, 61500.0, 0, 1000.0)
        dist = (62377 - 61500) / 61500
        self.assertAlmostEqual(pos['notional'], min(10 / dist, 5000))
        self.assertAlmostEqual(pos['margin'], pos['notional'] / 5)
        self.assertEqual(pos['parts'], [0.5, 0.5])
        d, o, px = self.v.step_vip(pos, 61500, 61600, 60900, 61000, True)      # дошли до точки б/у
        self.assertFalse(d); self.assertEqual(pos['stop'], 61500)
        d, o, px = self.v.step_vip(pos, 61000, 61100, 60400, 60500)            # первый тейк, 50%
        self.assertFalse(d); self.assertAlmostEqual(pos['rem'], 0.5)
        d, o, px = self.v.step_vip(pos, 60500, 61600, 60400, 61550)            # возврат к входу — б/у
        self.assertEqual(o, 'be')

    def test_margin_sizing(self):
        sig = {'side': 1, 'stop': 0.28297, 'tps': [0.30316, 0.31315, 0.32714], 'be': 0.30007, 'lev': 10,
               'size': ('margin', 0.01), 'fix_hints': [0.4, 0.3, 'full']}
        pos, _ = self.v.open_vip(sig, 0.295, 0, 1000.0)
        self.assertAlmostEqual(pos['margin'], 10.0); self.assertAlmostEqual(pos['notional'], 100.0)
        d, o, px = self.v.step_vip(pos, 0.295, 0.2955, 0.28, 0.281, True)
        self.assertEqual(o, 'stop')


class Bot(unittest.TestCase):
    def test_post_and_limit_fill(self):
        v = importlib.reload(importlib.import_module('brk20vip'))
        d = tempfile.mkdtemp(); v.make_slot(d)
        price = {'APTUSDT': 0.61}
        bot = v.VipBot(lambda base, ref: (('APTUSDT', 1.0, price['APTUSDT']), None), lambda s, st: [], lambda s: price[s])
        bot.on_post(1, P_LIM, 1_795_000_000_000)
        self.assertEqual(len(bot.st['pendings']), 1); self.assertEqual(bot.st['positions'], [])
        price['APTUSDT'] = 0.5960
        bot.manage()
        self.assertEqual(len(bot.st['positions']), 1)
        p = bot.st['positions'][0]
        self.assertEqual((p['sym'], p['side'], p['lev']), ('APTUSDT', 'long', 10))
        bot.on_post(2, 'Открываем Лонг по эфиру ! Тейк и стоп на скрине ! Х10-15', 1_795_000_100_000)
        self.assertIn('нет стопа', bot.st['skips'][-1]['why'])
        self.assertTrue(os.path.exists(os.path.join(d, 'state_slot8.json')))


if __name__ == '__main__':
    unittest.main()
