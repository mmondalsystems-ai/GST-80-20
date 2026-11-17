#!/usr/bin/env bash
#
# Finance Operations Portal — start it on this machine for a demo.
#
#   ./demo.sh              set up if needed, then start and open the browser
#   ./demo.sh --seed       the same, but with July 2026 already calculated
#   ./demo.sh --reset      throw away the demo database and start fresh
#   ./demo.sh --port 9000  use a different port
#
# No administrator rights, no database server, nothing installed system-wide.
# Everything lives in this folder and is removed when you delete it.

set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
APP_DIR="$PWD"

PORT=8080
SEED=0
RESET=0

while [ $# -gt 0 ]; do
  case "$1" in
    --seed)   SEED=1 ;;
    --reset)  RESET=1 ;;
    --port)   PORT="${2:-}"; shift ;;
    --port=*) PORT="${1#*=}" ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option '$1'. Try --help." >&2; exit 1 ;;
  esac
  shift
done

if [ -t 1 ]; then B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'
else B=""; D=""; G=""; R=""; N=""; fi
say()  { printf "%s==>%s %s\n" "$G" "$N" "$1"; }
fail() { printf "\n%sCannot start:%s %s\n\n" "$R" "$N" "$1" >&2; exit 1; }

printf "\n%sFinance Operations Portal — demo%s\n\n" "$B" "$N"

# ------------------------------------------------------------------ python
PY=""
for c in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || fail "Python 3.11 or newer is needed and was not found.
    macOS:          brew install python@3.12
    Ubuntu/Debian:  sudo apt install python3 python3-venv"

# ------------------------------------------------------------------- reset
if [ "$RESET" = "1" ]; then
  say "Clearing the demo database"
  rm -rf "$APP_DIR/var"
fi

# --------------------------------------------------------------- one-time
VENV_PY="$APP_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  say "First run — setting up (about a minute)"
  "$PY" -m venv "$APP_DIR/.venv" || fail "could not create the Python environment.
    On Ubuntu/Debian this usually means:  sudo apt install python3-venv"
  if [ -d "$APP_DIR/wheelhouse" ] && [ -n "$(ls -A "$APP_DIR/wheelhouse" 2>/dev/null)" ]; then
    "$VENV_PY" -m pip install -q --no-index --find-links "$APP_DIR/wheelhouse" \
        -r "$APP_DIR/requirements.txt" || fail "the offline bundle does not match this machine."
  else
    "$VENV_PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
    "$VENV_PY" -m pip install -q -r "$APP_DIR/requirements.txt" \
      || fail "could not download the dependencies. Check this machine's internet connection."
  fi
else
  say "Environment ready"
fi

# A demo uses a local file for its database, so there is nothing to install.
if [ ! -f "$APP_DIR/.env" ]; then
  {
    echo "# Demo configuration. Uses a local file for the database."
    echo "ORG_NAME=Oswal Group"
    echo "BOOTSTRAP_ADMIN_EMAIL=admin@oswalgroup.net"
    echo "BOOTSTRAP_ADMIN_PASSWORD=demo1234"
  } > "$APP_DIR/.env"
fi

# ------------------------------------------------------------------- seed
if [ "$SEED" = "1" ]; then
  say "Loading July 2026 so the portal opens with figures already in it"
  "$VENV_PY" seed_demo.py || fail "the sample month could not be loaded. See the message above."
else
  "$VENV_PY" -c "from app.main import startup; startup()" >/dev/null 2>&1 || \
    fail "the application could not start. Try:  ./demo.sh --reset"
fi

# ------------------------------------------------------------------ start
URL="http://127.0.0.1:${PORT}"

printf "\n%s" "$G"; printf '%.0s─' $(seq 1 60); printf "%s\n" "$N"
printf "  %sOpen%s   %s\n" "$B" "$N" "$URL"
printf "  %sSign in%s  admin@oswalgroup.net  /  demo1234\n" "$B" "$N"
if [ "$SEED" = "1" ]; then
  printf "  %sReady%s   July 2026 is already loaded\n" "$D" "$N"
else
  printf "  %sTo demo%s  GST 80:20 → New calculation → upload the three files\n" "$D" "$N"
  printf "           in %sdemo-inputs/%s\n" "$B" "$N"
fi
printf "\n  %sPress Ctrl+C in this window to stop.%s\n" "$D" "$N"
printf "%s" "$G"; printf '%.0s─' $(seq 1 60); printf "%s\n\n" "$N"

# Open the browser once the server is actually answering.
(
  for _ in $(seq 1 40); do
    sleep 0.5
    if "$VENV_PY" - "$PORT" <<'EOF' 2>/dev/null
import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=1)
except Exception:
    sys.exit(1)
EOF
    then
      case "$(uname -s)" in
        Darwin) command -v open     >/dev/null 2>&1 && open     "$URL" >/dev/null 2>&1 ;;
        *)      command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" >/dev/null 2>&1 ;;
      esac || true
      break
    fi
  done
) &

exec "$APP_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning
