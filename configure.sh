#!/usr/bin/env bash
# Change your settings, then restart.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Not installed yet - run ./setup.sh first."; exit 1; }
.venv/bin/python setup_wizard.py && ./stop.sh >/dev/null 2>&1; ./start.sh
