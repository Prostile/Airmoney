#!/usr/bin/env sh
set -eu

mkdir -p /app/data
python -m airmoney init-db

exec "$@"
