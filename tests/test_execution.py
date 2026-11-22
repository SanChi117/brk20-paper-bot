"""Тесты целостности бумажного исполнения BRK-20 (лонг) и BRK-20S (шорт).

Запуск без сети и без зависимостей:  python3 -m unittest discover -s tests -v
"""
import importlib
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.modules.setdefault('requests', types.ModuleType('requests'))  # сеть в тестах не нужна

H = 3600 * 1000
H4 = 4 * H


def load(name):
    mod = importlib.import_module(name)
    mod = importlib.reload(mod)
    tmp = tempfile.mkdtemp()
    for attr in ('STATE_F', 'TRADES_F', 'STATUS_F', 'JOURNAL_F', 'CONTROL_F'):
        setattr(mod, attr, os.path.join(tmp, os.path.basename(getattr(mod, attr))))
    return mod


def series(t4, side):
    """200 4h-баров диапазона, затем сигнальная 4h-свеча из 4 часовых и 2 часа после неё.

    Лонг: hi20 = 100, сигнальная свеча закрывается на 104, следующий час открывается 104.5
    и больше к 100 не возвращается. Шорт — зеркально (lo20 = 100, закрытие 96).
    """
    rows = []
    t = t4 - 200 * H4
    while t < t4:
        if side == 'long':
            hi = 100.0 if (t // H) % 50 == 0 else 99.0
            rows.append((t, 95.0, hi, 94.0, 95.0))
        else:
            lo = 100.0 if (t // H) % 50 == 0 else 101.0
            rows.append((t, 105.0, 106.0, lo, 105.0))
        t += H
    sig_long = [(99, 101, 98.5, 100.5), (100.5, 102, 100.2, 101.8),
                (101.8, 103, 101.5, 102.9), (102.9, 104.2, 102.6, 104.0),
                (104.5, 105.5, 104.1, 105.0), (105.0, 106.0, 104.6, 105.8)]
    for k, (o, h, l, c) in enumerate(sig_long):
        if side == 'short':
            o, h, l, c = 200 - o, 200 - l, 200 - h, 200 - c
        rows.append((t4 + k * H, float(o), float(h), float(l), float(c)))
    return rows


class Harness:
    def __init__(self, name, side):
        self.b = load(name)
        self.side = side
        self.t4 = (10**12 // H4) * H4
        self.rows = series(self.t4, side)
        self.b.SYMS = ['SOLUSDT']
        self.st = self.b.default_state()

    def signal(self):
        cut = [r for r in self.rows if r[0] < self.t4 + H4]
        self.b.klines = lambda sym, interval, limit: cut[-limit:]
        self.b.time.time = lambda: (self.t4 + H4 + 60_000) / 1000
        self.b.scan_signals(self.st)

    def feed(self, upto_hours):
        """Подаёт закрытые часовые бары так же, как main(): последние 3 бара и last_1h."""
        for t, o, h, l, c in [r for r in self.rows if r[0] < self.t4 + upto_hours * H][-3:]:
            if self.st['last_1h'].get('SOLUSDT', 0) >= t:
                continue
            self.b.process_bar_1h(self.st, 'SOLUSDT', t, o, h, l, c, None)
            self.st['last_1h']['SOLUSDT'] = t


class LookaheadTests(unittest.TestCase):
    def check(self, name, side):
        hz = Harness(name, side)
        hz.signal()
        self.assertEqual(len(hz.st['pendings']), 1, 'должна появиться заявка')
        self.assertEqual(hz.st['pendings'][0]['active_from'], hz.t4 + H4)
        hz.feed(4)  # бары внутри сигнальной свечи: исполнения быть не должно
        self.assertEqual(hz.st['positions'], [], 'вход до появления сигнала = подглядывание')
        hz.feed(5)  # первый бар после закрытия сигнальной свечи
        self.assertEqual(len(hz.st['positions']), 1)
        pos = hz.st['positions'][0]
        self.assertEqual(pos['entry_t'], hz.t4 + H4)
        if side == 'long':
            self.assertAlmostEqual(pos['entry'], 104.5 * (1 + hz.b.SLIP))
            self.assertGreater(pos['entry'], pos['signal_entry'])
        else:
            self.assertAlmostEqual(pos['entry'], 95.5 * (1 - hz.b.SLIP))
            self.assertLess(pos['entry'], pos['signal_entry'])
        expected = abs(pos['entry'] - pos['stop']) / pos['entry']
        self.assertAlmostEqual(pos['dist'], expected)

    def test_long_no_fill_before_signal(self):
        self.check('brk20_paper', 'long')

    def test_short_no_fill_before_signal(self):
        self.check('brk20s', 'short')


class GapAndManualTests(unittest.TestCase):
    def open_position(self, name, side):
        hz = Harness(name, side)
        hz.signal(); hz.feed(5)
        return hz, hz.st['positions'][0]

    def test_long_gap_below_stop_exits_at_open(self):
        hz, pos = self.open_position('brk20_paper', 'long')
        gap_open = pos['stop'] - 3.0
        hz.b.process_bar_1h(hz.st, 'SOLUSDT', hz.t4 + 6 * H, gap_open, gap_open + 0.5,
                            gap_open - 1, gap_open, None)
        self.assertEqual(hz.st['positions'], [])
        self.assertAlmostEqual(hz.st['trades'][-1]['exit'], gap_open * (1 - hz.b.SLIP))

    def test_short_gap_above_stop_exits_at_open(self):
        hz, pos = self.open_position('brk20s', 'short')
        gap_open = pos['stop'] + 3.0
        hz.b.process_bar_1h(hz.st, 'SOLUSDT', hz.t4 + 6 * H, gap_open, gap_open + 1,
                            gap_open - 0.5, gap_open, None)
        self.assertEqual(hz.st['positions'], [])
        self.assertAlmostEqual(hz.st['trades'][-1]['exit'], gap_open * (1 + hz.b.SLIP))

    def test_stop_inside_fill_bar_is_counted(self):
        hz = Harness('brk20_paper', 'long')
        hz.signal()
        stop = hz.st['pendings'][0]['stop']
        hz.b.process_bar_1h(hz.st, 'SOLUSDT', hz.t4 + H4, 104.5, 105.0, stop - 1, stop - 0.5, None)
        self.assertEqual(hz.st['positions'], [])
        self.assertEqual(hz.st['trades'][-1]['out'], 'stop')

    def test_manual_close_excluded_from_gate(self):
        hz, pos = self.open_position('brk20_paper', 'long')
        hz.st['trades'].append({'sym': 'X', 'entry': 1, 'exit': 2, 'r': 5, 'r_usd': 50, 'out': 'manual'})
        n, pf, wr = hz.b.stats(hz.st)
        self.assertEqual(n, 0)

    def test_equity_and_risk_frozen_during_gate(self):
        hz = Harness('brk20_paper', 'long')
        os.environ.pop('BRK20_ALLOW_GATE_OVERRIDES', None)
        import json
        with open(hz.b.CONTROL_F, 'w') as f:
            json.dump([{'cmd': 'set_equity', 'value': 5000}, {'cmd': 'set_risk', 'value': 0.02}], f)
        hz.b.apply_controls(hz.st)
        self.assertEqual(hz.st['eq'], hz.b.EQ0)
        self.assertEqual(hz.st['risk'], hz.b.RISK)


if __name__ == '__main__':
    unittest.main()
