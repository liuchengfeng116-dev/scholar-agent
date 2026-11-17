#!/usr/bin/env bash
set -euo pipefail

# Ubuntu 22.04+ 一键部署脚本
APP_DIR="/opt/scholar-agent"
mkdir -p "$APP_DIR"
cp -r . "$APP_DIR"
cd "$APP_DIR"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt

cd frontend
npm ci
npm run build
cd ..

chown -R www-data:www-data "$APP_DIR/data"
cp deploy/scholar-agent.service /etc/systemd/system/
cp deploy/nginx.conf /etc/nginx/sites-available/scholar-agent
ln -sf /etc/nginx/sites-available/scholar-agent /etc/nginx/sites-enabled/scholar-agent
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable --now scholar-agent
nginx -t && systemctl reload nginx
echo "部署完成！请访问服务器公网 IP 或域名。"
