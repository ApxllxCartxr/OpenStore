"""§7 golden corpus: viewer/CLI and in-process verifier must agree.

The Evidence Viewer (§9.4) and the `openstore-verify` CLI both consume the
same canonical JSON object produced by `verify_bundle`. This test pins that
contract: for every corpus bundle, the CLI's `--json` output is byte-for-byte
the in-process verifier result, and the CLI exit code matches the expected
verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openstore.verify import to_json_dict, verify_bundle
from openstore.verify.cli import main

GOLDEN = Path(__file__).parent / "fixtures" / "golden"

FIXTURES = {
    "valid.json": 0,
    "tampered_amount.json": 1,
    "tampered_digest.json": 3,
    "tampered_chain.json": 2,
}


@pytest.mark.parametrize("name,expected_exit", list(FIXTURES.items()))
def test_viewer_and_cli_agree_on_corpus(name, expected_exit, capsys):
    bundle = json.loads((GOLDEN / name).read_text())
    jwks = json.loads((GOLDEN / "merchant.jwks.json").read_text())

    # in-process verdict (the viewer's source of truth)
    in_res = verify_bundle(bundle, jwks)
    in_json = to_json_dict(in_res)

    # CLI --json, exercised through the real entry point
    code = main([str(GOLDEN / name), "--merchant-jwks", str(GOLDEN / "merchant.jwks.json"), "--json"])
    out = capsys.readouterr().out
    cli_json = json.loads(out)

    assert cli_json == in_json, f"{name}: CLI json diverged from verifier"
    assert code == in_res.exit_code == expected_exit, f"{name}: exit {code} != {expected_exit}"


def test_cli_quiet_emits_nothing(capsys):
    code = main([str(GOLDEN / "valid.json"), "--merchant-jwks",
                 str(GOLDEN / "merchant.jwks.json"), "--quiet"])
    out = capsys.readouterr().out
    assert code == 0
    assert out == ""


def test_cli_rejects_url_jwks(capsys):
    code = main([str(GOLDEN / "valid.json"), "--merchant-jwks", "https://evil.example/jwks.json"])
    assert code == 4
