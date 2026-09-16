# Contributing to OpenStore

Issues and PRs are welcome. This file is the short version of what makes a PR
mergeable; `docs/AGENTS.md` is the long version and wins on any conflict.

## Setup

```bash
uv sync          # installs the dev group too — pytest, ruff, mypy, hypothesis, mutmut
```

Python 3.12 only (`requires-python = ">=3.12,<3.13"`). No live credentials are
needed for development: the test suite mocks Razorpay, Discord and the LLM
providers and runs on temp SQLite.

## Running the checks

These four are exactly what CI runs, in this order. Run them before you push.

```bash
uv run ruff check src/ tests/
uv run mypy src/                        # strict
uv run python scripts/registry_diff.py  # must print NOTHING, exit 0
uv run pytest -q                        # 741 tests
```

## The two rules that gate every PR

**1. Identifiers are a closed set.** Every reason code, route, tool name and enum
value lives in `REGISTRY.json`. Code containing an unregistered identifier fails
the build, and so does a registry entry naming something unimplemented —
`scripts/registry_diff.py` and `tests/test_registry_compliance.py` check both
directions. Adding a capability means registering its identifiers in the same PR.

**2. Nothing bypasses the compiler.** Agent modules may not import payment or
signing code. `tests/sentinel/test_import_firewall.py` enforces it, and it is not
a lint — it is the reason a prompt-injected agent can only ever produce a
suggestion that gets rejected. If your change makes an agent need a key, the
design is wrong, not the test.

## House rules for the money path

Changes under `core/` and `psp/` are held to the invariants the evidence bundle
depends on:

- **Money is integers.** Minor units (paise) only. Floats are forbidden anywhere
  money appears.
- **Time is RFC 3339**, except `IntentPolicy.not_before` / `.expires_at`, which
  are integer Unix seconds — grandfathered, documented, do not "fix" them.
- **Fail loud.** No silent `except`, no coercion, no defaulting, no retrying a
  non-retriable error. Every rejection carries a closed-set reason code that ends
  up in signed evidence.
- **Never trust agent-supplied values.** Totals, prices, cart hashes and policy
  state are recomputed server-side, always.
- **Goldens are right, your code is wrong.** `tests/GOLDEN/` pins canonical JSON,
  hash chains, a signed PoAI bundle, WebAuthn fixtures, compiler decision vectors
  and the MCP wire bytes. If your output differs, fix the code — do not edit a
  vector. Regenerate one only when the change to the format is the *point* of the
  PR, and say so explicitly in the description.

Touching `compiler.py`, `ledger.py`, `holdcancel.py`, `idempotency.py` or
`policy_signing.py`? Line coverage is not enough there. Run the mutation suite:

```bash
uv run mutmut run       # scoped to those five modules via pyproject [tool.mutmut]
uv run mutmut results
```

A surviving mutant means a test asserts too little. Kill it or explain why it is
equivalent.

## Where new code goes

| Change | Where | Watch out for |
|---|---|---|
| New catalog source | `surfaces/catalog.py::load_catalog` dispatch | Return the normalized item shape (`sku`, `unit_minor`, `tags`, `related_skus`). Nothing above the seam may learn the source. |
| New LLM provider | `agents/llm.py::register_provider` | Raise `LLMError` on provider failure so `FailoverProvider` can move on; anything else must fail loud. |
| New chat platform | a driver over `chat_platform` / `chat_user_id` | Identity is not authority. The authenticator is. |
| New agent capability | `agents/` | Proposal-only. It goes through the same validators as input from a stranger. |
| New route, tool or enum | the code **and** `REGISTRY.json` | Both directions are checked. |

## Commits and PRs

- Conventional prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`,
  `test:`. Historic commits use `stage(NN): <slug>` — that scheme is closed; new
  work does not claim a stage number.
- One conceptual change per PR. Keep formatting-only churn in its own commit.
- Say what you verified. "741 passing, mypy clean, registry silent" is the
  useful sentence; CI will confirm it.

## When the spec is silent

This repo was built from a PRD that treats ambiguity as a stop condition rather
than an invitation to pick a default. If you hit a decision the docs do not
settle, check `docs/DECISIONS.md` first — it may already be resolved with the
alternatives written down. If it is genuinely open, append it to
`docs/OPEN_QUESTIONS.md` using the template there and raise it in the PR, instead
of choosing a default that quietly becomes the contract.

## Security

Found something exploitable? Please do not open a public issue. Email the
maintainer (see `pyproject.toml`) with the details and a repro, and give it a
reasonable window before disclosure.
