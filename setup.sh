#!/usr/bin/env bash
# Marketplace Sniper - setup for Mac and Linux. Run:  ./setup.sh   (safe to run again)
set -e
cd "$(dirname "$0")"
echo "================================================================="
echo " MARKETPLACE SNIPER - setup (takes about 5 minutes)"
echo "================================================================="
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed."
  echo "  Mac:   install it from https://www.python.org/downloads/  (or: brew install python)"
  echo "  Linux: sudo apt install python3 python3-venv   (or your distro's equivalent)"
  exit 1
fi
echo; echo "[1/5] Python: $($PY --version)"
./stop.sh >/dev/null 2>&1 || true
echo; echo "[2/5] Installing the app's packages..."
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
echo; echo "[3/5] Downloading the browser it uses to look at Marketplace..."
.venv/bin/python -m playwright install chromium || echo "  (failed - it will try your installed Chrome instead)"
if [ "$(uname)" = "Linux" ]; then .venv/bin/python -m playwright install-deps chromium 2>/dev/null || true; fi
echo; echo "[4/5] Your settings"
.venv/bin/python setup_wizard.py
echo; echo "[5/5] Optional: free AI photo check"
echo "  A small AI model on this computer checks each deal's photo. Free, ~5 GB download."
if command -v ollama >/dev/null; then
  ollama pull gemma3:4b || true
else
  echo "  To add it later: install Ollama from https://ollama.com, run  ollama pull gemma3:4b,  then ./start.sh"
fi
chmod +x start.sh stop.sh configure.sh
./start.sh
echo
echo "================================================================="
echo " All done! Your bot will message you in a minute. Then just type"
echo " what you want to it, e.g.   xbox series s"
echo
echo " Stop: ./stop.sh    Start: ./start.sh    Change setup: ./configure.sh"
echo " To start it automatically, see 'Run it all the time' in README.md"
echo "================================================================="
