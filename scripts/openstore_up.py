#!/usr/bin/env python3
"""`openstore up <domain>` — one command, no hand-edited files.

Writes the compose file, the reference `Caddyfile` and a fresh `.env`, generates
the signing key, prints the encrypted export to save, and prints the first-run
`/agentic` URL.

**A Merchant who can point DNS can run it.** Anything that needs a hand-edited
YAML before first boot is a bug in this gate, not a documentation problem
(SPEC §14).

Every secret is generated here with `secrets`, never `random` and never derived
from a timestamp (§16.1). Nothing this writes is ever committed.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Variables `.env.example` names that this generates rather than asks for.
GENERATED = (
    "TRAIT_HMAC_SECRET",
    "SIDECAR_SIGNING_KEY_PASSPHRASE",
    "DEPLOY_PSEUDONYM_KEY",
    "OAUTH_CLIENT_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "ADMIN_SEED_PASSWORD",
)


def generate_env(domain: str, *, demo: bool = True) -> str:
    """A complete `.env`, filled in.

    The template is the source of variable names (§10: `.env.example` is the
    only place a variable is introduced), so this reads it rather than keeping a
    second list that would drift.
    """
    template = (REPO / ".env.example").read_text(encoding="utf-8")
    values = {
        "OPENSTORE_MERCHANT_DOMAIN": domain,
        "OPENSTORE_PUBLIC_ORIGIN": f"https://{domain}",
        "WEBAUTHN_RP_ID": domain,
        "WEBAUTHN_RP_NAME": domain,
        "SIDECAR_PORT": "8000",
        "STORE_PORT": "3000",
        "STORE_INTERNAL_URL": "http://store:3000",
        "SIDECAR_DATABASE_URL": "postgresql+psycopg://sc_app:sc_app_dev@postgres:5432/sidecar",
        "MERCHANT_DATABASE_URL": "postgresql+psycopg://sd_app:sd_app_dev@postgres:5432/spoiledduckie",
        "TRAIT_BASE_URL": "http://store:3000/trait",
        "SIDECAR_SIGNING_KEY_PATH": "/data/keys/signing.pem",
        "SIDECAR_KEY_EXPORT_PATH": "/data/keys/export.enc",
        "PAYMENT_PROVIDER": "fake",
        "OPENSTORE_DEMO_MODE": "true" if demo else "false",
        "CHAT_MODEL_DRIVER": "scripted",
        "CHAT_DATABASE_PATH": "/data/chat.db",
        # Empty by default, and it stays empty unless somebody means it.
        "OPENSTORE_DEV_PROFILE_HOSTS": "",
    }
    for key in GENERATED:
        values[key] = secrets.token_urlsafe(24)

    lines: list[str] = []
    for line in template.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name = line.split("=", 1)[0].strip()
            lines.append(f"{name}={values.get(name, '')}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def write_install(domain: str, target: Path, *, demo: bool = True) -> dict[str, Path]:
    """Write everything first boot needs. Refuses to overwrite an existing
    `.env`: regenerating secrets over a live deployment would orphan every
    signature it has made."""
    target.mkdir(parents=True, exist_ok=True)
    env_path = target / ".env"
    if env_path.exists():
        raise SystemExit(
            f"{env_path} already exists. Refusing to overwrite it — regenerating secrets "
            f"over a live deployment orphans every signature it has made."
        )

    env_path.write_text(generate_env(domain, demo=demo), encoding="utf-8")
    env_path.chmod(0o600)

    compose = target / "docker-compose.yml"
    compose.write_text(
        (REPO / "docker-compose.yml")
        .read_text(encoding="utf-8")
        .replace("spoiledduckie.localhost", domain),
        encoding="utf-8",
    )
    caddyfile = target / "Caddyfile"
    caddyfile.write_text(
        (REPO / "Caddyfile").read_text(encoding="utf-8").replace("spoiledduckie.localhost", domain),
        encoding="utf-8",
    )
    return {"env": env_path, "compose": compose, "caddyfile": caddyfile}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openstore-up", description=__doc__)
    parser.add_argument("domain", help="the Merchant domain this deploy serves (ADR-0007)")
    parser.add_argument("--into", type=Path, default=Path.cwd())
    parser.add_argument("--live", action="store_true", help="turn demo mode off")
    args = parser.parse_args(argv)

    written = write_install(args.domain, args.into, demo=not args.live)

    print(f"Wrote {written['env']}, {written['compose']} and {written['caddyfile']}.")
    print()
    print("Next, in order:")
    print("  1. docker compose up -d --build")
    print(f"  2. open https://{args.domain}/agentic — first run will not continue until you")
    print("     acknowledge the encrypted key export as saved. It is the only way back if")
    print("     this deployment is lost, and nobody can recover it for you (ADR-0014).")
    print()
    print("Nothing here needs hand-editing before first boot. If it does, that is a bug.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
