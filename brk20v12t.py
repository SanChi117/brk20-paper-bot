#!/usr/bin/env python3
# V1.2 ТРЕНД — слот 9: та же V1.2 (brk20v12.py), но пробои против дневного тренда пропускаются.
# Дневной тренд = знак (закрытие вчерашнего дня UTC − EMA50 дневных закрытий). Если данных нет — сделка не отсеивается.
# Исследование: p2/v12_improve.py, вариант C2 (ветка mme/label-study). Отдельный бумажный счёт 1000$, ордеров нет.
import brk20v12 as B
from slotlib import Slot

B.TREND_FILTER = True
B.ENGINE = 'v1.2-trend-1'
B.SLOT = Slot('slot9', 'V1.2 ТРЕНД', B.DIR, eq0=1000.0, risk=B.RISK, engine=B.ENGINE)

if __name__ == '__main__':
    B.main()
