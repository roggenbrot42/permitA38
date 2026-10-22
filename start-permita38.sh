#!/bin/sh
# Creates a virtualenv on first run, installs permitA38 and starts it.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/permita38 ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e .
fi
exec .venv/bin/permita38 "$@"
