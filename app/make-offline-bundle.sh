#!/usr/bin/env bash
#
# Builds wheelhouse/ so the portal can be installed on a server with no
# internet access.
#
# Run this on a machine that HAS internet and runs the SAME operating system
# and Python version as the target server -- Python wheels are built per
# platform, so a bundle made on macOS will not install on Linux.
#
#   ./make-offline-bundle.sh
#   # then copy this whole folder to the server and run ./install.sh there

set -Eeuo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQS="$DIR/requirements.txt"
if [ "${1:-}" = "--postgres" ]; then
  REQS="$DIR/requirements-postgres.txt"
elif [ -n "${1:-}" ]; then
  echo "Usage: ./make-offline-bundle.sh [--postgres]" >&2
  exit 1
fi

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && \
     "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.11 or newer is required." >&2; exit 1; }

echo "Building the offline bundle with $("$PY" --version 2>&1) on $(uname -s)/$(uname -m)"
rm -rf "$DIR/wheelhouse"
mkdir -p "$DIR/wheelhouse"
"$PY" -m pip download --dest "$DIR/wheelhouse" --only-binary=psycopg2-binary -r "$REQS"

cat > "$DIR/wheelhouse/BUILT-ON.txt" <<TXT
Built $(date '+%Y-%m-%d %H:%M')
Python  $("$PY" --version 2>&1)
System  $(uname -s) $(uname -m)

install.sh uses these files automatically. They only work on a server with the
same operating system and Python version as shown above.
TXT

COUNT=$(find "$DIR/wheelhouse" -name '*.whl' -o -name '*.tar.gz' | wc -l | tr -d ' ')
SIZE=$(du -sh "$DIR/wheelhouse" | cut -f1)
echo
echo "Done: $COUNT packages, $SIZE in wheelhouse/"
echo "Copy this whole folder to the server, then run ./install.sh there."
