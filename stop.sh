#!/usr/bin/env bash
# Stop Marketplace Sniper.
pkill -f "mpwatch.main" && echo "Stopped. Run ./start.sh to start again." || echo "It wasn't running."
