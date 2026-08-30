# OpenStore — Decisions Log
# Append-only. Each entry: DECISION-NNN | date: <UTC> | stage: NN

## DECISION-001 | date: 2026-08-30T11:10:00Z | stage: 00
- Sidecar over plugin: credential absence (INV-2), R0.8, R0.10 enforcement requires separate process boundary.
- The sidecar runs as a separate process on the same domain (reverse proxy) or subdomain.
- Merchant's existing site is never modified.

## DECISION-002 | date: 2026-08-30T11:10:00Z | stage: 00
- PoAI moved to Stage 4: it is the product differentiator. Evidence layer before payments.

## DECISION-003 | date: 2026-08-30T11:10:00Z | stage: 00
- Demo stores are acceptance tests, not the deliverable. The deliverable is the pip-installable package.

## DECISION-004 | date: 2026-08-30T11:10:00Z | stage: 00
- Four Discord channel names defined in REGISTRY.json: #buyer-trace, #merchant-trace, #money-trace, #alerts.

## DECISION-005 | date: 2026-08-30T11:10:00Z | stage: 00
- Campaign check appended as #12 to preserve v2.1 transcript/golden validity for checks 1–11.

## DECISION-006 | date: 2026-08-30T11:10:00Z | stage: 00
- UAP watchlisted: NPCI UAP is pilot-stage, no public spec to pin constants against — watchlist only.

## DECISION-007 | date: 2026-08-30T11:10:00Z | stage: 00
- Multi-merchant delegation chains admitted unsolved (offline double-spend) — post-freeze.

## DECISION-008 | date: 2026-08-30T11:10:00Z | stage: 00
- Synthetic buyer swarm as a feature cut from MVP; red-team suite retained.

## DECISION-009 | date: 2026-08-30T11:10:00Z | stage: 00
- AXO optimizer loop cut from MVP.

## DECISION-010 | date: 2026-08-30T11:10:00Z | stage: 00
- Frontend: lean single-file HTML per surface, no SPA framework, no npm, no build step. Vanilla JS + fetch.

## DECISION-011 | date: 2026-08-30T11:10:00Z | stage: 00
- Import firewall: src/openstore/core/ and src/openstore/verify/ MUST NOT import any LLM SDK, network LLM client, or openstore.agents.*.

## DECISION-012 | date: 2026-08-30T11:10:00Z | stage: 00
- uv as package manager; uv.lock committed; uv sync --locked in CI; uv pip forbidden.

## DECISION-013 | date: 2026-08-30T11:10:00Z | stage: 00
- Money: integer minor units (paise) only. Floats forbidden anywhere money appears.

## DECISION-014 | date: 2026-08-30T11:10:00Z | stage: 00
- Time: RFC 3339 strings everywhere EXCEPT IntentPolicy.not_before / .expires_at (integer Unix seconds, grandfathered).

## DECISION-015 | date: 2026-08-30T11:10:00Z | stage: 00
- Currency: "INR" only. Single-tenant per sidecar process.