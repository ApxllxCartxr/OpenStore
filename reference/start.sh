#!/usr/bin/env bash
# reference/start.sh — start the reference project on all interfaces + cloudflared tunnel
#
# Starts:
#   1. Merchant server  (reference.merchant) on 0.0.0.0:8000 — storefront + MCP + OAuth + webhooks
#   2. Merchant agent   (reference.merchant_agent) on 0.0.0.0:8001 — A2A reasoning (cross_sell, campaign_draft, finance_qa)
#   3. Buyer agent      (reference.buyer_agent) — Discord bot (only if DISCORD_BOT_TOKEN is set, or --buyer)
#   4. cloudflared quick tunnel -> http://localhost:8000  (prints a https://*.trycloudflare.com URL you can paste into dashboards)
#
# Usage:
#   ./reference/start.sh                          # all surfaces + tunnel, buyer only if token present
#   ./reference/start.sh --no-tunnel              # skip tunnel
#   ./reference/start.sh --no-buyer               # skip buyer bot even if token is set
#   ./reference/start.sh --port 8000 --agent-port 8001
#   MERCHANT_CONFIG_PATH=config/chai.yaml ./reference/start.sh
#
# The tunnel URL is written to reference/.tunnel_url and printed — paste
#   <tunnel_url>/webhooks/razorpay
# into your Razorpay dashboard (or any dashboard that needs the public backend URL).
# The URL changes every run (quick tunnel) — re-paste after each restart.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT=8000
AGENT_PORT=8001
DO_TUNNEL=1
DO_BUYER=auto   # auto | 1 | 0
TUNNEL_LOG="/tmp/openstore-cloudflared.log"
TUNNEL_URL_FILE="reference/.tunnel_url"

PORT=8000; AGENT_PORT=8001; DO_TUNNEL=1; DO_BUYER=auto
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --port=*) PORT="${1#--port=}"; shift ;;
    --agent-port) AGENT_PORT="$2"; shift 2 ;;
    --agent-port=*) AGENT_PORT="${1#--agent-port=}"; shift ;;
    --no-tunnel) DO_TUNNEL=0; shift ;;
    --no-buyer) DO_BUYER=0; shift ;;
    --buyer) DO_BUYER=1; shift ;;
    --help|-h) echo "Usage: $0 [--port 8000] [--agent-port 8001] [--no-tunnel] [--no-buyer|--buyer]"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

PIDS=()
cleanup() {
  echo ""
  echo "[start] shutting down..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  # also kill cloudflared if we started it
  if [[ -n "${TUNNEL_PID:-}" ]]; then
    kill "$TUNNEL_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  echo "[start] done."
}
trap cleanup INT TERM EXIT

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[start] missing required command: $1" >&2
    if [[ "$1" == "cloudflared" ]]; then
      echo "  install: https://developers.cloudflare.com/cloudflare-one/connections/connect/networks/downloads/" >&2
      echo "  or: brew install cloudflared  /  yay -S cloudflared  /  https://github.com/cloudflare/cloudflared/releases" >&2
    fi
    return 1
  fi
}

echo "[start] OpenStore reference — all surfaces (0.0.0.0) + cloudflared tunnel"
echo "[start] root: $ROOT"
echo "[start] merchant:        0.0.0.0:$PORT  (reference.merchant)"
echo "[start] merchant-agent:  0.0.0.0:$AGENT_PORT  (reference.merchant_agent)"
if [[ "$DO_TUNNEL" == "1" ]]; then echo "[start] tunnel:            http://localhost:$PORT -> https://*.trycloudflare.com"; else echo "[start] tunnel:            skipped (--no-tunnel)"; fi

# --- 1. merchant ---
echo "[start] → merchant server..."
uv run uvicorn reference.merchant.app:app --host 0.0.0.0 --port "$PORT" --reload &
PIDS+=($!)
sleep 2
if ! kill -0 "${PIDS[-1]}" 2>/dev/null; then echo "[start] merchant failed to start" >&2; exit 1; fi
echo "[start]   merchant pid ${PIDS[-1]} — http://localhost:$PORT/health"

