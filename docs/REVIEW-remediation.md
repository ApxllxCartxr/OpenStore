# Remediation — stakeholder findings, 2026-09-19

Proposed fixes for every finding in `docs/REVIEW-stakeholder-findings.md`, written as an experienced legal/technical/finance reviewer would hand them back: what to do, who owns it, how urgent, and where it lands per `docs/EXPANSION.md`'s document ladder (ADR → SPEC → plan phase). Same rule as the findings file: this routes work, it doesn't do it — delete a row once its fix lands.

Priority key: **P0** blocks any live merchant or live money (fix before real installs), **P1** blocks a specific phase (D3, D5, hosted-mode) from starting, **P2** correctness/positioning debt that should land before the claim is made externally, **P3** hygiene.

---

## Verification log — 2026-09-19, run before acting on anything

Five findings rested on external facts asserted by a reviewer rather than read from a source. Three of the five were wrong, including the one this register had priced P0. Sources opened directly, not via summary:

| Claim | Source | Outcome |
|---|---|---|
| REG-1: Reserve Pay cap is ₹1,00,000, ADRs' ₹10,000 is a 10x error | NPCI/UPI/OC-228/2025-26, 8 Oct 2025 (scanned PDF, read as images) | **Wrong — the reviewer's own citation disproves the reviewer.** The circular says twice: "maximum of Rs.10,000 of block limit and up to 90 days." ADR-0016/0018 were right. Acting on item #1 as written would have put a 10x error *into* the ADRs. |
| SHF-1: UCP agent profile lives at `/.well-known/ucp-agent` with `signing_keys` | `ucp.dev/specification/signatures/` | **Path wrong.** The real path is `/.well-known/ucp` — one unified profile for both sides. An agent's profile is not at a well-known path at all: it is located via a `UCP-Agent: profile="https://…/.well-known/ucp"` request header. Keys are a JWK array (spec page says `keys[]`, secondary sources say `signing_keys` — resolve before writing either into SPEC). Signing is RFC 9421 with `Signature-Input` / `Signature` / `Content-Digest`. |
| WOO-3: "Woo 11.0" doesn't match WooCommerce's versioning scheme | developer.woocommerce.com changelog | **Wrong.** WooCommerce 11.0 shipped 4 Aug 2026; current is 11.1.1 (18 Sep 2026). The version pin is real. The HPOS half of WOO-3 stands. |
| MER-1: key loss is catastrophic and irreversible | ADR-0009, ADR-0014 (in-repo) | **Overstated.** ADR-0014 already states it: historical bundles verify forever against their own JWKS snapshot, and rotation is additive. Key loss is prospective-only — no new receipts until re-enrollment. The gap is that this is nowhere merchant-facing, not that it is untrue. |
| REG-4: `consumer_id` erasure scope | ADR-0011 (in-repo) | **Confirmed and worse than stated** — see item #7. `consumer_id` reaches the sealed evidence bundle, so cross-order unlinking would require rewriting sealed receipts. |

The standing fix is now a rule rather than a note: `docs/EXPANSION.md` §1 carries a citation convention requiring a dated source for any externally-sourced cap, threshold, path or version.

---

## 1. ~~Correct the Reserve Pay figure~~ — LANDED 2026-09-19, inverted

