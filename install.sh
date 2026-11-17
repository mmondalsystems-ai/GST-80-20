#!/usr/bin/env bash
#
# Finance Operations Portal — installer for Linux and macOS.
#
#   sudo ./install.sh                 install and start as a service
#   ./install.sh --no-service         install and print how to start it by hand
#   ./install.sh --sqlite             skip PostgreSQL, use a local file
#   ./install.sh --port 9000          serve on a different port
#   ./install.sh --uninstall          stop and remove the service (keeps data)
#
# Safe to run more than once: it upgrades an existing install in place and
# never touches the database contents.

set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="finops"
PORT=8080
USE_SQLITE=0
INSTALL_SERVICE=1
UNINSTALL=0
DB_NAME="finops"
DB_USER="finops"

# ---------------------------------------------------------------- output ---
if [ -t 1 ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'
else
  B=""; DIM=""; G=""; Y=""; R=""; N=""
fi
step() { printf "\n%s==>%s %s%s%s\n" "$G" "$N" "$B" "$1" "$N"; }
info() { printf "    %s\n" "$1"; }
warn() { printf "    %s! %s%s\n" "$Y" "$1" "$N"; }
fail() { printf "\n%sInstallation stopped:%s %s\n\n" "$R" "$N" "$1" >&2; exit 1; }

trap 'fail "the step above did not complete. Nothing further was changed."' ERR

while [ $# -gt 0 ]; do
  case "$1" in
    --sqlite)     USE_SQLITE=1 ;;
    --no-service) INSTALL_SERVICE=0 ;;
    --uninstall)  UNINSTALL=1 ;;
    --port)       PORT="${2:-}"; shift ;;
    --port=*)     PORT="${1#*=}" ;;
    -h|--help)    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            fail "unknown option '$1'. Run with --help to see the options." ;;
  esac
  shift
done

