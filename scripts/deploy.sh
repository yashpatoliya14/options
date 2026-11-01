#!/bin/bash
set -e

echo "=== Deploying Options IV Arbitrage Algo (Node.js / TS) ==="

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

# 5. Restart application with PM2
if command -v pm2 &> /dev/null; then
    echo "Reloading process with PM2..."
    pm2 reload ecosystem.config.js || pm2 start ecosystem.config.js
    pm2 save
else
    echo "PM2 not found. Running in background with nohup..."
    pkill -f "dist/index.js" || true
    nohup node dist/index.js >> algo.log 2>&1 &
fi

echo "=== Deployment Complete ==="
