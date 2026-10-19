#!/bin/sh
# Drop privileges to PUID:PGID so downloaded files land on the NAS share owned
# by a real user instead of root.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    groupmod -o -g "$PGID" appuser 2>/dev/null || true
    usermod  -o -u "$PUID" appuser 2>/dev/null || true

    mkdir -p "$CHANRIPPER_CONFIG_DIR" "$CHANRIPPER_DOWNLOAD_DIR"

    # Deliberately NOT recursive on the download dir: chown -R over a large NAS
    # share on every start is slow and can touch files that were never ours.
    # New files inherit the process's uid/gid, so the directory itself is enough.
    chown "$PUID:$PGID" "$CHANRIPPER_CONFIG_DIR" 2>/dev/null || true
    chown "$PUID:$PGID" "$CHANRIPPER_DOWNLOAD_DIR" 2>/dev/null || true
    chown -R "$PUID:$PGID" "$CHANRIPPER_CONFIG_DIR" 2>/dev/null || true

    echo "chanripper: starting as uid=$PUID gid=$PGID, config=$CHANRIPPER_CONFIG_DIR downloads=$CHANRIPPER_DOWNLOAD_DIR"
    exec gosu "$PUID:$PGID" "$@"
fi

echo "chanripper: starting as uid=$(id -u) gid=$(id -g)"
exec "$@"
