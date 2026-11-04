#!/bin/bash
# BRK-20 paper bot — установка на Ubuntu 24.04
set -e
cd "$(dirname "$0")"
apt install -y python3-requests
cp brk20.service /etc/systemd/system/brk20.service
systemctl daemon-reload
systemctl enable --now brk20
sleep 3
systemctl status brk20 --no-pager | head -15
echo "---"
echo "Если выше зелёное 'active (running)' — бот работает."
echo "Статус: cat /root/brk20/status.txt | Журнал: tail -20 /root/brk20/journal.log"
