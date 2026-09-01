# OpenStore verify — CLI entry point (openstore-verify)
# Per PRD §3.6 and S4.4.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from openstore.verify import checks

verify_app = typer.Typer(
    name="openstore-verify",
    help="Offline PoAI evidence verifier — 14 checks, zero network access.",
)

BUNDLE_ARG = typer.Argument(..., help="Path to the PoAI bundle JSON file")
OPT_JSON = typer.Option(False, "--json", help="Emit machine-readable JSON output")
OPT_JWKS = typer.Option(None, "--merchant-jwks", help="Directory of per-merchant JWKS files")
OPT_FORKS = typer.Option(None, "--detect-forks", help="Directory to check for forked envelopes")


def _load_bundle(path: Path) -> dict[str, Any]:
    try:
        with open(path) as f:
            bundle: dict[str, Any] = json.load(f)
            return bundle
    except json.JSONDecodeError as e:
        typer.echo(f"error: bundle is not valid JSON: {e}", err=True)
        raise typer.Exit(code=checks.EXIT_MALFORMED)
    except FileNotFoundError:
        typer.echo(f"error: bundle file not found: {path}", err=True)
        raise typer.Exit(code=checks.EXIT_USAGE)


def _load_jwks(path: Path) -> dict[str, Any] | None:
    if not path:
        return None
    if not path.exists():
        typer.echo(f"error: --merchant-jwks directory not found: {path}", err=True)
        raise typer.Exit(code=checks.EXIT_USAGE)
    return None


def _check_fork(ctx: checks.VerifierContext, fork_dir: Path | None) -> list[dict[str, Any]]:
    """Detect fork: same envelope_id + sequence appearing in multiple bundles."""
    if not fork_dir:
        return []
    fork_dir = Path(fork_dir)
    if not fork_dir.exists():
        return []

    proofs: list[dict[str, Any]] = []
    seen: dict[tuple[Any, Any], list[str]] = {}

    for bundle_path in fork_dir.glob("*.json"):
        try:
            with open(bundle_path) as f:
                bundle = json.load(f)
        except Exception:
            continue

        authority = bundle.get("authority") or {}
        delegation = authority.get("delegation") or {}
        envelope_id = delegation.get("envelope_id")
        spend_chain = delegation.get("spend_chain") or []

        for entry in spend_chain:
            seq = entry.get("sequence", -1)
            key = (envelope_id, seq)
            if key not in seen:
                seen[key] = []
            seen[key].append(str(bundle_path))

    for (env_id, seq), paths in seen.items():
        if len(paths) > 1:
            proofs.append({
                "envelope_id": env_id,
                "sequence": seq,
                "bundles": paths,
                "count": len(paths),
            })

    return proofs


@verify_app.command()
def verify(
    bundle_path: Path = BUNDLE_ARG,
    json_output: bool = OPT_JSON,
    merchant_jwks: Path | None = OPT_JWKS,
    detect_forks: Path | None = OPT_FORKS,
) -> None:
    """Verify a PoAI evidence bundle against all 14 checks."""
    if merchant_jwks is not None:
        if not merchant_jwks.exists():
            typer.echo(f"error: --merchant-jwks not found: {merchant_jwks}", err=True)
            raise typer.Exit(code=checks.EXIT_USAGE)
        if not merchant_jwks.is_dir():
            typer.echo(f"error: --merchant-jwks must be a directory, not a file: {merchant_jwks}", err=True)
            raise typer.Exit(code=checks.EXIT_USAGE)

    bundle = _load_bundle(bundle_path)

    ctx = checks.VerifierContext(
        bundle=bundle,
        jwks_dir=merchant_jwks,
        fork_dir=detect_forks,
    )

    results = checks.run_all_checks(ctx)
    exit_code = checks.bundle_exit_code(results)

    if json_output:
        output = {
            "bundle_id": bundle.get("bundle_id", "unknown"),
            "results": [
                {
                    "check": r.name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "section": r.section,
                    "link_index": r.link_index,
                }
                for r in results
            ],
            "aal": bundle.get("aal", {}).get("level"),
            "aal_liability": bundle.get("aal", {}).get("reasons", []),
            "merchant_asserted": _extract_merchant_asserted(bundle),
        }

        fork_proofs = _check_fork(ctx, detect_forks)
        if fork_proofs:
            output["fork_proof"] = fork_proofs

        typer.echo(json.dumps(output, indent=2))
    else:
        typer.echo(f"bundle: {bundle.get('bundle_id', 'unknown')} (PoAI {bundle.get('poai_version', '?')})")
        typer.echo(f"AAL level: {bundle.get('aal', {}).get('level', '?')}")
        liability = bundle.get("aal", {}).get("reasons", [])
        if liability:
            typer.echo(f"liability: {liability[0][:80]}...")

        for r in results:
            status = "PASS" if r.passed else "FAIL"
            detail = f"  [{status}] {r.name}"
            if r.detail:
                detail += f" — {r.detail}"
            if r.section:
                detail += f" (section={r.section}, link_index={r.link_index})"
            typer.echo(detail)

        if detect_forks:
            fork_proofs = _check_fork(ctx, detect_forks)
            if fork_proofs:
                typer.echo(f"\nFORK DETECTED ({len(fork_proofs)} duplicate envelope+sequence):")
                for fp in fork_proofs:
                    typer.echo(f"  envelope_id={fp['envelope_id']} seq={fp['sequence']} count={fp['count']}")
                    for path in fp["bundles"]:
                        typer.echo(f"    {path}")

    raise typer.Exit(code=exit_code)


def _extract_merchant_asserted(bundle: dict[str, Any]) -> dict[str, Any]:
    """Extract merchant_asserted fields from the bundle for --json output."""
    result: dict[str, Any] = {}
    goods = bundle.get("goods") or {}
    items = goods.get("items", [])
    if items:
        result["spent_minor"] = bundle.get("transaction", {}).get("amount_minor", 0)
        result["transactions_count"] = len(items)
    agent = bundle.get("agent") or {}
    if agent.get("agent_plan"):
        result["agent_plan"] = agent.get("agent_plan", {}).get("model")
    return result


def main() -> None:
    """Entry point for the openstore-verify CLI (registered in pyproject.toml)."""
    verify()


if __name__ == "__main__":
    main()
