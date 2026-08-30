"""dev_up.py — single-command demo environment bring-up.

Brings up the whole OpenStore demo in a fixed order so there is nothing to
remember manually under live-demo pressure:

    1. cloudflared tunnel  -> captures the random *.trycloudflare.com URL
    2. register that URL as the Razorpay webhook endpoint (replacing any stale one)
    3. merchant server (FastAPI + MCP)
    4. notifier bot (Discord approval/OTP)
    5. merchant reasoning agent
    6. buyer agent (Discord.py + LangGraph)

Risk R4 (tunnel URL churn) is solved here: every run re-registers the webhook
against the freshly assigned tunnel URL, so Razorpay can never be pointing at a
dead URL left over from a previous session.
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import time

from merchant.config import settings
from merchant.razorpay_client import _get_client

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
WEBHOOK_EVENTS = [
    "payment_link.paid",
    "payment.captured",
    "refund.created",
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_tunnel_and_register_webhook(local_port: int) -> str:
    """Launch `cloudflared tunnel --url ...` and capture its assigned URL, then
    (re)register it as the Razorpay webhook endpoint. Returns the tunnel URL."""
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{local_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tunnel_url = None
    for _ in range(60):
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            break
        m = TUNNEL_RE.search(line)
        if m:
            tunnel_url = m.group(0)
            break
    if not tunnel_url:
        raise RuntimeError("cloudflared did not emit a tunnel URL (is cloudflared installed?)")
    print(f"[dev_up] tunnel up: {tunnel_url}")

    _register_webhook(tunnel_url)
    return tunnel_url


def _register_webhook(tunnel_url: str) -> None:
    webhook_url = f"{tunnel_url}/webhooks/razorpay"
    if not settings.razorpay_key_id:
        print("[dev_up] no Razorpay credentials — skipping webhook registration")
        return
    client = _get_client()
    try:
        existing = client.webhook.all().get("items", [])
    except Exception as e:
        print(f"[dev_up] could not list webhooks: {e}")
        return

    for wh in existing:
        if wh.get("url") == webhook_url:
            print("[dev_up] webhook already registered for this tunnel URL")
            return
    # Re-point the first existing webhook at the new URL instead of creating
    # a duplicate that would leave stale tunnels subscribed.
    if existing:
        wh_id = existing[0]["id"]
        try:
            client.webhook.edit(wh_id, {"url": webhook_url})
            print(f"[dev_up] updated existing webhook {wh_id} -> {webhook_url}")
            return
        except Exception as e:
            print(f"[dev_up] webhook edit failed: {e}")
    try:
        client.webhook.create({
            "url": webhook_url,
            "events": WEBHOOK_EVENTS,
            "secret": settings.razorpay_webhook_secret or None,
        })
        print(f"[dev_up] created webhook -> {webhook_url}")
    except Exception as e:
        print(f"[dev_up] webhook create failed: {e}")


def _spawn(name: str, cmd: list) -> subprocess.Popen:
    p = subprocess.Popen(cmd, cwd=ROOT)
    print(f"[dev_up] started {name} (pid {p.pid})")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-tunnel", action="store_true", help="skip cloudflared + webhook")
    args = ap.parse_args()

    procs = []
    if not args.no_tunnel:
        start_tunnel_and_register_webhook(args.port)

    procs.append(_spawn("merchant", [
        sys.executable, "-m", "uvicorn", "merchant.app:app",
        "--port", str(args.port), "--reload",
    ]))
    time.sleep(2)
    procs.append(_spawn("notifier", [sys.executable, "-m", "merchant.notifier"]))
    procs.append(_spawn("merchant-agent", [sys.executable, "-m", "merchant_agent.app"]))
    procs.append(_spawn("buyer-agent", [sys.executable, "-m", "buyer_agent.bot"]))

    print("[dev_up] all processes up. Ctrl-C to stop.")
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.send_signal(signal.SIGINT)
        for p in procs:
            p.wait()


if __name__ == "__main__":
    main()
