#!/bin/sh
set -e

# Default script to run. Set SCRIPT environment variable to choose another.
: "${SCRIPT:=ragrunbook.py}"

echo "Starting container, running script: $SCRIPT"

exec python -u "/app/$SCRIPT" "$@"
