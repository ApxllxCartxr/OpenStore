# Stage 01 — Package skeleton + REGISTRY.json

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md` (repo root — must already exist from STEP ZERO).
- `OPENSTORE_PRD_v3.md` §1.1, §1.2, §1.3, Part 4, Part 7.

## SCOPE (closed) — files you may create or modify
- `pyproject.toml`, `uv.lock`
- `src/openstore/__init__.py`, `src/openstore/config.py`
- `src/openstore/cli.py` (the `openstore` entry point)
- `src/openstore/core/__init__.py`, `src/openstore/psp/__init__.py`,
  `src/openstore/surfaces/__init__.py`, `src/openstore/agents/__init__.py`,
  `src/openstore/verify/__init__.py`
- `alembic.ini`, `alembic/env.py`, `alembic/versions/` (empty initial migration OK)
- `REGISTRY.json` (generated — see S1.2)
- `scripts/registry_diff.py`
- `gelateria.yaml` (demo merchant config), `.env.example`
- `tests/stage01/**`, `tests/conftest.py`
- `OPEN_QUESTIONS.md` (append-only)

No other file may be created or modified.

## BUILD
### S1.1 uv project
`uv init`-based PEP 621 project: `requires-python = ">=3.12,<3.13"`. Dependencies pinned in
`uv.lock`: fastapi, sqlmodel, alembic, uvicorn, pydantic, pyyaml, python-jose[cryptography]
(or equivalent JWS lib — pin whichever is chosen), httpx, pytest, ruff, mypy.
Optional extra `[project.optional-dependencies] razorpay = ["razorpay"]`. Commit `uv.lock`.

### S1.2 REGISTRY.json generation
Write `REGISTRY.json` with EXACTLY the content of PRD Part 7 (the full JSON block,
including the `"routes"` array encoding every route in Part 6). This file is the single
source of truth for closed sets. Do not add, rename, or remove any identifier.

### S1.3 `scripts/registry_diff.py`
Scans `src/openstore/**/*.py` for string literals matching identifier shapes (reason
codes, route paths, tool names, state names) and prints any identifier present in code but
absent from REGISTRY.json, and any REGISTRY.json entry unreferenced in code. Exit 0 with
empty output when in sync; exit 1 listing mismatches otherwise.

### S1.4 Config loader — `src/openstore/config.py`
Loads a merchant YAML (e.g. `gelateria.yaml`) + `.env`. Fails loud (R0.3) on: unknown YAML
keys, missing required keys (`merchant.name`, `merchant.id`, `currency`),
non-integer `_minor` fields, unknown enum values. Config keys are a closed set:
`merchant{name,id}`, `currency`, `catalog[]`, `razorpay{key_id,key_secret}`,
`per_user_aggregate_cap_minor` (default 500000), `evidence_retention_days` (default 540),
`envelope_ttl_seconds` (default 14400), `challenge_ttl_seconds` (default 120),
`campaign_min_bps`, `campaign_max_bps`, `campaign_max_active`, `llm.model`,
`discord{token, guild_id}`. No other config keys exist.

### S1.5 `openstore` CLI — `src/openstore/cli.py`
Two commands only: `openstore init --merchant <name> --currency INR` (scaffolds
`<merchant>.yaml`, `.env.example` copy, runs initial Alembic migration) and
`openstore serve <merchant.yaml>` (loads config, applies migrations, starts uvicorn on a
config port). Unknown subcommand → exit 4 (usage error), print usage.

### S1.6 Alembic at Stage 0/1
Alembic is wired now (DECISIONS §11.1.13). `create_all` is permitted in tests only. The
initial migration creates whatever schema Stage 1 needs (may be empty/minimal).

## MUST NOT
- No application logic, no compiler, no ledger, no routes beyond a health probe.
- No money handling, no WebAuthn, no agents, no MCP.
- Do not invent config keys, CLI flags, or identifiers beyond §S1.4/S1.5 and Part 7.

## DONE WHEN (all exit 0)
- `uv sync --locked` → clean
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage01/ -q` → 0 failures
- `openstore init --merchant "Test" --currency INR && openstore serve gelateria.yaml` →
  starts, serves `GET /health` 200, config-load failures exit non-zero with a reason
- `ruff check src tests` and `mypy src` → clean

## COMMIT GATE
`stage(01): skeleton`
