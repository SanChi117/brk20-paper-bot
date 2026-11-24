#!/bin/bash
# Обновить терминал и запустить ботов слотов 3–4 одной короткой командой из консоли сервера:
#   cd /root/brk20 && git fetch && git reset --hard origin/main && bash go.sh
cd /root/brk20 || exit 1
bash install_autoupdate.sh
cp brk20v12.service brk20l.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now brk20v12 brk20l
systemctl restart brk20 brk20s brk20-web
echo; echo "=== РЕЗУЛЬТАТ ==="
for s in brk20 brk20s brk20v12 brk20l brk20-web; do
  printf '%-10s %s\n' "$s" "$(systemctl is-active $s)"
done
echo "Если везде active — готово."
