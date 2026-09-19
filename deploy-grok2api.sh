#!/bin/bash
# Deploy grok2api ke VPS Ubuntu/Debian
# Jalankan: bash deploy-grok2api.sh

set -e

echo "=== Install Docker ==="
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. You may need to logout/login for group to take effect."
fi

echo "=== Clone grok2api ==="
cd /opt
if [ -d "grok2api" ]; then
    cd grok2api && git pull
else
    git clone https://github.com/chenyme/grok2api.git
    cd grok2api
fi

echo "=== Generate APP_KEY ==="
APP_KEY=$(openssl rand -hex 16)
echo "Your APP_KEY: $APP_KEY"
echo "SAVE THIS KEY! You need it for grok-register config."

echo "=== Create .env ==="
cat > .env << EOF
PORT=8080
APP_KEY=${APP_KEY}
MODE=multi
LOG_LEVEL=info
EOF

echo "=== Start with Docker ==="
docker compose up -d --build

echo "=== Done ==="
echo "API URL: http://$(curl -s ifconfig.me):8080"
echo "APP_KEY: $APP_KEY"
echo ""
echo "Update grok-register config.json:"
echo '  "grok2api_auto_add_remote": true,'
echo '  "grok2api_remote_base": "http://YOUR_VPS_IP:8080",'
echo "  \"grok2api_remote_app_key\": \"$APP_KEY\""
