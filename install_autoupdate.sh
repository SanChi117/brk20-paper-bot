#!/bin/bash
# Одноразовая установка автообновления BRK-20. Запуск: bash /root/brk20/install_autoupdate.sh
set -e
cd "$(dirname "$0")"
[ -d .git ] || { echo "ОШИБКА: $(pwd) не git-репозиторий"; exit 1; }
git config --global --add safe.directory "$(pwd)" || true
cp brk20-update.service brk20-update.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now brk20-update.timer
bash ./auto_update.sh || true
echo "--- автообновление включено (каждые 15 минут). Последние события:"
tail -5 update.log 2>/dev/null || echo "(обновлений пока не было — версия уже актуальная)"
echo "--- статус бота:"
cat status.txt 2>/dev/null; cat status_slot2.txt 2>/dev/null