**Findings:** REG-1 (**disproved**), PSP-2 (partial, now item #11).

REG-1 was wrong: the circular it cites says ₹10,000, which is what ADR-0016 and ADR-0018 already said. What landed instead:

- ADR-0016 now carries the cap **with its dated citation** (NPCI/UPI/OC-228/2025-26, 8 Oct 2025, enriching OC-200/2024-25, 31 Jul 2024) plus the constraint that matters more than the number — Reserve Pay "shall be enabled only for online verified merchants with low ticket and high frequency transactions," which is an acquirer selection rule, not just a ceiling. The banding argument is unchanged and now sourced in NPCI's own words.
- ADR-0018 carries the cap, the citation, and the circular's "a block is not a guarantee of payment" line against its own attestation claim.
- The citation convention is in `docs/EXPANSION.md` §1 as a rule, not a note.

Everything else the circular turned up is in item #11, which this promotes to P0.

---

## 2. Get the RBI PA/PG question answered before any real PSP integration — ADR LANDED 2026-09-19, opinion pending

**Finding:** PSP-1 (premise **partly wrong** — see below).

**Landed:** ADR-0023 (`payment-orchestration-boundary`), cross-linked from SPEC §2. It does not stop at "we don't know": RBI's published guidelines draw the PA/PG line precisely on handling funds — aggregators pool and transfer, gateways provide routing technology with no involvement in funds and need no authorisation — so PSP-1's premise ("doesn't hinge on fund custody alone") is wrong as to the *licensing* test. The sidecar reads as a gateway. Two real exposures survive that classification and are named in the ADR: the baseline gateway security obligations attach to this codebase, and acquirer contracts routinely push merchant due-diligence onto whoever integrates.

**Still open:** the confirming opinion, now Q1 of `docs/COUNSEL-BRIEF.md`. Prerequisite for a production keys-live conversation with a PSP, not for writing S3 against the fake provider or a sandbox.

Original analysis retained below for the reasoning it captures.

ADR-0021 answers "are we a GST ECO" and never asks "are we a payment intermediary under RBI's PA/PG framework." These are different regulators, different tests, and a real PSP's legal team will ask the RBI question first — before they read any of the money-core rigor that's actually the project's strongest asset. Right now the honest answer is "we don't know," which is a fine place to be mid-spec and a bad place to be mid-integration-negotiation.

**Action:**
- Write a new ADR (`0023-payment-orchestration-boundary.md` or similar) parallel in structure to ADR-0021: state the architecture fact (sidecar calls `make-link`/`block`/webhooks on the Merchant's behalf, over HMAC, never touching settlement), state the open question plainly (does orchestrating the payment *request* without touching *settlement* still read as a PA/PG-regulated activity to RBI), and mark it explicitly "a read, not a ruling" exactly as ADR-0021 does — don't overclaim certainty where none exists yet.
- Commission an actual opinion on this before approaching any real PSP (Razorpay/Justpay/Airpay) for a production integration agreement — this is a prerequisite for PLAN-sidecar S3 going from "fake + Razorpay in v1" to a signed partnership, not a blocker on writing the code. The fake provider and sandbox Razorpay integration can proceed without it; a production keys-live conversation should not.
- Practical mitigation regardless of the opinion's outcome: keep the sidecar's role to *initiating* requests the Merchant's own PSP credentials authorize, never proxying or transforming payment data in a way that could read as "operating" the payment flow — this is already the architecture, so the fix here is mostly making the boundary explicit and defensible in writing, not re-architecting.

**Owner:** Founder, with a fintech/payments lawyer engaged specifically for this question (different specialism from the GST counsel ADR-0021 already gestures at). **Effort:** 1 ADR (~2 hrs) + external legal opinion (weeks, external cost). **Lands in:** new ADR, cross-linked from ADR-0021 and SPEC §8.

---

## 3. Stop claiming "any Buyer Agent, no pre-registration" without a reach caveat — SPEC LANDED 2026-09-19, install copy open

**Landed:** SPEC §2 now separates the admission mechanism from reach, names why (feed submission and gated merchant programmes, not arbitrary-endpoint pull), and points at `PLAN-distribution.md`. The same edit carried item #19's cross-link, which is therefore also closed.

**Still open:** the install-facing half — the `openstore up` first-run flow and `/agentic` copy (PLAN-sidecar S8), which is where a merchant actually reads it. Ship before the first real merchant installs.

Original analysis below.



**Findings:** MER-4, SHF-2, SHF-1 (cross-stakeholder consensus item).

The claim is architecturally true (the *admission mechanism* really does accept any self-registering agent) and practically false (no live agent surface today speaks the resulting discovery format, and the two mainstream ones — ChatGPT Shopping via Bing Merchant Center, Perplexity Shopping via gated Merchant Program — don't do the scheduled-pull ingestion D1 assumes). This is a marketing-truthfulness problem as much as a technical one: SPEC §2 is read by merchants, not just engineers, and it currently overpromises relative to what D0–D2 (unbuilt) would need to deliver.

**Action:**
- Reword SPEC §2's "any Buyer Agent" line to distinguish the *mechanism* (self-registration works for any agent implementing the admission protocol) from the *reach* (no mainstream assistant surface implements it yet) — one added sentence, not a rewrite: something like "the admission mechanism accepts any agent that speaks it; which agents speak it today is a distribution problem tracked in `PLAN-distribution.md`, not a limitation of the Gate."
- Add the same caveat where a merchant actually reads it: the `openstore up` first-run flow and/or the `/agentic` console should state plainly, at install time, "this makes your store *callable* by any agent that adopts the protocol; it does not currently place your store inside ChatGPT/Perplexity/Gemini shopping surfaces — see `PLAN-distribution.md` for that roadmap." One sentence in the install-facing UI copy, sourced from PLAN-sidecar S8's first-run work.
- This is honesty debt, not a redesign — ship it before the first real merchant installs, since it's the exact claim MER-4 says a merchant would build a go-live decision on.

**Owner:** Founder (copy) + whoever owns S8's first-run UI text. **Effort:** <1 day. **Lands in:** SPEC §2, PLAN-sidecar S8 first-run copy, PLAN-distribution.md intro.

---

## 4. Fix the Agent Profile so it actually composes with UCP — P1 (blocks D2/D5 credibly reaching anyone)

**Findings:** SHF-1, cross-referenced MER-4, `docs/REVIEW-findings.md` R2 (already tracked, this sharpens it).

R2 already recommended adopting UCP's agent-profile shape or nesting OpenStore's fields inside it. SHF-1 makes the problem concrete: UCP's real profile lives at `/.well-known/ucp-agent` with a `signing_keys` JWK Set + `ucp.capabilities` declaration, and OpenStore's spec doesn't even pin a well-known path for the chat's own profile (`PLAN-buyer-chat.md` B1/B2 leaves it unnamed). This is two non-interoperating discovery mechanisms, not a style choice — worth treating as a Break-severity item for the reach story even though it's Reach-severity for the money core (the Gate itself is untouched).

**Corrected 2026-09-19 — SHF-1 got the path wrong, and the real gap is smaller and sharper than the finding claims.** Verified at `ucp.dev/specification/signatures/`:
- The path is **`/.well-known/ucp`**, not `/.well-known/ucp-agent`. OpenStore already serves `/.well-known/ucp.json` (SPEC §10, PLAN-sidecar). The suffix is the whole defect on the business side: a UCP platform fetching `/.well-known/ucp` gets a 404 from a sidecar that is otherwise publishing the right document at nearly the right place. That is a one-line proxy-and-mount fix, not a schema redesign.
- **An agent's profile is not at a well-known path at all.** It is located from a request header: `UCP-Agent: profile="https://platform.example/.well-known/ucp"`. So the real admission gap is that the sidecar does not resolve that header, and `PLAN-buyer-chat.md` B1/B2 does not send it — not that the chat publishes at an unnamed path. This is the substantive half of SHF-1 and the finding as written would have sent the fix to the wrong place.
- Profile is unified across both sides (capabilities + keys in one document), signing is RFC 9421 with `Signature-Input` / `Signature` / `Content-Digest` (RFC 9530) — which is what OpenStore already does.
- Unresolved: the key array's field name (`keys[]` per the spec page, `signing_keys` per secondary sources). Read the profile schema page before writing either into SPEC — per `docs/EXPANSION.md` §1, with a dated citation.

**Action:**
- Serve the profile at `/.well-known/ucp` (keeping `.json` as an alias if anything already depends on it) and add the `UCP-Agent` header — sent by the chat, resolved by the sidecar's admission path.
- Restructure the Agent Profile shape to nest under UCP's `signing_keys` + `ucp.capabilities` envelope as a namespaced extension, keeping RFC 9421 signing and the JWK-thumbprint `agent_id` unchanged (R2's own recommendation — this fix was already scoped, just not scheduled).
- Add a translator step to Sidecar S6 (which currently only translates checkout envelopes) covering identity/admission, or explicitly scope S6's DONE WHEN to say envelope translation only and add a new phase for profile-format translation — right now it's silently out of S6's stated scope, which is how this gap survived a document ladder that's otherwise disciplined about contracts.
- Sequence this ahead of D2 ("addable by any MCP client") and D5 (gated surfaces) — neither is worth pursuing while the identity layer can't be discovered by the ecosystem it's trying to join.

**Owner:** Sidecar-plan owner (S4 admission logic + S6 translators) and buyer-chat-plan owner (B1/B2 profile publishing) in the same pass, per `EXPANSION.md` §3's cross-surface-contract rule (this is exactly the "Agent admission (Profile, signed requests, tiers)" row already in that table). **Effort:** design + one grill round, then normal build-phase work. **Lands in:** SPEC §7, ADR-0012 (amend), PLAN-sidecar S4/S6, PLAN-buyer-chat B1/B2.

---

## 5. Narrow the ECO/TCS claim to what it actually proves, and open the broader question — P1

**Findings:** REG-2, cross-referenced MER-2.

ADR-0021's §52/TCS conclusion is sound; its "therefore not an ECO" framing overreaches past §2(45)'s broader operator definition and §9(5)-class liability, neither of which depend on fund custody. Right now the ADR reads more confident than the law it cites supports — exactly the kind of overclaim that looks fine internally and becomes a liability the moment a merchant or a partner repeats it as settled.

**Action:**
- Edit ADR-0021 to explicitly scope its conclusion: "this architecture is not liable for TCS under §52 because it never collects consideration" stays as the confident claim; add a new paragraph naming §2(45) and §9(5) as open questions not resolved by this ADR, with the same "read, not a ruling, get counsel" hedge ADR-0021 already uses well elsewhere.
- Fold this into the same legal-opinion engagement as item #2 (RBI PA/PG) — a single GST/RBI-literate counsel engagement can answer both the narrow §9(5) question and the PA/PG question in one pass, since both turn on "how much does orchestrating-without-custodying count as facilitating."
- Practical guardrail regardless of the opinion: keep the "no version of this system that routes payment through an OpenStore-controlled account should ship without revisiting this ADR" line (already present) and extend it to cover any future feature that adds *ranking, featuring, or curation* to the Index (D0/ADR-0022) — a facilitation argument gets easier to make the more OpenStore's infrastructure does beyond passive plumbing, and ADR-0022 already forecloses ranking for a different (anti-mall) reason that happens to help this one too. Worth cross-referencing.

**Owner:** Founder + tax/GST counsel (same engagement as item #2's RBI opinion where practical). **Effort:** ADR edit ~1 hr; legal opinion is the real cost, share it with #2. **Lands in:** ADR-0021, cross-linked from ADR-0022.

---

## 6. Put e-invoicing/IRN on the roadmap with a stated threshold, not a silent deferral — P1

**Findings:** REG-3, WOO-6.

ADR-0020 correctly solves the numbering-and-gaplessness problem and correctly defers IRN registration — but "stays phase-two, not decided here" reads as "nice to have later," when the actual fact is "merchants above the e-invoicing turnover threshold are non-compliant from day one." This is also the single clearest place a Zoho competitor beats OpenStore on a claim OpenStore is making (WOO-6) — Zoho already ships IRN generation. Both problems have the same fix: be explicit about the boundary instead of silent about it.

**Action:**
- Add a stated limitation to SPEC §13 (the phase-two exclusion list already has a slot for exactly this kind of "not a cut, a consequence" reasoning, per its own COD precedent): "e-invoicing/IRN is not implemented; merchants above the current e-invoicing turnover threshold are not GST-compliant on this system until it ships — check the current threshold before adopting for a business at that scale." State the mechanism (GSTN IRP registration, QR-embedding) so it's clear what's missing, not just that something is.
- Surface the same line at `openstore up` first-run or `/agentic` GST-identity setup (PLAN-merchant-site M2 pricing setup already collects GSTIN/registered-state — the same form is the natural place to add a turnover self-declaration and a warning above the threshold).
- Scope a real D-phase for IRN (new `PLAN-distribution.md` entry or a `PLAN-merchant-site.md` M-phase, whichever owner fits — this is Merchant-side tax-identity work, closer to M2/M3 than to the reach track) rather than leaving it an unscheduled "further step."
- Reframe the competitive claim: D3's pitch should read "GST-correct agent-order quoting with a disputable audit artifact — the layer Zoho doesn't have," not an unqualified "GST-correct," since Zoho's e-invoicing coverage is real and better than OpenStore's today for merchants above threshold.

**Owner:** Founder (SPEC/copy) + whoever takes the M2/M3 GST-identity work. **Effort:** SPEC/copy fix same day; IRN implementation is real scoped work, size it separately. **Lands in:** SPEC §13, PLAN-merchant-site M2, ADR-0020 (add the threshold caveat), PLAN-distribution D3 copy.

---

## 7. Fix the DPDP anonymization gap in `consumer_id`, and state the backup-irrecoverability guarantee explicitly — P1

**Findings:** REG-4, REG-5.

Two distinct fixes bundled because they're both "state an invariant that's currently only true by accident."

**Escalated 2026-09-19 after reading ADR-0011 directly.** The remediation below says a full "forget this person" request "requires deploy-key rotation." That does not work, and the reason is structural: rotation changes the pseudonym for *future* orders, but every `consumer_id` already written stays identical to every other order by that consumer, so the cross-order trail survives the rotation intact. Unlinking history would mean rewriting historical `consumer_id` values — and ADR-0011 puts `consumer_id` into the Transcript and the **sealed evidence bundle**, which ADR-0009 signs and hash-chains. DPDP cross-order unlinkability and receipt immutability are therefore in direct conflict, and no amount of key management resolves it.

The per-consumer-salt escape hatch below has the same defect: it fixes new orders and cannot reach sealed ones.

The honest resolution is to stop implying the unlinking is available and state the retention basis instead — per-order plaintext erasure is real and satisfiable (already true), the pseudonymous cross-order trail is retained under the same lawful basis that requires keeping the tax invoice (GST record retention), and it ages out with the order row on the merchant's stated retention window. That is a defensible position; the current silence is not. Related gap: SPEC §14 implies erasure is unqualified and never names the tax-retention exception that limits it.

**Decide before S1 freezes the bundle shape:** whether `consumer_id` belongs in the sealed sections at all, or whether the attribution panel can read it from the Merchant row like Destination and Contact already do (ADR-0011's own pattern). If the latter works, the conflict disappears entirely and this becomes a schema decision rather than a permanent tension. That is a grill question, not a call to make here.

**Action (consumer_id pseudonymization, REG-4):**
- Accept that `consumer_id` is pseudonymous, not anonymous, and stop implying otherwise anywhere it does (check ADR-0011/0017's language). The fix isn't to make it truly anonymous — the attribution panel (SPEC §10) *needs* the linkability to be useful, so this is an inherent tradeoff, not a bug.
- Add an explicit erasure-scope statement to ADR-0011: a per-order erasure request removes that order's Destination/Contact plaintext (already true) but does *not* sever the consumer's cross-order `consumer_id` trail on that merchant; a full "forget this person" request requires deploy-key rotation, which is a merchant-wide action affecting every consumer, and should be documented as the actual (heavy) mechanism rather than left implicit.
- If a lighter per-consumer unlinking is wanted later, the seam for it is a per-consumer salt rather than one deploy-wide key — worth a one-line note in ADR-0011 as a "if this becomes a live complaint, here's the escape hatch" rather than building it now.

**Action (backup irrecoverability, REG-5's sibling point and PLAN-sidecar S8's backup drill):**
- Add one line to ADR-0011 or PLAN-sidecar S8: "the backup/restore drill (S8) must demonstrate that an erased order's PII commitment salt does not resurrect from a backup, replica, or WAL segment — erasure is not complete until this is tested, not just coded." This turns an assumed property into a tested one, cheaply, since S8 already has a backup/restore drill DONE WHEN — it just needs one more assertion added.

**Action (data-fiduciary allocation, REG-5's main point):**
- Write down, as a binding rule (not an emergent property), that OpenStore Inc. collects zero telemetry, logs, or support data containing Consumer PII from any deployment. This belongs in SPEC §14 next to the existing DPDP paragraph, and ideally in `docs/CODES.md`'s guardrail spirit — a "if you ever add telemetry, it must not carry Consumer PII, and if it does, SPEC §14's fiduciary allocation needs to be revisited" line, mirroring ADR-0021's own "revisit if..." pattern.

**Owner:** Founder (SPEC/ADR text) + whoever builds S8's backup drill (add one assertion). **Effort:** text fixes ~2 hrs; backup-drill assertion is a small test addition. **Lands in:** ADR-0011, SPEC §14, PLAN-sidecar S8 DONE WHEN.

---

## 8. Add Consumer Protection (E-Commerce) Rules 2020 coverage — P1

**Finding:** REG-7.

This is the one area that appears genuinely unresearched rather than deliberately deferred — unlike GST/DPDP, which clearly got real attention, grievance redressal and return/refund timelines don't appear anywhere. Fix by treating it exactly like the other regulatory tracks already in this document: name the obligation, decide what's architecture vs. what's merchant-owned, write the ADR.

**Action:**
- New ADR (`0024-consumer-protection-rules-boundary.md`): name the three concrete obligations (grievance-officer contact display, return/refund/exchange timeline disclosure, country-of-origin/importer disclosure) and decide, per obligation, whether it's a Merchant-owned disclosure (most likely, consistent with how DPDP fiduciary duty is allocated) or something the product should surface structurally (e.g., a `/agentic` or M2 field for grievance-officer contact info that gets rendered on the storefront and in the receipt, the same way GST identity already is).
- The mechanical part is cheap: grievance-officer contact and return-window are both just more typed fields a Merchant enters, matching the project's own "no engine, typed values only" discipline (EXPANSION.md §2) — this slots naturally next to `merchant_tax_identity` in PLAN-merchant-site M1's schema, not a new subsystem.
- Return/refund timeline disclosure has one real interaction with existing logic worth flagging in the ADR: SPEC §6's refund flow doesn't currently model a *timeline commitment*, only that refunds happen. Whether Consumer Protection Rules require a stated turnaround time (and whether the receipt/Timeline should show it) is the one part of this that's a design decision, not just a form field — flag it for a grill rather than deciding it unilaterally in this remediation pass.

**Owner:** Founder + consumer-protection-literate counsel (can likely be folded into the same GST/DPDP counsel engagement — Indian e-commerce compliance counsel typically covers all three). **Effort:** ADR ~half day; schema/field additions are normal M1/M2 work once scoped. **Lands in:** new ADR, PLAN-merchant-site M1 (schema), M2 (admin fields).

---

## 9. Give merchants a real key-loss story, not a checkbox — P1

**Finding:** MER-1.

**Downgraded 2026-09-19 — the verification this item asked for came back positive, which changes what the item is.** ADR-0014 already states, and ADR-0009 already guarantees, that historical bundles verify forever against their own JWKS snapshot; rotation is additive and history is never re-signed. So key loss costs the ability to sign *new* receipts and nothing else — MER-1's "catastrophic, irreversible" is wrong, and the reassuring fact is already true and merely unpublished. This is copy work (say it where a merchant reads it), not a robustness gap, and the Shamir option below should not be built.

The single-operator failure mode here is real and the current mitigation (an "acknowledged as saved" nag at first-run) is thin. This doesn't need a vendor-escrow reversal (that would undercut ADR-0014's whole self-custody premise, which is correct) — it needs a better *first-run ritual* and a clearly documented recovery runbook, which is cheap relative to the risk.

**Action:**
- Strengthen the first-run export flow (PLAN-sidecar S8) beyond a single acknowledgment: require the encrypted export to be downloaded *and* optionally offer a second export destination (print-a-QR-and-recovery-phrase pattern, or "email yourself a copy" with a clear warning that email isn't a vault) — the goal is redundancy, not new custody, so this is UX work, not architecture work.
- Write an explicit, merchant-facing "what happens if I lose my key" runbook as part of the `/agentic` docs: re-enrollment is possible, here's exactly what breaks (can't sign new receipts, existing receipts remain independently verifiable per ADR-0009's chain design — check this is actually true and state it, since "existing receipts stay valid" is a genuinely reassuring fact if accurate and currently unstated), and here's the exact sequence to recover.
- Consider (flag for a grill, don't decide unilaterally) whether a Shamir's-Secret-Sharing-style split export (e.g., 2-of-3 shares held by the merchant, a trusted contact, and optionally a printed copy) is worth offering as an opt-in stronger mode — this is the kind of thing that should be evaluated against ADR-0014's stated design goals before adding, not bolted on reflexively.

**Owner:** Whoever owns PLAN-sidecar S8's first-run flow. **Effort:** UX/copy work, ~2-3 days; the optional split-key mode is a separate, larger design decision — don't block the cheap fix on it. **Lands in:** PLAN-sidecar S8, `/agentic` docs, ADR-0014 (add the runbook reference).

---

## 10. Give the sidecar a real alerting path for the one failure mode SPEC itself flags as dangerous — P1

**Finding:** MER-3.

SPEC §10's "the sidecar sends no email or SMS" was written correctly for *Consumer/Merchant transactional notifications* (that's the Merchant-site's job, since it owns the Contact Point — the reasoning is sound). But it got applied as a blanket rule that also silences the sidecar's own operational alerts (overdue holds, reconciler drift, wedge detection) — which SPEC §14 separately says a Merchant needs to see. These are two different notification classes that got conflated into one rule.

**Action:**
- Amend SPEC §10 to scope the "no email/SMS" rule explicitly to Consumer/Merchant-facing *transactional* notifications (order confirmation, refund, shipped) — the ones tied to a Contact Point and owned by the Merchant site. Add a new, narrow exception: sidecar *operational* alerts (overdue holds past deadline, reconciler drift beyond a threshold, webhook lag) may notify the Merchant operator directly through a channel the Merchant configures at `/agentic` (webhook URL, or a pluggable sender matching M4's existing "log-only default" pattern so the demo still needs no external account).
- This is a small, well-contained addition — it reuses M4's already-designed "pluggable sender with log-only default" pattern (PLAN-merchant-site M4), just pointed at the sidecar's own operational events instead of Merchant-site's transactional ones, and doesn't touch the money core at all.
- Land it in PLAN-sidecar S8, next to the existing counters-and-observability DONE WHEN, since that's already the phase that owns "somebody can see it."

**Owner:** Sidecar-plan owner (S8). **Effort:** design ~1 day, implementation normal S8 scope. **Lands in:** SPEC §10 (scope the rule), PLAN-sidecar S8 (add the alert channel).

---

## 11. Model Reserve Pay's real production failure modes before building `upi-block` — **P0, promoted 2026-09-19**

**Finding:** PSP-2 — understated. This is now the most serious open item in the register.

Reading NPCI/UPI/OC-228/2025-26 directly (the circular REG-1 cited but nobody had opened) turned up a requirement that may invalidate `upi-block`'s reason for existing: for fixed-amount goods, "delivery of goods and service should only be after the confirmation of successful debit," with post-delivery debit allowed only where the amount is not fixed (cabs, EV charging). ADR-0018 designs `upi-block` as capture-at-delivery on fixed-amount baskets — the case the circular appears to exclude. If the strict reading holds, `upi-block` is not a COD instrument at all and ADR-0018 keeps only its cash path.

Three readings and the resolution path are written into ADR-0018 under "Unresolved." It is an acquirer question before it is a design question, and it is Q4 of `docs/COUNSEL-BRIEF.md`.

Three further circular constraints are now in ADR-0018 and unmodelled by the Ledger: one block per customer per merchant at a time (needs a refusal code, not a Provider error); mandated consumer-initiated revocation, which makes `RELEASE` fire mid-transit and can fail a capture against a live block; and debit timeouts treated as declines, reversed real-time, retryable at most 3 times in 24 hours with no retries for other declines.

This blocks S3's COD path, not S3 itself. The original analysis below stands and is additive to the above.

**Action:**
- Extend ADR-0018 (or a follow-on ADR) to name and give reason codes for: block-expiry-mid-transit (goods dispatched, block lapses before delivery — decide the re-authorization UX now, since "ask the Consumer to re-tap for an order they already placed" is a real product decision, not an edge case to discover in production), partial capture against a block (does a partial refund on a `upi-block` order partially release the block, or does full capture happen and then a standard refund follow? — pick one and state it), and bank-side decline of the block itself at order time (needs its own reason code distinct from a generic Gate refusal, per SPEC §12's closed-reason-code discipline).
- This is Gate/Ledger design work, so it belongs in PLAN-sidecar S3/S4 alongside the rest of ADR-0018's Ledger consequences, not a separate track.

**Owner:** Sidecar-plan owner (S3/S4). **Effort:** design work before S3 build starts on the COD path; not urgent until Reserve Pay integration is actually scheduled. **Lands in:** ADR-0018 (extend), PLAN-sidecar S3/S4.

---

## 12. State that the receipt is out-of-band evidence relative to NPCI's actual dispute process — P2

**Finding:** PSP-4.

The `upi-pin` binding-by-reference argument (ADR-0017) is sound; what's missing is one honest sentence about where the evidence actually goes when a dispute happens. This is a documentation fix, not a design change — the receipt bundle remains valuable as merchant-side evidence, it's just not natively wired into NPCI's UDIR tooling.

**Action:** Add one paragraph to ADR-0017 or SPEC §14's disputes section: "the sealed receipt is the Merchant's own evidence pack for a manual submission into whatever channel adjudicates a dispute (bank ombudsman, NPCI grievance, UDIR) — it is not natively ingested by NPCI's dispute-resolution tooling, and no integration with that tooling exists or is planned in v1." This sets correct expectations for both merchants and any future PSP partner reading the spec.

**Owner:** Founder (text only). **Effort:** <1 hour. **Lands in:** SPEC §14, ADR-0017 (cross-reference).

---

## 13. Spike Justpay/Airpay's real API shape before committing to "same shape" — P2

**Finding:** PSP-5.

**Action:** Before PLAN-sidecar S3 treats Justpay/Airpay as a known quantity ("later in the same shape"), do a short API-surface spike against their published docs — specifically for the `block/capture-block/release-block` optional capability, which is the piece most likely to diverge. If they don't expose an equivalent primitive, ADR-0013's provider-declares-methods model already handles this gracefully (they simply don't declare `upi-block` support) — so the fix may just be confirming the trait already degrades correctly, and adjusting the "same shape" language in SPEC §8 to "same trait, capability sets may differ" if the spike finds real divergence.

**Owner:** Whoever integrates the second/third Provider adapter. **Effort:** 1-2 day spike, deferred until that work is actually scheduled. **Lands in:** SPEC §8 (soften "same shape" claim if the spike finds divergence), ADR-0013 (no change needed if the declared-methods model already covers it — likely outcome).

---

## 14. Separate "adapter can call this API" from "this Merchant's PSP account is entitled to use it" — P2

**Finding:** PSP-6.

**Action:** Add a distinct reason code (e.g. `method-not-entitled`, alongside the existing `method-not-supported`) for the case where a Provider call fails because the PSP itself hasn't authorized that method for this Merchant's account, distinct from the Merchant simply not having enabled it in `/agentic`. This is a small addition to the closed reason-code registry (`docs/CODES.md` per SPEC §12's discipline) plus a Provider-trait contract note that adapters should surface PSP-side authorization failures distinctly from client-side configuration failures where the PSP's API makes that distinguishable.

**Owner:** Sidecar-plan owner (S3, Provider trait). **Effort:** small, normal S3 scope. **Lands in:** `docs/CODES.md`, PLAN-sidecar S3, ADR-0013 (note the distinction).

---

## 15. Fix D3's WordPress-specific overclaims and gaps — P2 (blocks D3's grill, not urgent pre-D3)

**Findings:** WOO-1 through WOO-5, WOO-7. Bundled since all land in the same unstarted D3 grill (`EXPANSION.md` §5: "`PLAN-distribution.md` is not in this rotation... gets its own grill when that gate goes green"). None of these need fixing today — they need to be *queued* for that grill so they don't get rediscovered live.

**Action, filed as pre-grill agenda items for D3:**
- **WOO-1:** Reword "entered without anyone's approval" to "no partner agreement or business-development gate — WordPress.org's standard plugin-review process still applies, including its external-service disclosure requirements."
- **WOO-2:** Add a concurrent-write race test to D3's DONE WHEN: native-WooCommerce-checkout racing an agent `reserve` call, not just the sidecar's own compare-and-set in isolation.
- **WOO-3:** The version half is **disproved** — WooCommerce 11.0 shipped 4 Aug 2026 and 11.1.1 is current (18 Sep 2026), so SPEC §5's "Woo 11.0 parity" is a real pin; consider moving it to 11.1 with a date. The HPOS half stands: state explicitly whether D3 targets HPOS, legacy postmeta storage, or both, and test against whichever is claimed.
- **WOO-4:** Add an explicit hosting-compatibility statement to D3: name the realistic target (self-hosted/VPS WordPress, not managed hosting that forbids arbitrary containers), and consider whether a lighter-weight deployment mode (e.g., a hosted sidecar option for merchants who can't run Docker) is worth a future ADR — flag for the grill, don't decide here.
- **WOO-5:** Not actually an open choice — ADR-0010 already decides it. Door 9 reads the existing tax plugin's computed output; the "adapter bypasses the plugin for agent orders" alternative *is* the two-engines-disagree bug class ADR-0010 exists to prevent, moved one layer out. Write it into D3 as a constraint inherited from ADR-0010, not as a question for the grill.
- **WOO-7:** Add a one-line Multisite exclusion or support statement to D3's DONE WHEN.

**Owner:** Whoever picks up D3 when its grill opens. **Effort:** all text/scoping work, ~half day total, sequenced before D3 build starts (already gated behind `PLAN.md` step 7 per `PLAN-distribution.md`'s entry condition, so there's no urgency to act before that gate is green). **Lands in:** `PLAN-distribution.md` D3 (all six sub-points).

---

## 16. Reframe D3's competitive pitch against Zoho with the narrowed claim from item #6 — P2

**Finding:** WOO-6 (remediated jointly with REG-3 in item #6 above; listed here only to confirm it's covered, not duplicated).

---

## 17. Tighten the conformance-badge language so it doesn't imply UCP certification — P3

**Finding:** SHF-3 (mostly a correction *in the project's favor*; one loose end).

**Action:** Add one qualifying phrase to SPEC §9's badge description: "this badge is OpenStore's own conformance statement; UCP does not currently operate a formal certification program, so no third-party body has reviewed or endorsed it." Cheap, prevents future misreading, no other change needed — SHF-3 otherwise confirms ADR-0016's existing self-correction on redirect completion was accurate and doesn't need touching.

**Owner:** Founder (text only). **Effort:** <30 min. **Lands in:** SPEC §9.

---

## 18. Restate the India-scoping of the regulatory-band argument for any global-facing material — P3

**Finding:** SHF-4.

**Action:** Wherever ADR-0016's "the Indian picture favours our tap" argument gets reused outside the ADR itself (pitch decks, README, partner conversations), carry its own scoping sentence with it: "this argument applies to the Indian regulatory environment specifically and provides no comparable cover in jurisdictions without equivalent card-not-present/UPI rules." This is a discipline note for whoever writes external-facing copy, not a spec change — flag it in `docs/EXPANSION.md`'s pre-grill checklist as a reminder if ADR-0016-derived claims start appearing in non-ADR documents.

**Owner:** Founder (whoever writes external copy). **Effort:** trivial, ongoing discipline rather than a one-time fix. **Lands in:** no document change required; noted here as a standing reminder.

---

## 19. ~~Cross-link ADR-0021 and SPEC §2~~ — LANDED 2026-09-19 (carried by item #3's SPEC §2 edit; also covers ADR-0023)

**Finding:** REG-8.

**Action:** Add a one-line pointer in SPEC §2's Payment Provider definition: "this fact is load-bearing for ADR-0021's ECO/TCS boundary — any change here reopens that ADR." Mirrors the discipline `EXPANSION.md` already asks for elsewhere.

**Owner:** Founder. **Effort:** <15 min. **Lands in:** SPEC §2.

---

## 20. Document the escrow-zero/REVERSAL framing honestly in merchant-facing copy — P3

**Finding:** MER-6.

**Action:** Wherever the product's value proposition gets stated to a prospective merchant (README, install flow, `/agentic` documentation), make sure "disputable audit artifact" is paired with what it actually means: "this system produces evidence for a dispute; it does not prevent chargebacks or reversals, and money taken back by a bank or NPCI adjudication is recorded, not blocked." One sentence, prevents a merchant over-trusting the system as loss-prevention rather than loss-documentation.

**Owner:** Founder (copy). **Effort:** <30 min. **Lands in:** README / install-facing copy (not currently tracked in this repo's doc set — add wherever that copy eventually lives).

---

## 21. Credit notes against a dispatched-and-invoiced RTO — P1, new 2026-09-19

**Findings:** none of the five stakeholder reviews; this reopens the unclosed half of `docs/REVIEW-findings.md` **F12** ("no tax-invoice series and no credit notes"). ADR-0020 closed the series half and left credit notes where it found them.

The two ADRs interact in a way neither notices. ADR-0020 assigns the invoice number **at dispatch**. ADR-0018's RTO path returns a dispatched order, writes no Ledger entry ("nothing moved"), restocks, and marks it `cancelled` with an `rto` reason. But a tax invoice was already issued against that dispatch, and under GST an issued invoice is unwound by a credit note (§34) with its own consecutive series and its own GSTR-1 reporting — it is not made to disappear by the order ending in `cancelled`. On COD, which ADR-0018 itself puts at ~60% of Indian ecommerce with 76–83% of RTO volume, this is the *common* path, not the edge.

MER-5 waved the area off as deferred operational tooling. The operations (reattempt SLAs, NDR, restocking fees) are genuinely deferrable; the credit-note obligation against an already-issued invoice is not, because it is created by a decision this repo already took.

**Action:** extend ADR-0020 (or a follow-on) with the credit-note series — same gapless discipline, same financial-year scoping, same atomic increment, issued whenever a dispatched-and-invoiced order is cancelled, RTO'd, or refunded in part. State explicitly that a `cancelled`/`rto` order that was never dispatched still gets nothing, which is the existing rule and stays correct.

**Owner:** Founder + whoever takes M2/M3's GST work. **Effort:** ADR ~2 hrs; the sequence table and issuance point are a small extension of ADR-0020's existing mechanism. **Lands in:** ADR-0020 (extend), ADR-0018 (cross-reference from the RTO path), `PLAN-merchant-site.md` M3.

---

## Not remediated here — explicitly deferred by design, confirmed correct as deferred

- **MER-5 (COD/RTO operational tooling — reattempt SLAs, NDR handling, restocking fees):** ADR-0018 already scopes this out deliberately ("stay out — they are policy on top of this path, not changes to it"). No remediation needed; this is honest scoping, not a gap in disguise. Worth a merchant-facing note (similar spirit to item #3) that ledger correctness for COD doesn't include RTO operations tooling, so a merchant doesn't assume otherwise.
- **MER-7 (demo topology vs. self-host reality):** ADR-0019 already states the two are different and why. No remediation beyond making sure install-facing copy (item #3's fix) doesn't accidentally borrow demo-polish language for the self-host path.
- **PSP-7, SHF-5, REG-6:** all three reviewers independently concluded the project already self-corrects or self-scopes accurately in these spots. No action; listed for completeness so they don't get re-litigated in a future review pass.

---

## Sequencing — revised 2026-09-19 after the verification log

0. **Verify before acting.** Done for the five claims in the log above; three were wrong. Any remaining item resting on an external fact (#4's key-array field name, #6's e-invoicing threshold, #13's Justpay/Airpay capability, #15's WP.org policy) gets the same treatment before its text lands.
1. **Landed this pass:** #1 (inverted), #2's ADR, #3's SPEC half, #19. Files touched: `docs/EXPANSION.md` §1, ADR-0016, ADR-0018, SPEC §2, new ADR-0023, new `docs/COUNSEL-BRIEF.md`.
2. **Next, and now the top of the list:** **#11** — the circular may forbid capture-on-delivery for fixed-amount goods, which decides whether `upi-block` exists. Acquirer question first (COUNSEL-BRIEF Q4), then a grill on ADR-0018's three readings, before S3 touches the COD path.
3. **Decide before S1 freezes the bundle shape:** #7's escalation — whether `consumer_id` belongs in the sealed receipt at all. Cheap now, permanent later.
4. **One counsel engagement, already briefed:** #2 (Q1), #5 (Q2), #8 (Q3) — plus #11 as Q4 to the acquirer. Sent as `docs/COUNSEL-BRIEF.md`.
5. **Before the first real merchant installs:** #3's install-copy half, #9 (now copy only — say that old receipts survive key loss), #10, #20, #6's SPEC/copy half.
6. **Before D2/D5 is worth pursuing:** #4 — smaller than it looked; serve `/.well-known/ucp` and resolve the `UCP-Agent` header.
7. **Before D3's grill:** #15 (with WOO-3 and WOO-5 corrected above), #6/#16 together.
8. **Scoped work, not urgent:** #21 (credit notes, before M3 builds invoicing), #6's IRN implementation.
9. **Before a second/third PSP integrates:** #12, #13, #14.
10. **Whenever convenient:** #17, #18.