# ------------------------------------------------------------- uninstall ---
if [ "$UNINSTALL" = "1" ]; then
  step "Removing the service"
  if command -v systemctl >/dev/null 2>&1 && [ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    info "Service removed."
  else
    info "No service was installed."
  fi
  info "Your data has been left alone: the database, ${APP_DIR}/var and .env are untouched."
  exit 0
fi

printf "\n%sFinance Operations Portal — installer%s\n" "$B" "$N"
printf "%sInstalling into %s%s\n" "$DIM" "$APP_DIR" "$N"

# ------------------------------------------------------------- python ------
step "Checking Python"
PY=""
for c in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
[ -n "$PY" ] || fail "Python 3.11 or newer is required and was not found.
    Install it, then run this script again:
      Ubuntu/Debian   sudo apt install python3 python3-venv
      RHEL/Rocky      sudo dnf install python3
      macOS           brew install python@3.12"
info "Using $("$PY" --version 2>&1) at $(command -v "$PY")"

if ! "$PY" -c 'import venv' >/dev/null 2>&1; then
  fail "Python is missing the 'venv' module.
    On Ubuntu/Debian:  sudo apt install python3-venv"
fi

# -------------------------------------------------------- virtualenv -------
step "Setting up the Python environment"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  "$PY" -m venv "$APP_DIR/.venv"
  info "Created .venv"
else
  info "Reusing the existing .venv"
fi
VENV_PY="$APP_DIR/.venv/bin/python"

step "Installing dependencies"
if [ -d "$APP_DIR/wheelhouse" ] && [ -n "$(ls -A "$APP_DIR/wheelhouse" 2>/dev/null)" ]; then
  info "Found wheelhouse/ — installing without touching the internet"
  "$VENV_PY" -m pip install --quiet --upgrade --no-index --find-links "$APP_DIR/wheelhouse" pip \
      >/dev/null 2>&1 || true
  "$VENV_PY" -m pip install --quiet --no-index --find-links "$APP_DIR/wheelhouse" \
      -r "$APP_DIR/requirements.txt" \
    || fail "the offline bundle in wheelhouse/ does not cover this machine.
    It must be built on the same operating system and Python version as this server.
    See 'Air-gapped servers' in DEPLOYMENT.md."
else
  "$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$VENV_PY" -m pip install --quiet -r "$APP_DIR/requirements.txt" \
    || fail "could not download the dependencies.
    If this server has no internet access, build an offline bundle on a machine that does:
      ./make-offline-bundle.sh
    copy the whole folder across, and run this installer again."
fi
info "Dependencies installed"

# ---------------------------------------------------------- database -------
# An existing .env is the authority. Re-running the installer must not reset a
# database password that the current .env still refers to -- that would leave a
# working install unable to authenticate.
DB_URL=""
EXISTING_DB_URL=""
if [ -f "$APP_DIR/.env" ]; then
  EXISTING_DB_URL="$(sed -n 's/^DATABASE_URL=\(.*\)$/\1/p' "$APP_DIR/.env" | tail -1)"
fi

if [ -n "$EXISTING_DB_URL" ]; then
  step "Using the database already configured in .env"
  info "${EXISTING_DB_URL%%:*}: $(echo "$EXISTING_DB_URL" | sed 's#.*@##')"
  info "Delete DATABASE_URL from .env if you want the installer to set one up again."
elif [ "$USE_SQLITE" = "0" ]; then
  step "Setting up PostgreSQL"
  if command -v psql >/dev/null 2>&1; then
    DB_PASS="$("$VENV_PY" -c 'import secrets;print(secrets.token_urlsafe(24))')"
    PSQL=""
    if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
      PSQL="su postgres -c"
    elif psql -U postgres -c 'select 1' >/dev/null 2>&1; then
      PSQL="direct"
    fi

    run_sql() {
      if [ "$PSQL" = "direct" ]; then psql -U postgres -tAc "$1" 2>/dev/null
      else $PSQL "psql -tAc \"$1\"" 2>/dev/null; fi
    }
    # The same, but connected to the application's own database.
    run_sql_db() {
      if [ "$PSQL" = "direct" ]; then psql -U postgres -d "$DB_NAME" -tAc "$1" 2>/dev/null
      else $PSQL "psql -d ${DB_NAME} -tAc \"$1\"" 2>/dev/null; fi
    }

    if [ -n "$PSQL" ] && run_sql "select 1" >/dev/null 2>&1; then
      if [ "$(run_sql "select 1 from pg_roles where rolname='${DB_USER}'")" = "1" ]; then
        run_sql "ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}'" >/dev/null
        info "Reset the password for the existing '${DB_USER}' role"
      else
        run_sql "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}'" >/dev/null
        info "Created the '${DB_USER}' role"
      fi
      if [ "$(run_sql "select 1 from pg_database where datname='${DB_NAME}'")" != "1" ]; then
        run_sql "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}" >/dev/null
        info "Created the '${DB_NAME}' database"
      else
        info "Using the existing '${DB_NAME}' database"
      fi

      # Make sure the role can actually create its tables. This matters when the
      # database already existed under another owner, and on PostgreSQL 15 and
      # newer, where a role no longer gets CREATE on the public schema for free.
      run_sql "ALTER DATABASE ${DB_NAME} OWNER TO ${DB_USER}" >/dev/null 2>&1 || true
      run_sql "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER}" >/dev/null 2>&1 || true
      run_sql_db "ALTER SCHEMA public OWNER TO ${DB_USER}" >/dev/null 2>&1 || true
      run_sql_db "GRANT ALL ON SCHEMA public TO ${DB_USER}" >/dev/null 2>&1 || true

      # Adopt anything already in the database. Tables created earlier by hand,
      # or by a previous install running as a different role, would otherwise be
      # unreadable to the application even though it could create new ones.
      run_sql_db "DO \$\$ DECLARE r record; BEGIN
          FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
            EXECUTE format('ALTER TABLE public.%I OWNER TO ${DB_USER}', r.tablename);
          END LOOP;
          FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname='public' LOOP
            EXECUTE format('ALTER SEQUENCE public.%I OWNER TO ${DB_USER}', r.sequencename);
          END LOOP;
        END \$\$;" >/dev/null 2>&1 || true
      run_sql_db "GRANT ALL ON ALL TABLES IN SCHEMA public TO ${DB_USER}" >/dev/null 2>&1 || true
      run_sql_db "GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO ${DB_USER}" >/dev/null 2>&1 || true

      if [ "$(run_sql_db "select has_schema_privilege('${DB_USER}','public','CREATE')")" != "t" ]; then
        warn "The '${DB_USER}' role cannot create tables in the '${DB_NAME}' database."
        info "A database administrator needs to run:"
        info "  GRANT ALL ON SCHEMA public TO ${DB_USER};"
        info "Falling back to SQLite so the portal works in the meantime."
      else
        DB_URL="postgresql+psycopg2://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}"
      fi
    else
      warn "PostgreSQL is installed but this script cannot administer it."
      info "Falling back to a local SQLite file so the portal works now."
      info "To switch later: create the database by hand (DEPLOYMENT.md section 2),"
      info "put its URL in DATABASE_URL in .env, and restart."
    fi
  else
    warn "PostgreSQL was not found on this machine."
    info "Falling back to a local SQLite file, which suits a pilot or a single preparer."
    info "For several people at once, install PostgreSQL and set DATABASE_URL in .env."
  fi
elif [ "$USE_SQLITE" = "1" ]; then
  step "Using SQLite as requested"
fi

# ------------------------------------------------------------ .env ---------
step "Writing configuration"
ENV_FILE="$APP_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$ENV_FILE.backup"
  info "Kept your existing .env (a copy is at .env.backup)"
  if [ -n "$DB_URL" ] && ! grep -q '^DATABASE_URL=.\+' "$ENV_FILE"; then
    printf 'DATABASE_URL=%s\n' "$DB_URL" >> "$ENV_FILE"
  fi
else
  SECRET="$("$VENV_PY" -c 'import secrets;print(secrets.token_urlsafe(48))')"
  {
    echo "# Written by install.sh on $(date '+%Y-%m-%d %H:%M'). Safe to edit."
    if [ -n "$DB_URL" ]; then echo "DATABASE_URL=$DB_URL"
    else echo "# No DATABASE_URL set, so the portal uses var/finops.db (SQLite)."; fi
    echo "SECRET_KEY=$SECRET"
    echo "SESSION_HTTPS_ONLY=0        # set to 1 once the site is served over HTTPS"
    echo "ORG_NAME=Oswal Group"
    echo "BOOTSTRAP_ADMIN_EMAIL=admin@oswalgroup.net"
    echo "# The first password is generated on first start and printed below."
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  info "Created .env"
fi

# ------------------------------------------------------ first start --------
cd "$APP_DIR"
DATABASE_KIND="$("$VENV_PY" -m app.deploy_check database-kind)"
if [ "$DATABASE_KIND" = "postgresql" ]; then
  step "Installing optional PostgreSQL driver"
  DRIVER_ARGS=(--only-binary=psycopg2-binary -r "$APP_DIR/requirements-postgres.txt")
  if [ -d "$APP_DIR/wheelhouse" ] && [ -n "$(ls -A "$APP_DIR/wheelhouse" 2>/dev/null)" ]; then
    DRIVER_ARGS+=(--no-index --find-links "$APP_DIR/wheelhouse")
  fi
  "$VENV_PY" -m pip install --quiet "${DRIVER_ARGS[@]}" \
    || fail "No compatible PostgreSQL binary driver is available. Check Python/platform and package access, or use SQLite."
else
  info "SQLite: PostgreSQL driver installation skipped."
fi

step "Preparing the database and the first administrator"
cd "$APP_DIR"
FIRST_RUN_LOG="$APP_DIR/var/install-first-run.log"
mkdir -p "$APP_DIR/var"
set +e
"$VENV_PY" - >"$FIRST_RUN_LOG" 2>&1 <<'PYEOF'
from app.main import startup
startup()
PYEOF
RC=$?
set -e
if [ $RC -ne 0 ]; then
  printf "\n%s%s\n" "$DIM" "$(tail -n 15 "$FIRST_RUN_LOG")"; printf "%s" "$N"
  fail "the database could not be prepared. The output above says why.
    The full log is at $FIRST_RUN_LOG"
fi
grep -E "Email:|Password:|Database:" "$FIRST_RUN_LOG" | sed 's/^ */    /' || true

# ------------------------------------------------------- smoke test --------
step "Checking that it serves"
"$APP_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" \
    >"$APP_DIR/var/install-smoke.log" 2>&1 &
SMOKE_PID=$!
OK=0
for _ in $(seq 1 40); do
  sleep 0.5
  if "$VENV_PY" - "$PORT" <<'PYEOF' 2>/dev/null
import sys, urllib.request
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=2) as r:
        sys.exit(0 if r.read().decode().strip() == "ok" else 1)
except Exception:
    sys.exit(1)
PYEOF
  then OK=1; break; fi
done
kill "$SMOKE_PID" 2>/dev/null || true
wait "$SMOKE_PID" 2>/dev/null || true
if [ "$OK" != "1" ]; then
  printf "\n%s%s\n" "$DIM" "$(tail -n 15 "$APP_DIR/var/install-smoke.log")"; printf "%s" "$N"
  fail "the portal did not answer on port $PORT. The output above says why."
fi
info "Answered on port $PORT"

# ---------------------------------------------------------- service --------
STARTED_SERVICE=0
if [ "$INSTALL_SERVICE" = "1" ]; then
  step "Registering the service"
  if [ "$(id -u)" != "0" ]; then
    warn "Not running as root, so no service was registered."
    info "Re-run with sudo to have it start automatically with the machine."
  elif ! command -v systemctl >/dev/null 2>&1; then
    warn "This machine does not use systemd, so no service was registered."
  else
    SVC_USER="${SUDO_USER:-root}"
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Finance Operations Portal
After=network.target postgresql.service

[Service]
User=${SVC_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 4 --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    chown -R "$SVC_USER" "$APP_DIR/var" 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
      STARTED_SERVICE=1
      info "Service '${SERVICE_NAME}' is running and will start with the machine"
    else
      warn "The service was registered but is not running."
      info "See what happened with:  journalctl -u ${SERVICE_NAME} -n 40"
    fi
  fi
fi

# ------------------------------------------------------------ finish -------
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$HOST_IP" ] || HOST_IP="127.0.0.1"

printf "\n%s" "$G"
printf '%.0s─' $(seq 1 64); printf "%s\n" "$N"
printf "%s  Installed.%s\n\n" "$B" "$N"
if [ "$STARTED_SERVICE" = "1" ]; then
  printf "  Open   %shttp://%s:%s%s\n" "$B" "$HOST_IP" "$PORT" "$N"
  printf "  %sManage with: systemctl {status|restart|stop} %s%s\n" "$DIM" "$SERVICE_NAME" "$N"
else
  printf "  Start it with:\n\n    cd %s && ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port %s\n\n" \
         "$APP_DIR" "$PORT"
  printf "  Then open %shttp://%s:%s%s\n" "$B" "$HOST_IP" "$PORT" "$N"
fi
printf "\n  Sign in with the email and password shown above.\n"
printf "  %sChange that password under Administration → Users straight away.%s\n" "$DIM" "$N"
printf "\n  %sNext:%s serve it over HTTPS behind nginx — see section 4 of DEPLOYMENT.md.\n" "$B" "$N"
printf "%s" "$G"; printf '%.0s─' $(seq 1 64); printf "%s\n\n" "$N"
