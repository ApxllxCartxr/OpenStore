"""`openstore-verify` CLI (IMPLEMENTATION_SPEC §7.2 / §7.5 / §7.6).

Usage:
    openstore-verify <bundle.json> [--merchant-jwks <path>] [--json] [--quiet]

Exit codes (§7.6):
    0  all checks passed (warnings allowed)
    1  one or more checks failed
    2  bundle malformed / unparseable / schema invalid
    3  unsupported_compiler_digest
    4  usage error

No network access is ever performed. A `--merchant-jwks` value that looks like a
URL is rejected with exit code 4.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from openstore.verify import to_json_dict, verify_bundle
from openstore.delegation import detect_forks

_USAGE_ERROR = "usage_error: jwks must be a local file"
_SCHEMA_PATH = Path(__file__).parent / "schema" / "poai-0.1.schema.json"


def _human_table(res) -> str:
    lines = []
    title = f"PoAI {res.poai_version}  bundle {res.bundle_id}  -> {'PASS' if res.ok else 'FAIL'}"
    lines.append(title)
    lines.append("-" * len(title))
    for c in res.checks:
        mark = {"pass": "PASS", "fail": "FAIL", "warn": "WARN"}.get(c["result"], c["result"].upper())
        detail = f"  ({c['detail']})" if c.get("detail") else ""
        lines.append(f"  [{mark}] {c['name']}{detail}")
    if res.warnings:
        lines.append("Warnings:")
        for w in res.warnings:
            lines.append(f"  - {w}")
    if res.re_derived:
        rd = res.re_derived
        aal = rd.get("aal_level")
        reasons = ", ".join(rd.get("aal_reasons", [])) or "-"
        lines.append(f"Re-derived verdict: {rd.get('verdict')}  AAL {aal}  reasons: {reasons}")
    lines.append(res.liability_note)
    return "\n".join(lines)


def main(argv=None) -> int:
    args = (argv if argv is not None else sys.argv[1:])

    positional: list[str] = []
    jwks_path: str | None = None
    as_json = False
    quiet = False
    detect_dir: str | None = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            as_json = True
        elif a == "--quiet":
            quiet = True
        elif a == "--detect-forks":
            i += 1
            if i >= len(args):
                sys.stderr.write(_USAGE_ERROR + "\n")
                return 4
            detect_dir = args[i]
        elif a == "--merchant-jwks":
            i += 1
            if i >= len(args):
                sys.stderr.write(_USAGE_ERROR + "\n")
                return 4
            jwks_path = args[i]
        elif a.startswith("--merchant-jwks="):
            jwks_path = a.split("=", 1)[1]
        elif a.startswith("-"):
            sys.stderr.write(f"usage_error: unknown option {a}\n")
            return 4
        else:
            positional.append(a)
        i += 1

    # §4 fork detection: scan a directory of bundles, no single-bundle verify.
    if detect_dir is not None:
        return _run_detect_forks(detect_dir, as_json)

    if not positional:
        sys.stderr.write("usage_error: missing <bundle.json>\n")
        return 4
    bundle_path = positional[0]

    if jwks_path is not None and jwks_path.startswith(("http://", "https://")):
        if as_json:
            sys.stdout.write(json.dumps({"usage_error": "jwks must be a local file"}) + "\n")
        else:
            sys.stderr.write(_USAGE_ERROR + "\n")
        return 4

    try:
        bundle = json.loads(Path(bundle_path).read_text())
    except FileNotFoundError:
        sys.stderr.write(f"usage_error: bundle file not found: {bundle_path}\n")
        return 4
    except json.JSONDecodeError as e:
        if as_json:
            sys.stdout.write(json.dumps({"ok": False, "failures": ["schema_invalid"],
                                         "error": f"malformed json: {e}"}) + "\n")
        else:
            sys.stderr.write(f"schema_invalid: malformed json: {e}\n")
        return 2
    except Exception as e:  # unreadable file etc.
        sys.stderr.write(f"usage_error: cannot read bundle: {e}\n")
        return 4

    jwks = None
    if jwks_path is not None:
        try:
            jwks = json.loads(Path(jwks_path).read_text())
        except Exception as e:
            sys.stderr.write(f"usage_error: cannot read jwks: {e}\n")
            return 4

    res = verify_bundle(bundle, jwks)

    if as_json:
        sys.stdout.write(json.dumps(to_json_dict(res), indent=2) + "\n")
    elif not quiet:
        sys.stdout.write(_human_table(res) + "\n")

    return res.exit_code


def _run_detect_forks(directory: str, as_json: bool) -> int:
    """`--detect-forks <dir>` — emit any fork_proof across the bundles in <dir> (§4.3)."""
    from pathlib import Path as _Path

    bundles = []
    for p in sorted(_Path(directory).glob("*.json")):
        try:
            bundles.append(json.loads(p.read_text()))
        except Exception:
            continue
    proofs = detect_forks(bundles)
    if as_json:
        sys.stdout.write(json.dumps({"forks_detected": len(proofs), "fork_proofs": proofs}) + "\n")
    elif proofs:
        sys.stdout.write(f"Detected {len(proofs)} fork(s):\n")
        for pf in proofs:
            sys.stdout.write(
                f"  envelope {pf['envelope_id']} sequence {pf['sequence']}: "
                f"{len(pf['entries'])} conflicting SPEND entries\n"
            )
    else:
        sys.stdout.write("No forks detected.\n")
    return 1 if proofs else 0


if __name__ == "__main__":
    sys.exit(main())
