#!/usr/bin/env bash
# Start permitA38 from this checkout as a local preview on http://127.0.0.1:8765 with FAKE demo data.
# The data lives in ~/permitA38-preview (override with PREVIEW_DIR); your real config and data are not touched.
# Reset: delete that folder. See docs/preview.md.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dir="${PREVIEW_DIR:-$HOME/permitA38-preview}"
mkdir -p "$dir"

export PERMITA38_DATA="$dir"
export PERMITA38_HOST=127.0.0.1
export PERMITA38_PORT="${PERMITA38_PORT:-8765}"
export PERMITA38_IN_CONTAINER=1          # no per-device config.yaml is written; the secret is kept in the data folder
export XDG_CONFIG_HOME="$dir/.config"    # belt and braces: never read or write the real user config
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
py="${PYTHON:-python3}"

if [ ! -e "$dir/.seeded" ]; then
  echo "First run: creating fake demo data in $dir"
  "$py" "$root/scripts/seed_demo.py"
  touch "$dir/.seeded"
fi

echo "permitA38 preview on http://127.0.0.1:$PERMITA38_PORT  (login: demo / demo, local only)"
exec "$py" -m permita38 --no-browser serve
