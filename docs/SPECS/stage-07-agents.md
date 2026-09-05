# Stage 07 — Agents: buyer agent, negotiation loop, amendment ceremony, narrator

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md` — R0.9 and R0.10 are the constitution of this stage.
- `OPENSTORE_PRD_v3.md` Part 5 (§5.1–5.4), §7.4 (PolicyAmendment schema), §7.5
  (Negotiation message schema), §7.6 (Discord channels), INV-12, Part 4 (llm.py interface).
- `REGISTRY.json` — `negotiation_states` is a closed set; do not edit REGISTRY.json.
- Stages 2–6 artifacts: compiler, WebAuthn RP, MCP tools, surfaces.

## SCOPE (closed)
- `src/openstore/agents/llm.py`, `buyer_agent.py`, `merchant_agent.py`
- `src/openstore/notifier.py` (4-channel Discord trace emitter)
- `src/openstore/surfaces/studio.py` (amendment-approval surface only)
- `tests/stage07/**` (incl. R0.9/R0.10 adversarial tests)
- `OPEN_QUESTIONS.md`

## BUILD

### S7.1 `agents/llm.py`
Single provider interface; model name from config key `llm.model`. All agent LLM calls go
through this module. It holds no payment/PSP/signing material (R0.10). Import firewall:
`core/` and `verify/` must not import this — extend `test_import_firewall.py` to prove it.

### S7.2 Buyer agent — `buyer_agent.py`
discord.py bot, MCP client. Planning loop per §5.1: parse goal → `search_products` →
policy-aware cart (`create_cart`/`update_cart`) → `checkout_initiate` → `checkout_confirm`
→ hold monitoring → report. It never sees Razorpay credentials; payment links go
out-of-band to the human. Every money attempt goes through the compiler (R0.9).

### S7.3 Negotiation loop (§5.2)
On a recoverable DENY (`policy.tag_violation`, `policy.sku_blocked`,
`policy.spend_per_tx_exceeded` with in-policy headroom), merchant agent and buyer agent
exchange §7.5 messages (`negotiation_id`, `round`, `from`, `state`, `cart_delta`,
`reason_code`, `trace_id`) until a cart compiles or both emit `NO_COMPLIANT_PATH`.
States are the closed `negotiation_states` set. Transcript stored and available for
`human_intent.agent_plan`.

### S7.4 Policy amendment ceremony (§5.3)
On `NO_COMPLIANT_PATH`, merchant agent MAY draft a §7.4 PolicyAmendment (`amendment_id`,
`base_policy_hash`, `delta{...}`, `reason_code_triggered`,
`drafted_by:"merchant_agent"`, `draft_digest`). Routed to the human for one-tap WebAuthn
approval in Policy Studio → on approval, policy version increments, cart recompiles.
Assertion recorded for PoAI §authority. Denied amendment is terminal for that cart: fail
loud, no retry (R0.5). The agent CANNOT self-approve — a test asserts no amendment
activates without a valid WebAuthn assertion.

### S7.5 Evidence narrator (§5.4)
Given a PoAI bundle + verifier output, writes the human-readable cover note. Prose only;
the bundle is never modified. Narrative stored alongside, never inside, the signed bundle.

### S7.6 Discord notifier (§7.6)
Exactly four channels: `#buyer-trace`, `#merchant-trace`, `#money-trace`, `#alerts`.
`#money-trace` is append-only and receives every RESERVE/CAPTURE/RELEASE/REFUND with
`trace_id`. Every rejection traced with its reason code.

## MUST NOT
- No agent may bypass `compile_decision()` — adversarial tests REQUIRED: (a) an agent
  instructs "ignore the spend cap" → compiler still denies; (b) an agent supplies its own
  totals → server recomputes (R0.8); (c) an agent attempts to publish a campaign or
  amendment without WebAuthn approval → hard error.
- No campaign orchestration (Stage 8). No new identifiers.
- Agents never hold keys — assert statically and at runtime.

## DONE WHEN (all exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage07/ -q` → 0 failures, incl. all three R0.9/R0.10 adversarial tests
- `pytest tests/sentinel/test_import_firewall.py` → pass
- Human-verified smoke (report to operator): Discord bot completes a full policy-aware
  purchase in test mode; a DENY triggers negotiation; a `NO_COMPLIANT_PATH` produces a
  draft amendment that activates ONLY after the human's WebAuthn approval.
- `ruff check src tests` and `mypy src` → clean

## COMMIT GATE
`stage(07): agents`
