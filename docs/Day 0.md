# OpenStore: The 9-Day Course

**A from-scratch, university-style build of a real agentic commerce reference server for Razorpay's rails.**

Deadline: **Saturday, September 5, 2026.** Day 1: **Wednesday, August 26, 2026.** Day 9: **Thursday, September 3, 2026.** That leaves September 4–5 as slack. You are not supposed to build features on those two days — they exist so that a bad rehearsal, a dead tunnel, or a Razorpay outage doesn't sink you. Feature freeze is at the end of Day 8, no exceptions. This isn't a suggestion; it's in the plan you already committed to, and this course enforces it the same way.

---

## What you already know

Python, and discord.py. That's the floor this course is built on. Everything else — FastAPI, SQLModel, OAuth 2.1, JWTs, MCP, Ed25519 signatures, JWS, LangGraph, the A2A protocol, Razorpay's API, webhooks, ASGI, Pydantic — you will meet for the first time in these pages, and each one gets a full first-principles explanation **the first time it appears**. After that, later days assume you remember it and just give you the shape of what to build.

This is not a "hello world" tutorial. By day 9 you'll have a three-process, two-Discord-bot, OAuth-protected, cryptographically-signed-mandate commerce system, with a second independent merchant running on the same code with zero changes. It's long because the system is real. There is no shortcut version of "explain OAuth 2.1 from zero" that is also short.

## How the depth tapers

- **Days 1–4**: every new idea gets the full treatment — what it is, why it exists, what breaks without it, then the code. Expect to read slowly.
- **Days 5–9**: concepts you've already seen (FastAPI routes, SQLModel queries, trace emission, Pydantic models) are given as **function signatures only** — name, inputs, outputs, one line on behavior. Concepts that are genuinely new on that day (LangGraph state graphs, the A2A task lifecycle, idempotency testing patterns) still get the full explanation.

If you ever hit a signature you don't understand because you skipped ahead or forgot a Day 1–4 concept, stop and go back — don't guess. The whole point of the signed-mandate architecture is that skipped verification steps are exactly how these systems break in the real world; the same discipline applies to skipping steps in _learning_ it.

## How to use these files

One file per day: `01-day-one.md` through `09-day-nine.md`. Each day has:

1. **Concepts** — the first-principles explanations for anything new today, in the order you'll need them.
2. **Build** — the actual implementation, broken into numbered steps. Each step says WHAT you're building, WHAT tool/library/technique you're using, and WHY that's the right tool, before showing HOW (the code).
3. **Exit check** — the plan's own "✅ Exit" criterion, plus a way to verify you actually hit it, not just that you think you did.
4. **What could go wrong** — the specific failure modes people hit on that exact day, so you recognize them fast instead of debugging blind for an hour.

Code blocks are meant to be typed or copied into your own repo, not generated for you — you're building this by hand, which is the whole point of doing it over 9 days instead of asking an AI to scaffold it.

## The one-paragraph thesis (memorize this — you'll say it out loud on Day 9)

Razorpay's live pilots let an AI _buyer_ act for a human against a conventional merchant backend. Nobody has standardized the _merchant_ side. OpenStore is a generic, config-driven reference server that makes any D2C merchant discoverable and safely transactable by an external AI buyer — implementing the primitive the industry has converged on (AP2 Mandates, Mastercard Verifiable Intent, Visa TAP): a single-use, server-issued, human-approved signed mandate that cryptographically binds an immutable cart to an explicit human authorization. It's scoped down to something buildable in 9 days, wired into Razorpay's actual rails, somewhere this hasn't been shown publicly.

**Say the honest version too, every time:** this is a _human-approved, server-signed_ mandate. The Ed25519 signature proves the merchant server authorized this exact payload after an out-of-band human checkpoint. It does **not** prove the human personally produced a cryptographic signature. Discord DM + OTP is a practical out-of-band human checkpoint for a controlled demo — it is not a phishing-resistant authenticator under NIST SP 800-63B-4. Saying this out loud makes the system look _more_ trustworthy, not less. Judges have seen enough overclaiming to smell it instantly; precise claims read as competence.

## The architecture you're building toward

```
Discord #buyer-agent ──> Buyer Agent process (LangGraph + Gemini)
                              │ A2A (JSON-RPC/SSE)      │ MCP (streamable HTTP, OAuth Bearer)
                              ▼                          ▼
                   Merchant Reasoning Agent      Merchant Execution Server (FastAPI)
                   - Gemini + Razorpay MCP       - OAuth 2.1 AS
                   - read-only DB session        - scoped MCP tools
                   - NO signing key              - Ed25519 mandate signing
                   - NO Razorpay write creds     - spend caps, idempotency, audit
                   - recommends / drafts only    ──> razorpay-python SDK ──> Razorpay Test
                                                 <── webhook (cloudflared tunnel)
                                                          │
                   OpenStore Merchant bot (separate app) ─┴─> DM approval embed + OTP modal
```

Three processes. Three separate credential sets. The reasoning layer — the part with the LLM in it, the part that can hallucinate, get prompt-injected, or just be wrong — **cannot move money, because it does not hold the keys.** Not because you told it not to. Because it structurally can't. Every single day of this course is in service of that one sentence. If you remember nothing else from the whole build, remember that the security model is an architecture, not an instruction.

## Prerequisites checklist (do these in parallel with Day 1, don't block on them)

- [ ] Two Razorpay test-mode accounts, each with its own test key ID + secret. (Second one is for Day 8's genericity proof — create it now, account creation/verification can have unpredictable delays.)
- [x] Two Discord Applications in the [Discord Developer Portal](https://discord.com/developers/applications): `OpenStore Buyer` and `OpenStore Merchant`. Each needs its own bot token.
- [x] One Discord server (guild) you control, with 4 text channels: `#buyer-agent`, `#merchant-agent`, `#merchant-server`, `#audit-trail`.
- [x] A webhook URL for each of those 4 channels (Channel Settings → Integrations → Webhooks → New Webhook → Copy URL). You'll paste these into a `.env` file on Day 1.
- [x] Both bots invited to that server with at least `Send Messages`, `Embed Links`, `Use Slash Commands`, and (for the Merchant bot) `Send Messages in DMs`-equivalent permission (DMs work automatically once a user shares a server with the bot — no special scope needed, but note it since it trips people up).
- [x] Python 3.11+ installed. Check with `python3 --version`.
- [x] `uv` installed (a fast Python package/project manager — Day 1 explains it). Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- [x] A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) (free tier to start; the plan recommends buying a $5 paid key by Day 8 as demo-day insurance against free-tier rate limits).
- [x] `cloudflared` installed for the Day 4 webhook tunnel (`brew install cloudflared` on macOS, or the equivalent package for your Linux distro — a quick tunnel needs no account).

None of this blocks reading Day 1's concept sections — start those now, and have these ready by the time Day 1's build steps need them.

---

Turn to `01-day-one.md` when you're ready to start.