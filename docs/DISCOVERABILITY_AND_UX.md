# Discoverability & UX Analysis

The problem with OpenStore's signing flow, and what it should look like in the state of agentic commerce.

---

## 1. The problem

A human must visit `http://localhost:8000/intent/sign` in a browser to register a WebAuthn credential and sign a spending policy. The agent cannot do this — WebAuthn requires a browser context (`navigator.credentials`). After signing, the agent picks up the conversation.

This creates two distinct gaps:

**Discoverability gap.** A new agent encountering the merchant has no way to know where the human should go to sign a policy. The agent must somehow surface a URL, and the human must navigate to it. For a demo this is manageable ("go to this link"). For a real product it is a friction point that kills autonomous operation.

**UX gap.** The signing ceremony is a browser redirect: leave the conversation, complete a WebAuthn form on an unfamiliar page, come back. This is a 3DS-style flow — the same pattern banks forced onto e-commerce in 2019, and the industry has spent a decade trying to escape because it drops conversion and breaks context.

---

## 2. What the agentic commerce space does instead

The direction of travel is **in-context approval** — the human sees the authorization request inside the agent's interface and approves it with a single biometric gesture, no redirect, no browser switch.

| Project | Pattern | Where the human approves |
|---|---|---|
| **AP2 (Google + 60 partners)** | Trusted Surface | The agent provider's UI — not the merchant's site |
| **Verifiable Intent** | L1/L2/L3 delegation chain | Human approves mandate inside the agent's conversation |
| **Apple Pay / Google Pay** | In-app biometric | Within the merchant's app, no redirect |
| **OpenStore (current)** | Browser redirect to `/intent/sign` | A website the human has to find |

OpenStore's approach is functionally equivalent to 3DS: redirect → authenticate → return. It works, but it is behind where the space is heading. The gap is not cryptography — it is the *location* of the approval.

---

## 3. What is feasible

WebAuthn requires a browser. This is not going to change. Any approach that claims to eliminate the browser visit is either replacing WebAuthn with a weaker mechanism or building a platform partnership that does not yet exist.

The feasible move is to **collapse the redirect into a single flow** rather than eliminate the browser:

1. The bot sends the policy as a rich message in Discord — what the human is actually authorising, in plain language.
2. The message contains a link that opens the signing page directly (deep link or in-app browser, not a manual navigation).
3. After the WebAuthn ceremony completes, the bot is notified (polling `GET /internal/webauthn/latest-assertion`, or a webhook) and continues the conversation.

This keeps WebAuthn, keeps the cryptography, removes the "where do I go" problem, and collapses the redirect into one tap. The human never has to find the merchant site.

---

## 4. What is not feasible (and why)

| Approach | Why it is not feasible for this project |
|---|---|
| **Portable Verifiable Credentials (W3C VC + DID)** | Requires full DID infrastructure, SD-JWT issuance, credential wallets, key management. A separate project, not an add-on. |
| **Verifiable Intent layered delegation** | Same problem — layered SD-JWTs with L1/L2/L3 require an entire credential lifecycle that does not exist here. |
| **AP2 Trusted Surface integration** | Depends on the agent provider being trusted by the merchant. Contradicts OpenStore's trust model where the merchant verifies independently. |
| **In-app WebAuthn inside Discord** | Discord does not expose `navigator.credentials`. WebAuthn requires a browser context. Not achievable today without a platform partnership. |
| **Ditching Discord entirely** | The redirect problem follows you to any channel. Discord is fine as a demo channel; the issue is the signing ceremony pattern, not the channel. |

---

## 5. The Discord question

Discord is not the problem. The problem is treating the Discord bot as the permanent home of the buyer agent rather than a demo vehicle.

In production, the buyer agent interacts with the merchant via **MCP over OAuth 2.1** — no Discord in the picture. The Discord bot is the first user of that MCP interface, not the architecture. For the signing ceremony, the agent platform (whatever the merchant's customers actually use) should provide the in-context approval mechanism — AP2 Trusted Surface, Verifiable Intent, or whatever standardizes next. OpenStore should be ready to accept any of those, which is what `INTEROP_SPEC.md` §3 (AuthorityPresentation) and §4 (AAL by scheme) are designed for.

**Practical stance**: keep the Discord bot for demo day. Make the signing ceremony as seamless as possible within it (rich policy message + direct link + completion detection). But design the architecture so that the buyer agent is MCP-first and channel-agnostic. Discord is a delivery mechanism, not a design decision.

---

## 6. The signing UX that should be built

For demo day, the signing flow should be:

1. **Bot shows the policy inline.** Not a link — the actual constraints, in plain language: *"Your agent will be allowed to buy vegan and dairy-free gelato from Gelateria Roma, up to ₹500 per purchase, for the next 24 hours."*
2. **Bot sends a direct link to the signing page.** The user clicks, the WebAuthn ceremony happens, the bot detects completion.
3. **Bot confirms and continues.** *"Policy signed ✅ — your agent is ready to shop."*

This collapses the redirect into one flow. The user never has to find the merchant site, never has to wonder if signing succeeded, and never has to context-switch back to Discord wondering what happened.

For the longer term, the **Policy Studio** (blast-radius engine with live catalog badging, proposed in `PROOF_CARRYING_COMMERCE.md` §2.11) is the right UX — but it is a larger build that depends on the evidence layer existing first. Start with the seamless redirect, add the Policy Studio later.
