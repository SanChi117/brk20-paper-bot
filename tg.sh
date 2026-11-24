#!/bin/bash
# Выгрузка закрытых Telegram-каналов вашим аккаунтом. Запуск: cd /root/brk20 && bash tg.sh
cd /root/brk20 || exit 1
if ! python3 -c "import telethon" 2>/dev/null; then
  echo "Ставлю библиотеку Telegram (один раз, ~1 минута)…"
  python3 -m pip install -q telethon --break-system-packages 2>/dev/null || python3 -m pip install -q telethon 2>/dev/null || {
    apt-get update -qq && apt-get install -y -qq python3-pip >/dev/null && python3 -m pip install -q telethon --break-system-packages; }
fi
python3 tg/tg_export.py
