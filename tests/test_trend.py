import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import brk20v12 as B  # noqa: E402

H = 3600 * 1000
DAY = 24 * H


class TestDayTrend(unittest.TestCase):
    def test_sign(self):
        up = [100 + i for i in range(80)]
        dn = [200 - i for i in range(80)]
        self.assertEqual(B.day_trend_of(up), 1)
        self.assertEqual(B.day_trend_of(dn), -1)
        self.assertEqual(B.day_trend_of(up[:30]), 0)      # мало истории → не отсеиваем

    def test_uses_only_closed_days_before_bar(self):
        # 80 дней роста, затем «сегодняшний» день обвала — он не должен влиять на сигнал внутри этого дня
        js = [[i * DAY, 0, 0, 0, str(100 + i)] for i in range(80)] + [[80 * DAY, 0, 0, 0, '1']]
        old = B._get
        B._get = lambda path, params, timeout=15: js
        try:
            self.assertEqual(B.day_trend('XUSDT', 80 * DAY + 5 * H), 1)
        finally:
            B._get = old

    def test_slot9_module_separate_from_slot3(self):
        code = ('import brk20v12t as T, brk20v12 as B; '
                'assert B.TREND_FILTER and B.SLOT.tag == "slot9" and B.SLOT.STATE_F.endswith("state_slot9.json"); '
                'assert B.ENGINE == "v1.2-trend-1"; print("ok")')
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
        out = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True)
        self.assertIn('ok', out.stdout, out.stderr)
        self.assertFalse(B.TREND_FILTER)                  # слот 3 по умолчанию без фильтра
        self.assertEqual(B.SLOT.tag, 'slot3')


if __name__ == '__main__':
    unittest.main()
