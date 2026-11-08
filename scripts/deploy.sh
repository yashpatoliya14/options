#!/bin/bash
set -e

echo "=== Deploying Delta Gold Trend + Grid Hedging Bot ==="

# 1. Navigate to project directory
cd "$(dirname "$0")/.." || cd ~/options

# 2. Pull latest code from git
echo "Pulling latest changes from master..."
git pull origin master

# 3. Install dependencies
echo "Installing Node.js dependencies..."
npm install

# 4. Compile TypeScript to JavaScript
echo "Building TypeScript..."
npm run build

# 5. Ensure data directories exist
mkdir -p data data/backups

# 6. Restart application with systemd (options.service or options-algo)
if systemctl is-active --quiet options.service || systemctl is-enabled --quiet options.service 2>/dev/null; then
    echo "Restarting systemd service options.service..."
    sudo systemctl restart options.service
    sudo systemctl status options.service --no-pager
elif systemctl is-active --quiet options-algo; then
    echo "Restarting systemd service options-algo..."
    sudo systemctl restart options-algo
    sudo systemctl status options-algo --no-pager
elif command -v pm2 &> /dev/null; then
    echo "Reloading process with PM2..."
    pm2 reload ecosystem.config.js || pm2 start ecosystem.config.js
    pm2 save
else
    echo "Restarting in background with nohup..."
    pkill -f "dist/index.js" || true
    nohup node dist/index.js >> algo.log 2>&1 &
fi

echo "=== Deployment Complete ==="


