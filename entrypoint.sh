#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data
exec python -m app.main
