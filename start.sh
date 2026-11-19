#!/usr/bin/env bash
# Start Marketplace Sniper in the background.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Not installed yet - run ./setup.sh first."; exit 1; }
mkdir -p logs
nohup .venv/bin/python -m mpwatch.main >> logs/console.log 2>&1 &
echo "Marketplace Sniper is running in the background. Log: logs/mpwatch.log"
