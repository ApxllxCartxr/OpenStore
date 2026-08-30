#!/usr/bin/env bash
# Bring up the entire OpenStore demo environment with one command.
# Re-registers the Razorpay webhook against the fresh cloudflared tunnel URL
# every time, so the live demo never silently hits a dead webhook URL.
set -e
cd "$(dirname "$0")/.."

# Point at a specific merchant config to demo genericity, e.g.:
#   MERCHANT_CONFIG_PATH=config/chai.yaml ./scripts/dev_up.sh
export MERCHANT_CONFIG_PATH="${MERCHANT_CONFIG_PATH:-config/gelateria.yaml}"

exec uv run python scripts/dev_up.py "$@"
