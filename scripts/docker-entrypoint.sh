#!/bin/sh
# Create the demo database if the volume is empty, then run the service command.
# All services share one volume, so the check runs under a lock: only one of them seeds.
set -e
mkdir -p "$(dirname "$PORTFOLIO_DB_PATH")" "$PORTFOLIO_BACKUP_DIR"
flock "$(dirname "$PORTFOLIO_DB_PATH")/.init.lock" python -m shared.seed
exec "$@"
