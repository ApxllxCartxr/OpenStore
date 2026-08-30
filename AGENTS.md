# AGENTS.md — OpenStore Implementation Contract
# This file is read by the coding agent at the start of every session. Obey it absolutely.

You are implementing the OpenStore sidecar package from OPENSTORE_PRD.md v3.0.
These rules bind every action you take in this repository.

## The ten rules (from PRD §0.1, verbatim)
- R0.1  MUST/MUST NOT/SHOULD/MAY per RFC 2119.
- R0.2  NEVER invent an identifier. All names are closed sets in REGISTRY.json.
        If a name is not there, it does not exist.
- R0.3  NEVER invent a value. Enums are exhaustive. Unknown value => hard error,
        never a fallback, never a silent default.
- R0.4  When the PRD is silent, STOP. Append to OPEN_QUESTIONS.md (template below),
        commit, and stop the stage. Do NOT pick a default.
- R0.5  Fail loud. No silent except, no coercion, no defaulting, no retrying
        non-retriable errors. Every rejection carries a closed-set reason code.
- R0.6  The legacy OTP path is retired. Do not resurrect it.
- R0.7  [verify-at-build] markers MUST be confirmed against live test-mode responses
        and pinned as constants with a source comment. Never invent a constant.
- R0.8  NEVER trust agent-supplied totals, prices, cart hashes, or policy state.
        Recompute server-side.
- R0.9  LLM output is a proposal, never a command. All agent artifacts pass through
        compile_decision() and the deterministic validators. No agent bypass path exists.
- R0.10 Reasoning agents hold NO payment keys, PSP credentials, or signing material.

## Hard constraints
- Money: integer minor units (paise) only. Floats are forbidden anywhere money appears.
- Time: RFC 3339 strings everywhere EXCEPT IntentPolicy.not_before / .expires_at,
  which are integer Unix seconds (grandfathered — do not "fix").
- Currency: "INR" only. Single-tenant per sidecar process.
- Do not refactor, rename, or "improve" anything outside the current stage's SCOPE.

## Closed sets are executable
- REGISTRY.json is the single source of truth for every identifier and enum.
- Run `python scripts/registry_diff.py` before EVERY commit. It MUST print nothing.
- `pytest tests/test_registry_compliance.py` MUST pass. If code contains an identifier
  not in REGISTRY.json, or REGISTRY.json names something unimplemented, the build is red.
- You may NOT add an identifier to REGISTRY.json yourself. A missing identifier is an
  OPEN_QUESTION (R0.4), not a permission slip.

## Crypto is pinned, never improvised
- GOLDEN/ contains canonical-JSON vectors, hash-chain vectors, a known-good signed PoAI
  bundle, WebAuthn fixtures, and 40+ compiler decision vectors.
- Your implementation MUST reproduce these byte-for-byte. If output differs, the CODE is
  wrong, the vector is right. Do not "fix" a golden vector.

## Stage discipline
- Work exactly one stage at a time, from SPECS/stage-NN-*.md.
- A stage is DONE only when every command in its "DONE WHEN" block exits 0.
- Commit message format: `stage(NN): <slug>`.

## Ambiguity protocol (R0.4)
When you hit an unspecified decision:
1. Check DECISIONS.md — it may already be resolved.
2. If not, append to OPEN_QUESTIONS.md:

## Q-NNN | stage: NN | date: <UTC>
- What is ambiguous:
- Options considered:
- Blocked since: <UTC>

3. Commit `docs(open-questions): Q-NNN` and STOP the stage.
4. Resume only after a human writes a `RESOLUTION:` block under Q-NNN.

## The one unforgivable act
Inventing a `[verify-at-build]` constant from memory. It silently invalidates every
piece of evidence already issued. When in doubt: OPEN_QUESTION and stop.