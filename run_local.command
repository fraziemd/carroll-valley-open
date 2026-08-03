#!/bin/bash
# JDCVO 2026 - local fallback launcher.
#
# WHAT THIS IS: a panic button. If Streamlit Cloud or GitHub is down,
# suspended, or you just want to run everything on your own machine,
# double-click this file (in Finder) or run  ./run_local.command  in a
# terminal. It starts the full app - public leaderboard AND admin - on THIS
# computer, with no dependence on any cloud service.
#
# Your data still lives in Google Sheets when reachable; if it isn't, the app
# automatically falls back to the local snapshots in data_2026/.

cd "$(dirname "$0")" || exit 1
PORT=8501

pause_exit() { echo; read -r -p "Press Return to close this window. " _; exit "${1:-1}"; }

echo "======================================================"
echo "  JDCVO 2026 - running the scoring app on THIS computer"
echo "======================================================"
echo "Folder: $(pwd)"
echo

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "ERROR: Python 3 isn't installed on this machine."
  echo "Install it from https://www.python.org/downloads/ then double-click this again."
  pause_exit 1
fi

# Only installs on the first run (or a fresh machine); skipped once present.
if ! "$PY" -c "import streamlit" >/dev/null 2>&1; then
  echo "First run: installing dependencies (needs internet, about a minute)..."
  "$PY" -m pip install -r requirements_2026.txt \
    || "$PY" -m pip install --user -r requirements_2026.txt \
    || { echo "ERROR: could not install dependencies. See requirements_2026.txt."; pause_exit 1; }
  echo
fi

if [ ! -f ".streamlit/secrets.toml" ]; then
  echo "NOTE: .streamlit/secrets.toml was not found."
  echo "      The app will still run using the local data in data_2026/, but the"
  echo "      admin password and Google Sheets sync may be unavailable until it exists."
  echo
fi

echo "Starting the app - a browser tab should open at http://localhost:$PORT"
echo "KEEP THIS WINDOW OPEN while you use the app."
echo "To stop the app: close this window, or press Ctrl-C."
echo
exec "$PY" -m streamlit run app_2026.py --server.port "$PORT"