# --- 2. merchant agent ---
echo "[start] → merchant reasoning agent..."
uv run uvicorn reference.merchant_agent.app:app --host 0.0.0.0 --port "$AGENT_PORT" --reload &
PIDS+=($!)
sleep 1
if ! kill -0 "${PIDS[-1]}" 2>/dev/null; then echo "[start] merchant-agent failed to start" >&2; exit 1; fi
echo "[start]   merchant-agent pid ${PIDS[-1]} — http://localhost:$AGENT_PORT/health  (.well-known/agent-card.json)"

# --- 3. buyer agent (optional) ---
SHOULD_START_BUYER=0
if [[ "$DO_BUYER" == "1" ]]; then SHOULD_START_BUYER=1
elif [[ "$DO_BUYER" == "auto" ]]; then
  if [[ -n "${DISCORD_BOT_TOKEN:-}" ]] || grep -q "DISCORD_BOT_TOKEN" .env 2>/dev/null; then
    SHOULD_START_BUYER=1
  fi
fi
if [[ "$SHOULD_START_BUYER" == "1" ]]; then
  echo "[start] → buyer agent (Discord)..."
  uv run python -m reference.buyer_agent.bot &
  PIDS+=($!)
  echo "[start]   buyer-agent pid ${PIDS[-1]}"
else
  echo "[start]   buyer agent skipped (no DISCORD_BOT_TOKEN; use --buyer to force)"
fi

# --- 4. cloudflared tunnel for the backend ---
if [[ "$DO_TUNNEL" == "1" ]]; then
  if ! need_cmd cloudflared; then
    echo "[start] tunnel skipped — install cloudflared and re-run, or use --no-tunnel"
  else
    echo "[start] → cloudflared quick tunnel for http://localhost:$PORT ..."
    rm -f "$TUNNEL_LOG" "$TUNNEL_URL_FILE"
    # cloudflared prints the URL to stderr, so capture both
    cloudflared tunnel --url "http://localhost:$PORT" 2>&1 | tee "$TUNNEL_LOG" &
    TUNNEL_PID=$!
    PIDS+=($TUNNEL_PID)

    echo "[start]   waiting for tunnel URL (up to 30s)..."
    TUNNEL_URL=""
    for i in $(seq 1 30); do
      if [[ -f "$TUNNEL_LOG" ]]; then
        TUNNEL_URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n1 || true)
        if [[ -n "$TUNNEL_URL" ]]; then break; fi
      fi
      sleep 1
    done

    if [[ -z "$TUNNEL_URL" ]]; then
      echo "[start] ⚠ cloudflared did not emit a URL in 30s — check $TUNNEL_LOG"
      echo "      tail -f $TUNNEL_LOG"
    else
      echo "$TUNNEL_URL" > "$TUNNEL_URL_FILE"
      echo ""
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo "  TUNNEL UP: $TUNNEL_URL"
      echo "  (also written to $TUNNEL_URL_FILE)"
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo "  Paste into your dashboard:"
      echo "    • Razorpay webhook URL:  $TUNNEL_URL/webhooks/razorpay"
      echo "    • Merchant base URL:     $TUNNEL_URL"
      echo "    • Agent commerce doc:    $TUNNEL_URL/.well-known/agent-commerce.json"
      echo "    • Health check:          $TUNNEL_URL/health"
      echo ""
      echo "  This is a quick tunnel — the URL changes every restart."
      echo "  Re-paste the new URL after each ./reference/start.sh run."
      echo "  (Razorpay: Dashboard → Developers → Webhooks → Edit → paste above → enable payment_link.paid)"
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo ""
    fi
  fi
fi

echo ""
echo "[start] all surfaces up. Ctrl-C to stop."
echo "  storefront:      http://localhost:$PORT/  (or \$TUNNEL_URL/ via tunnel)"
echo "  agent card:      http://localhost:$AGENT_PORT/.well-known/agent-card.json"
echo "  health:          http://localhost:$PORT/health  +  http://localhost:$AGENT_PORT/health"
echo ""

# Wait on all background jobs; trap handles cleanup
wait
