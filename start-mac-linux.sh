#!/bin/bash
# UDM launcher for macOS / Linux
cd "$(dirname "$0")" || exit 1
echo ""
echo "=== UDM ==="

# Already running? Just open it.
if curl -s -m 2 http://127.0.0.1:5757/api/ping 2>/dev/null | grep -q UDM; then
  echo "UDM is already running."
  open "http://127.0.0.1:5757" 2>/dev/null || xdg-open "http://127.0.0.1:5757" 2>/dev/null
  exit 0
fi

# 1. Find Python 3.10 or newer (Apple's built-in /usr/bin/python3 is often 3.9 = too old)
PY=""
for p in \
  /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
  /opt/homebrew/bin/python3 /usr/local/bin/python3 \
  $(command -v python3.13 python3.12 python3.11 python3.10 python3 2>/dev/null); do
  if [ -x "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
if [ -z "$PY" ]; then
  echo ""
  echo "Python 3.10 or newer was not found on this Mac."
  echo "Install it from https://www.python.org/downloads/macos/ (the big yellow Download button),"
  echo "then open this again."
  open "https://www.python.org/downloads/macos/" 2>/dev/null
  read -p "Press Enter to close..."
  exit 1
fi
echo "Using $("$PY" --version) at $PY"

# 2. Create the private environment (re-create it if it is broken or from an old Python)
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
  echo "Setting up (first time only, about a minute)..."
  rm -rf .venv
  "$PY" -m venv .venv || { echo "Could not create the environment."; read -p "Press Enter to close..."; exit 1; }
fi

# 3. Install / update packages
echo "Checking for updates..."
.venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1
if ! .venv/bin/python -m pip install -q --upgrade -r requirements.txt; then
  echo ""
  echo "Installing failed. Check your internet connection and try again."
  read -p "Press Enter to close..."
  exit 1
fi

# 4. Free port 5757 if a stuck old copy is holding it
lsof -ti :5757 2>/dev/null | xargs kill -9 2>/dev/null

# 5. Run
.venv/bin/python app.py
