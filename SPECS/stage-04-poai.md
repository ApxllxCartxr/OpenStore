# Stage 04 — PoAI evidence layer + offline verifier

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md`.
- `OPENSTORE_PRD_v3.md` §3.3 (all sub-sections incl. §3.3.11 hash-chain algorithm),
  §3.5 (AAL predicates e1–e9, first-match-wins, liability strings), §3.6 (verifier),
  §3.8 (attestations), Part 7.
- `REGISTRY.json` — do not edit.
- Stage 3 artifacts: compiler transcript format, WebAuthn fixtures.

## SCOPE (closed)
- `src/openstore/core/poai.py`, `src/openstore/core/aal.py`
- `src/openstore/verify/cli.py`, `src/openstore/verify/checks.py`
- `src/openstore/surfaces/templates/evidence_viewer.html` (NEW — single-file, zero-dependency)
- `scripts/make_poai_goldens.py`
- `GOLDEN/poai/*.json` (a known-good signed bundle + tampered variants), `GOLDEN/hashchain/*.json`,
  `GOLDEN/canonical/*.json`
- `tests/stage04/**`, `tests/test_poai_golden.py`, `tests/test_verifier.py`
- `OPEN_QUESTIONS.md`

## BUILD

### S4.1 Canonicalization
`canonical_json_bytes(obj)`: UTF-8, sorted keys, no insignificant whitespace, integers
unquoted, `null` for absent sections. Pin at least 5 vectors in `GOLDEN/canonical/`
(nested objects, unicode, empty containers, key-order independence, integer fidelity).
Any input whose canonical form differs from its vector → the code is wrong, never the
vector (AGENTS.md crypto rule).

### S4.2 Bundle assembly — `src/openstore/core/poai.py`
Assemble the 9-section bundle per §3.3.0–§3.3.10 exactly. A non-applying section is JSON
`null`, never absent. `campaign` is `null` unless a `campaign_id` applied. Hash chain per
§3.3.11: `link_0 = SHA256(c_0)`, `link_i = SHA256(link_{i-1} || c_i)`, `root = link_8`,
`chain.links` = 9 `"sha256:"+hex` strings in SECTION_ORDER, `chain.root == links[8]`.
`merchant_signature` = ES256 JWS Compact over exactly `{bundle_id, issued_at, root}`,
`kid = "{merchant_id}-key-{n}"` (DECISIONS §11.1.10). `time_anchor`: salted root digest,
Rekor primary / `merkle_daily` fallback, asynchronous — never blocks `checkout_confirm`
(DECISIONS §11.1.1). AAL grading per §3.5 first-match-wins, storing `predicates{e1..e9}`
and `reasons`.

### S4.3 PoAI goldens — `GOLDEN/poai/`
`scripts/make_poai_goldens.py` generates with fixed keys and fixed timestamps:
`bundle_aal2.json` (known-good, signed, anchored), `bundle_aal3.json` (cart-bound
challenge), plus tampered variants: `bundle_tampered_amount.json`,
`bundle_tampered_transcript.json`, `bundle_bad_merchant_sig.json`,
`bundle_missing_anchor.json`. `tests/test_poai_golden.py` asserts byte-identical
reproduction of the good bundles.

### S4.4 Verifier — `openstore-verify` CLI
Implements the 14 checks of §3.6 in order; fully offline (no network). Exit codes:
0 all pass · 1 a check failed · 2 malformed/schema-invalid · 3 `unsupported_compiler_digest`
· 4 usage error. Flags: `--json` (emits `merchant_asserted{spent_minor, transactions_count,
agent_plan}`), `--merchant-jwks <dir>` (directory of per-merchant JWKS, select by `kid`;
omitted → merchant signature reported `unverified_no_jwks`), `--detect-forks <dir>`
(emits `fork_proof` on duplicate envelope_id+sequence). On any chain break, the output
MUST name the exact section and link index that failed. AAL reporting uses the §3.5
liability strings verbatim with the mandated prefix.

### S4.5 Evidence Viewer — `evidence_viewer.html`
Single-file, zero-dependency HTML (Part 4): loads a bundle file, recomputes the hash chain
and verifies signatures in-browser via WebCrypto (ECDSA P-256 and RSASSA-PKCS1-v1_5
natively — DECISIONS §11.1.4), renders per-check pass/fail, the AAL level with its
liability string, and the compiler transcript. No external assets, no CDN.

## MUST NOT
- No Razorpay integration (Stage 5), no MCP/routes beyond what assembly needs (Stage 6),
  no agents (7), no campaigns (8).
- Do not reorder or renumber the 14 checks, the 9 sections, or the e1–e9 predicates.
- The verifier MUST NOT make network calls — a test asserts offline operation.
- Do not "improve" the liability strings; they are a closed set.

## DONE WHEN (all exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage04/ tests/test_poai_golden.py tests/test_verifier.py -q` → 0 failures
- `python -m openstore.verify GOLDEN/poai/bundle_aal2.json --merchant-jwks GOLDEN/poai/jwks/`
  → exit 0
- `python -m openstore.verify GOLDEN/poai/bundle_tampered_amount.json` → exit 1, output
  names section `transaction` and the broken link index
- Verifier offline proof: run the valid-bundle verification with network disabled → exit 0
- `ruff check src tests` and `mypy src` → clean

## COMMIT GATE
`stage(04): poai`
