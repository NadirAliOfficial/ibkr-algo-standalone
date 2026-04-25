#!/bin/bash
# VPS setup script for Ubuntu 22.04+
# Usage: sudo bash setup.sh your-domain.com
set -e

DOMAIN=${1:-"localhost"}
APP_DIR="/opt/ibkr-algo"
REPO="https://github.com/NadirAliOfficial/ibkr-algo-standalone.git"

echo "=== Installing system packages ==="
apt-get update -qq
apt-get install -y python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx git nodejs npm redis-server
systemctl enable redis-server
systemctl start redis-server

echo "=== Cloning repo ==="
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR" && git pull
else
    git clone "$REPO" "$APP_DIR"
fi
chown -R ubuntu:ubuntu "$APP_DIR"

echo "=== Python venv ==="
cd "$APP_DIR"
sudo -u ubuntu python3.11 -m venv venv
sudo -u ubuntu venv/bin/pip install -q -r requirements.txt

echo "=== Building React frontend ==="
cd "$APP_DIR/dashboard/frontend"
sudo -u ubuntu npm install -q
sudo -u ubuntu npm run build

echo "=== Creating .env if missing ==="
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" <<EOF
POLYGON_API_KEY=your_polygon_key_here
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=changeme
IB_HOST=127.0.0.1
IB_PORT=4002
EOF
    echo "  Created .env — edit /opt/ibkr-algo/.env with your credentials"
fi

echo "=== Installing systemd services ==="
cp "$APP_DIR/deploy/ibkr-dashboard.service" /etc/systemd/system/
cp "$APP_DIR/deploy/ibkr-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable ibkr-dashboard
systemctl start ibkr-dashboard

echo "=== Configuring nginx ==="
if [ "$DOMAIN" != "localhost" ]; then
    sed "s/DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/ibkr-algo
    ln -sf /etc/nginx/sites-available/ibkr-algo /etc/nginx/sites-enabled/ibkr-algo
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl reload nginx

    echo "=== Obtaining SSL certificate ==="
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN"
else
    echo "  Skipping SSL — no domain provided"
fi

echo ""
echo "=== Done ==="
echo "Dashboard: https://$DOMAIN"
echo "Edit credentials: /opt/ibkr-algo/.env"
echo "Logs: journalctl -u ibkr-dashboard -f"
