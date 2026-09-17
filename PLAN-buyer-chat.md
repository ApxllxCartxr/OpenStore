# Plan — Demo Buyer Chat (`demo/buyer-chat/`)

Surface 3. Proves the Consumer flow end to end. Claude-like chat over Ollama (swappable model) with tool calls. Air-gapped stranger: own folder/process/deps/DB (chat sessions only), zero sidecar/merchant-site imports, HTTP-only with its own OAuth client like any external agent. The chat UI is dumb on purpose — convenience is sidecar-engineered (SPEC §11), the agent renders verbatim.

## B1 — Chat scaffold + model seam

- Web chat (threads, streaming text, cards, modals) + model seam (Ollama default, swappable without touching flow code). Own session DB; nothing else persisted — no carts, no money state, no Consumer PII store.
- Tool-calling loop with bounded steps and closed action set (search / ask / answer / cart ops / order status). Model proposes, deterministic code validates against real Merchant data before anything is shown or sent.
- DONE WHEN: chat boots alone against a stub tool server, step caps enforced, malformed model output rejected with a named error and no state change.

## B2 — Direct-add + contacts

- Paste-Merchant-URL direct-add: fetch sidecar card, show name/category/key/protocols, OAuth enroll, save as contact. Custom list-URL support + baked-in scaffolded seed file (empty + example). No crawler, no ranking.
- Contact = one Merchant route (URL + credential + standing read approvals). Forgetting a contact revokes nothing server-side; server revocation is separate and authoritative.
- DONE WHEN: unknown-URL, tampered-card, and dead-sidecar cases each fail with a named reason; valid add completes without touching sidecar code.

## B3 — MCP shopping flow (live)

- Search → permission modal showing the exact request JSON with `Allow once / Always allow (reads only) / Decline` → result cards with `ADD` → cart modal (second Allow-once for spend) → checkout card rendered **verbatim** from the Merchant-signed total (items, ETA, total, UPI-only options, `Place order`, expiry countdown).
- Pre-display signature check on every Merchant-signed payload; mismatch blocks Place order with `signature-invalid`. Agent-side totals never exist.
- DONE WHEN: golden MCP transcript passes; edited-total and expired-offer variants block with codes; standing approval never covers a spend step (tested).

## B4 — Tap + pay + receipt without context loss

- `Place order` opens `/agentic/approve?t=` (same-domain tap ceremony, virtual tap in demo with test keys) then auto-returns via resume URL to the exact chat + cart state. Fake-UPI link → marked paid → signed webhook → receipt.
- Pending/expiry messaging: 24h countdown shown, expired carts say "re-add, don't re-search". Failures name the exact door + reason. Zero re-login, zero re-search after tap — asserted, not hoped.
- DONE WHEN: tap-to-paid round-trip preserves exact state; expired and declined paths message correctly; demo refuses live keys at boot; receipts marked demo.

## B5 — Protocol toggle + smoothness gates

- Header toggle `[MCP|UCP|ACP|AP2]`: MCP drives live; the other three replay the identical golden flow through their translators with envelope + shared core transcript + conformance badge side by side.
- Smoothness gates (red on breach): one tap per spend, no re-login/re-search across tap, resumed-state equality, verbatim-render equality vs signed payload, expiry messaging present.
- DONE WHEN: toggle proves all four envelopes against the same transcript; smoothness suite green; any agent freelancing (reworded totals, skipped checks) fails conformance display.
