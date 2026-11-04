#!/bin/bash
# BRK-20 web terminal — установка второго сервиса
set -e
cd "$(dirname "$0")"
apt install -y python3-requests
cp brk20-web.service /etc/systemd/system/brk20-web.service
systemctl daemon-reload
systemctl restart brk20
systemctl enable --now brk20-web
sleep 3
systemctl status brk20-web --no-pager | head -12
echo "---"
TOKEN=$(python3 -c "import json;print(json.load(open('/root/brk20/web_config.json'))['token'])")
IP=$(curl -s ifconfig.me || hostname -I | awk '{print $1}')
echo ""
echo "ОТКРОЙ В БРАУЗЕРЕ:  http://$IP:8080/?key=$TOKEN"
echo "(эта ссылка — единственный ключ к терминалу, сохрани её)"
