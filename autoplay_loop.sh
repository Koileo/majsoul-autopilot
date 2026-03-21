#!/bin/bash
# Wrapper script: restarts run_autoplay.py automatically after every N games.
# Usage: ./autoplay_loop.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COOLDOWN=10  # seconds between restarts

while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') | Starting run_autoplay.py ..."
    python3 "$SCRIPT_DIR/run_autoplay.py"
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 130 ]; then
        # Ctrl+C (SIGINT) — user wants to stop
        echo "User interrupted, exiting."
        break
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') | run_autoplay.py exited (code $EXIT_CODE), restarting in ${COOLDOWN}s..."
    sleep $COOLDOWN
done
