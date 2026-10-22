#!/bin/sh
# Starts permitA38 and opens it in your browser. Pass options such as --data ~/Sync/permitA38
exec "$(dirname "$(readlink -f "$0")")/permita38" "$@"
