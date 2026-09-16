# Stage 24 — Merchant console and passkey login

Slug: `merchant-console`. Goal: every merchant task is doable in a browser,
and the merchant can actually log in. Ships first: stages 25–27 land on it.

Scope: session layer (`core/session.py`, migration `0011` with
`merchant_sessions` + `merchant_settings`), TOFU claim / assertion login
(`GET /merchant/login`, `POST /merchant/logout`), rewritten `_operator`
(cookie → header → `?operator=` → 401, plus `authenticated: bool`),
DEF-2/DEF-3 fixes, CSRF (`X-OpenStore-CSRF` + `__CSRF_TOKEN__`), shared shell
(`static/app.css`, `static/js/webauthn.js|api.js|fmt.js` via `StaticFiles`,
`surfaces/render.py`), nine `/merchant/*` pages, evidence gating (Q-044),
`init` cleanup (DEF-7), SID-5 in-page pre-validation (DEF-14),
compiler-digest sentinel (see below), session signing via HKDF (no new secret).

Decisions/questions: DECISION-044 (TOFU claim), DECISION-045 (browser drafting
supersedes DECISION-025; settings precedence env > DB > YAML > default),
Q-044 (evidence: session OR buyer capability OR share token;
`evidence_share_ttl_days` default 30, bounds 1–540, capped by
`evidence_retention_days`; `evidence_token_hash` + `expires_at` on checkouts;
`view?t=` renders and autoload fetches `evidence?t=`; fix the
`evidence.py:84-101` fail-silent handler in the same commit; register
`checkout.evidence_not_found` and audit sibling dict-literal codes).

Compiler-digest sentinel (binding, from 26.5 riders): assert
`get_compiler_digest()` still matches `KNOWN_COMPILER_DIGESTS`, failing with
a pointer to `scripts/pin_compiler_digest.py`.

## DONE WHEN

- Fresh clone: `uv sync && uv run openstore serve <cfg>`, then zero terminal
  commands. In a browser: claim store → wizard → 3 SKUs → standing policy →
  order view → campaign approve.
- `GET /intent/studio`, `/campaign/studio`, `/admin/orders/view` render with a
  session; 401 without. `?operator=` works on all three, tests both directions.
- Sentinel: no HTML route 401s for a session-holding merchant.
- `tests/redteam/test_inv10_internal_routes.py` extended, green (never laxer).
- CSRF rejection test green. SID-5 mismatch refused in-page, never a boot crash.
- `uv run pytest -q`, `uv run mypy src/`, `uv run ruff check src/ tests/`,
  `uv run python scripts/registry_diff.py` (prints nothing, exit 0).
- Commit: `stage(24): merchant-console`.

