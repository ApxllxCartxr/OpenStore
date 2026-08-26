# Day 9 — Demo Day

**Date: Thursday, September 3, 2026.**

No new code today, per yesterday's freeze — unless you find a genuine, demo-blocking bug, in which case fix exactly that and nothing else. Today is about presentation: a rehearsed script, a fallback recording in hand, and a clear, honest telling of what you built and why each piece is there.

---

## Concepts

### 1. Structuring a technical demo around moments, not features

A feature-by-feature walkthrough ("first I'll show you the OAuth flow, then the MCP tools, then...") is the natural way to _think_ about what you built, because that's the order you built it in. It is usually the wrong way to _present_ it, because a feature list doesn't build toward anything — each item is independently forgettable. The plan's Day 9 script (below) is instead organized around **specific moments that carry evidentiary weight** — points where the audience sees a concrete, checkable fact, not just hears a claim. Structure your narration the same way: each beat should end with "...and here's the proof," not "...and that's another feature."

### 2. Precision over grandiosity, one more time

Revisit the honesty framing from `00-START-HERE.md`'s thesis paragraph before you present, because it's easy to let the pressure of a live demo push your language toward overclaiming ("this is unhackable," "the human's identity is cryptographically verified"). Neither is true, and saying either is a mistake a technically literate audience will catch immediately, at real cost to your credibility on everything else you say. The accurate claims — "the server signs after a human checkpoint," "the reasoning agent structurally cannot hold the signing key," "Discord DM + OTP is a practical, not phishing-resistant, out-of-band check" — are still an accomplishment, and stating them precisely is itself a demonstration of engineering maturity that a good judge will notice and credit.

---

## The demo script

Walk through these beats in order. Each includes what to show, what to say, and what evidence should be on screen when you say it.

### Beat 1 — The problem, in one paragraph

State the thesis from `00-START-HERE.md` directly: Razorpay's live pilots let an AI buyer act for a human against conventional merchants; nobody has standardized the merchant side; OpenStore is a generic, config-driven reference server implementing the human-approved signed-mandate primitive the industry has converged on. Keep this under 30 seconds — it's context, not the payload.

### Beat 2 — Discovery, cold

Open the storefront fresh (nothing pre-loaded, nothing cached). Show the JSON-LD in view-source. Then fetch `/.well-known/agent-commerce.json` and `/.well-known/oauth-authorization-server` live, and narrate: "this is the entire amount of information an external agent needs to know about this merchant before it can do anything — no custom integration, no bespoke API docs."

### Beat 3 — OAuth consent, live

Trigger the buyer agent's OAuth flow from a cold start (no cached token — delete the token cache file if needed before the demo, or use a fresh client). Show the actual consent screen with the actual requested scopes rendered in plain language. Approve it live. This is the human-in-the-loop moment made visible, not asserted.

### Beat 4 — The Discord-driven purchase

From `#buyer-agent`, type a real request. Let the audience watch: search → cross-sell suggestion arriving via a real A2A call to a separate process (point this out explicitly — "that suggestion just came from a second, isolated process over a standard agent-to-agent protocol, not a hardcoded string") → checkout initiate → the DM landing on the _separate_ merchant bot → approve + OTP in the modal → the fingerprint appearing in the DM reply.

### Beat 5 — The fingerprint match (one of the plan's three "moments that win the rubric")

Explicitly show the same 8-character fingerprint in three places side by side: the notifier's DM reply, the buyer agent's chat reply, and the `/admin/audit` row for that checkout. Say plainly: "these three things, generated in three different moments by two different processes, all point at the exact same signed object, and none of them ever showed the full signed payload in a chat channel."

### Beat 6 — Real money, real webhook

Open the real Razorpay payment link, pay with test-mode card details, and — without refreshing anything by hand — show the order status flipping to `PAID` in `/admin/audit`, driven purely by the webhook firing. Narrate the HMAC verification and event-dedup briefly: "Razorpay's server pushed this to mine; mine verified the signature before trusting it; if Razorpay retries this same event, my server recognizes it and ignores the duplicate."

### Beat 7 — Break it, on purpose (the second "moment that wins the rubric")

This is where Day 7 pays off. Pick 2–3 of your rehearsed failure modes — idempotent retry and tampered-signature are strong, distinct choices; a third from OTP lockout, revocation, or prompt-injection resilience rounds it out well. For each: state what you're about to attempt and why it should fail, do it live, and point at the resulting blocked trace/audit row as it appears. The idempotent-retry demo in particular should land as: "I'm about to simulate the exact ambiguous situation a real network timeout creates — watch what happens when the same request comes back."

### Beat 8 — Genericity (the third "moment that wins the rubric")

Restart the whole stack pointed at your second merchant config from Day 7/8, with zero code changes, and run a short version of Beat 2 or 4 against it. This is the concrete proof behind the word "generic" in your opening thesis — let it be brief and let the _lack_ of any code change be the point, not a long second demo.

### Beat 9 — Close with the honest caveat

Restate, in your own words, the precision point from Concepts section 2 above: this is a human-approved, server-signed mandate; Discord DM + OTP is a practical demo-grade out-of-band check, not a phishing-resistant authenticator; the security property that's actually proven is architectural (the reasoning agent cannot hold the signing key), not a claim about the human's identity being cryptographically verified. End on what would need to change for this to be production-grade, briefly — this shows you understand the boundary of what you built, which is itself a mark of the engineering judgment a technical audience is evaluating.

---

## If something breaks live

You recorded a fallback on Day 5 (the happy path) — know exactly where that recording is and how fast you can pull it up. If a failure-mode demo (Beat 7) breaks in an _unplanned_ way (i.e., the system fails somewhere other than where you intended it to), that's actually still salvageable — say so honestly ("that's not the failure I meant to show you — let's look at what actually happened," and if you can diagnose it in a few seconds live, that's a genuine, unscripted demonstration of understanding your own system). What's not salvageable is pretending it didn't happen or rushing past it hoping nobody noticed — a technical audience notices, and the recovery matters more than the glitch.

---

## After the demo

Feature freeze is over. If you want to keep developing OpenStore past today — hardening the loopback redirect handling, adding real per-merchant rate-limit tuning, replacing the in-memory OAuth code/token stores with persistent ones, whatever's next — that's a new phase of work, not a continuation of this 9-day plan, and worth treating as such: write down what changed about the goal before you start, the same discipline that made the last 9 days work.