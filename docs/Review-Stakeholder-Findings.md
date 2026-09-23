# Stakeholder validation — 2026-09-19

> Historical stakeholder review. Formerly `docs/REVIEW-stakeholder-findings.md`. Frozen.

Five adversarial reviews of `SPEC.md`, `CONTEXT.md`, the four `PLAN-*.md` files, `docs/EXPANSION.md`, `docs/REVIEW-findings.md`, `docs/DRAFT-discovery-registry.md`, and ADRs 0001–0022, each run from one stakeholder's seat: a merchant deciding whether to self-host, a payment-processor exec deciding whether to integrate as a Provider, a Shopify exec assessing UCP interop and competitive threat, a WooCommerce/Zoho exec assessing the D3 plugin track, and a regulator spanning GST/RBI/DPDP/consumer-protection. Each reviewer had full document context and was told to find real problems, not perform a courtesy pass; the payment-processor, Shopify, and regulator reviews additionally verified external claims against current sources (cited inline).

This file is the raw output. See `docs/REVIEW-remediation.md` for proposed fixes. Same rule as `docs/REVIEW-findings.md`: this register routes work, it does not do it — delete a row's source finding here once its remediation lands.

IDs: `MER-*` merchant, `PSP-*` payment processor, `SHF-*` Shopify, `WOO-*` WooCommerce/Zoho, `REG-*` regulator.

---

## Merchant perspective

**Scope note (MER-0):** No code exists on this branch (`docs/REVIEW-findings.md` says so explicitly). Every finding below is against a *specification*, not a runnable product — read "value proposition" as "value proposition of the eventual v1."

**MER-1 — Key custody is a single point of catastrophic, irreversible failure.**
ADR-0014: keys never leave the sidecar; recoverable only from the Merchant's own encrypted export. ADR-0014 itself: "Losing the key without an export means the Merchant can sign no new receipts and must enroll a fresh key." For a solo operator (dead laptop, no backup discipline), that's every future sale unsignable until manual re-enrollment, with no described secondary custody (split-key, trusted third party, even a "print and put in a safe" ritual) — just an "acknowledged as saved" checkbox at first-run.

**MER-2 — GST/legal compliance is fully delegated to the merchant with only a "get a lawyer" disclaimer.**
Three separate live gaps, silently merchant-owned: (a) ADR-0021's ECO/TCS read is explicitly "a read, not a ruling" — if wrong, the *merchant* owes retroactive TCS/GSTR-8, not OpenStore, and there's no in-product disclaimer at `openstore up` or `/agentic` telling them to get their own opinion; (b) ADR-0020 explicitly defers e-invoicing/IRN — a merchant who crosses the turnover threshold gets no warning at that threshold; (c) SPEC §14's "DPDP obligations sit with the Merchant... not to make compliance impossible" is a low bar — no consent-record tooling, no breach-notification flow, no grievance-officer designation support.

**MER-3 — "Merchant is truth" means running two systems in permanent lockstep with reconciliation as an assumed background chore.**
Solo operator now runs store + sidecar (+ possibly WooCommerce under D3), with `/healthz`/`/readyz`, reconciler poll, and overdue-hold monitoring layered on top. SPEC §14 names the failure mode honestly ("a wedged or stopped sidecar holds stock indefinitely and the only defence is that somebody can see it") but SPEC §10 says the sidecar sends no email or SMS *at all*, for any notification class — including this one. No alerting channel exists for the one failure mode the spec itself flags as dangerous.

**MER-4 — Where does the first agent-driven order actually come from?**
`docs/REVIEW-findings.md` R2 (still open, not cleared) already flags the Agent Profile as an invented schema. Concretely for a merchant: no major assistant surface natively speaks this discovery format today, and D0–D2 (the phase-two reach work) haven't started. The honest sentence is "you get a correct, auditable checkout API that nothing external calls yet," not SPEC §2's "any Buyer Agent." This caveat exists in the internal ADR/PLAN trail a merchant would never read, not anywhere merchant-facing.

**MER-5 — COD financial exposure is honestly scoped but the operationally hard half (RTO) is entirely out.**
ADR-0018 is candid: "the Merchant asserts that cash was collected, and nothing in the system can check it. That is correct and not a weakness." Fine as scoping. But COD drives 76–83% of RTO volume (ADR-0018's own stat) and "Restocking fees, partial COD collection, and NDR/reattempt flows stay out" — the actual cost driver of COD is unaddressed while ledger correctness is solved.

**MER-6 — Escrow-zero/REVERSAL documents disputes, it does not prevent or recover from them.**
"negative net raises an alert, never a refusal" is correct bookkeeping design, but a merchant reading "disputable audit artifact" as the value prop needs to understand the system documents disputes; it doesn't win them or return the money.

**MER-7 — Install-story polish gap between the public demo and self-host reality.**
ADR-0019's three-Fly-app demo topology is explicitly not what `openstore up` targets (docker-compose + VM + Caddy, per ADR-0019's own text). Not a contradiction, but "a Merchant who can point DNS can run it" (SPEC §14) undersells the ongoing operational surface (VM selection, TLS renewal, key-export backup, health monitoring) a solo operator now owns permanently.

---

## Payment processor perspective

**PSP-1 — "The sidecar never custodies funds" (ADR-0021) answers the GST question, not the RBI one.**
RBI's Payment Aggregator framework doesn't hinge on fund custody alone — it also covers who *initiates and orchestrates* the payment request on the merchant's behalf. The sidecar calls `make-link`/`block` and receives webhooks — a technical intermediary orchestrating payment even though settlement lands in the merchant's own account. No ADR asks this RBI PA/PG question at all; ADR-0021 only reasons about GST §52. A real PSP's compliance team raises this before reading any of the money-core rigor.

**PSP-2 — Reserve Pay's real mechanics are asserted, not verified, and the escrow-zero equivalence claim is unsupported.**
ADR-0016's "Reserve Pay blocks around ₹10,000 for up to 90 days" describes a capability nobody on the team has integrated against, and (per REG-1 below) the figure itself is wrong. Unmodeled failure modes: block-expiry-mid-transit (goods dispatched, block lapses before delivery — no re-authorization UX described anywhere); partial capture against a block (ADR-0018 only models full RESERVE→CAPTURE→RELEASE); bank-side decline of the block itself at order time (no distinct reason-code family named).

**PSP-3 — Idempotency-key scoping is under-specified against real PSP webhook behavior.**
`order_id:attempt` + `event_id` dedupe is stated as a property, not demonstrated against Razorpay's actual event catalog (`payment.authorized`/`payment.captured`/`order.paid` overlap and can arrive out of order; webhook retries can carry regenerated signatures). PLAN-sidecar S3's "webhook forgery/replay/reorder tests pass" names no specific PSP quirks the tests encode.

**PSP-4 — `upi-pin` basket-binding-by-reference is real UPI practice, but the evidentiary chain into NPCI's actual dispute process is unacknowledged.**
ADR-0017's framing (amount bound by bank, basket bound by reference via approve-page rendering) is honest and matches real UPI mechanics — not fabricated. But NPCI's UDIR dispute-resolution process works off transaction reference numbers and doesn't natively ingest a third-party evidence bundle; the sealed receipt is *out-of-band* evidence that would need manual submission into whatever channel adjudicates (bank ombudsman, NPCI grievance). Nothing in SPEC/ADR-0016/0017 states this explicitly.

**PSP-5 — "Fake + Razorpay in v1, Justpay/Airpay later in the same shape" underestimates real API divergence, specifically the `block` capability.**
Reserve Pay is an NPCI-level UPI feature individual PSPs expose with very different maturity; Razorpay's public self-serve block/capture/release API maturity, and Justpay/Airpay's actual support, are unverified in the docs. The "same shape" claim (SPEC §8, ADR-0013) needs a spike, not an assumption.

**PSP-6 — ADR-0013's method-declaration model conflates adapter capability with PSP-side entitlement.**
"Each provider adapter declares its supported method set at boot" doesn't distinguish "the code knows how to call this API" from "this specific Merchant's PSP account is authorized to use it" (methods like UPI Autopay/Reserve Pay are frequently gated behind separate MCC/risk-tier approval). A merchant could enable a method in `/agentic` that fails at the PSP's own authorization layer — a distinct failure class the closed reason-code set doesn't name (`method-not-supported` is config-side only).

**PSP-7 — UAP positioning is self-aware and doesn't need action.**
ADR-0016 already says "It is positioning, not a design change" and "watch UAP; do not build for it while its rules are unpublished." A payments exec will discount this section until UAP has a published spec — but the doc already reflects that caution. No fix needed; flagged for completeness.

*Not a finding, noted for synthesis:* the money math itself (escrow-zero, idempotency-key design, GST apportionment logic in PLAN-merchant-site M3) is unusually rigorous — above the bar most vendors ship with. The structural risk is regulatory (PSP-1), not arithmetic.

---

## Shopify exec perspective

*(External claims verified via WebFetch/WebSearch against `ucp.dev/specification`, current as of this review, not against the project's own characterization.)*

**SHF-1 — The Agent Profile isn't just differently shaped from UCP's — it's unreachable by a real UCP agent, and SPEC never pins the well-known path.**
**Corrected 2026-09-19:** the path in this finding is wrong. UCP's profile is at `/.well-known/ucp` (unified for both sides), and an agent's own profile is located from a `UCP-Agent: profile="…"` request header rather than any well-known path. The finding's *conclusion* — two discovery mechanisms that don't compose — survives; its prescription would have sent the fix to the wrong place. See remediation item #4. Original text follows.

UCP's real agent profile lives at `/.well-known/ucp-agent`: RFC 9421/ES256-signed like OpenStore's, but with a `signing_keys` JWK Set plus a `ucp.capabilities` declaration OpenStore's shape lacks. Worse: `PLAN-buyer-chat.md` B1/B2 never names the well-known path the chat publishes at, and no ADR fixes it. A UCP-native agent publishing at the real path in the real shape would not be found by OpenStore's fetcher — S6 only translates checkout envelopes, not identity/admission. This is a sharper version of `docs/REVIEW-findings.md` R2, not a restatement of it: two discovery mechanisms that don't compose, not a style mismatch.

**SHF-2 — ADR-0016's "permissionless doors" premise doesn't match how the two live AI shopping surfaces actually source catalogs.**
Verified: ChatGPT Shopping sources listings via Bing Merchant Center feed submission; Perplexity Shopping requires enrollment in a gated Merchant Program. Neither does scheduled-pull ingestion against an arbitrary merchant-hosted feed URL the way `PLAN-distribution.md` D1 assumes. D1, even fully built, reaches zero traffic on either mainstream surface without the bilateral submission work D5 explicitly defers. SPEC §13's "reach is deliberately absent from v1" undersells how large this gap is versus ADR-0012's "any Buyer Agent, no pre-registration" — true for a hand-rolled MCP client, not for either surface a merchant actually cares about.

**SHF-3 — Correction in the project's favor: redirect/escalation completion is real UCP conformance, not a deviation needing an apology.**
Verified against `ucp.dev/specification/shopping/checkout/embedded/`: UCP explicitly defines `requires_escalation` with a mandatory `continue_url`; both embedded and redirect completion are first-class, spec-defined paths. ADR-0016 already corrected SPEC §9's earlier overclaim on this (its own text: "SPEC §9's badge wording overstates this and is corrected per R1") and that correction holds up. One loose end: UCP has no publicly documented formal conformance-certification process, so "the badge names its deviations inline" is OpenStore's own invention layered on an ecosystem with no equivalent — harmless, but shouldn't imply a UCP body would recognize the badge.

**SHF-4 — Native in-agent completion is exactly what Shopify/OpenAI ship via delegated credentials, and OpenStore concedes it by design, globally.**
ChatGPT Instant Checkout runs ACP with a Stripe Shared Payment Token — precisely what ADR-0008/0013 refuse categorically. On any surface Shopify or OpenAI controls, this is total concession of the completion UX by architecture, permanently. Legitimate and defensible on security/regulatory grounds, but the "Indian regulatory band favors us" argument (ADR-0016) is explicitly India-scoped and provides zero cover elsewhere — the doc says so itself, worth restating for a global reader.

**SHF-5 — The merchant wedge is real but correctly self-scoped as tiny relative to Shopify's addressable market.**
`docs/DRAFT-discovery-registry.md` already states this correctly ("Not competing with Shopify/Stripe — serving merchants who don't use them"). No finding beyond confirming the self-assessment is honest; the credible wedge (self-hosted D2C with India-specific GST granularity) is real and narrow, not a Shopify threat.

*Not independently verified — flagged for another reviewer:* whether a formal UCP conformance-test program exists at all (none found — negative result, not confirmed absence).

---

## WooCommerce / Zoho Commerce exec perspective

*(Verified against current WordPress.org plugin-review policy and general WooCommerce/Zoho product knowledge; flagged where confidence is lower.)*

**WOO-1 — "Entered without anyone's approval" (PLAN-distribution D3) overstates WordPress.org's actual gate.**
WordPress.org plugin review requires disclosure of any external service call (who, what data, ToS/privacy links), no undisclosed telemetry, GPL-compatible licensing, and automated security scanning (Plugin Check) gating every release. A plugin whose entire purpose is signing HMAC/ES256 requests to an external self-hosted sidecar and holding OAuth credentials is exactly the case the guidelines single out. It will likely clear review, but "without anyone's approval" is false messaging — D3 should say "no partner agreement or business-development gate," not imply no review gate exists.

**WOO-2 — "Merchant is truth" collides with WooCommerce's real concurrency model.**
ADR-0001/SPEC §5's "external sale wins automatically, next gate re-reads" is sound against one writer (native checkout) but not against WooCommerce's real extensibility surface — subscriptions, bookings, multi-vendor, POS plugins all mutate stock via hooks the adapter can't see except by polling. D3's DONE WHEN never tests concurrent native-checkout-vs-agent-reserve races, which is the actual failure mode a WooCommerce architect asks about first.

**WOO-3 — "Woo 11.0 parity" claim needs a version pin and doesn't account for HPOS.**
WooCommerce's order storage moved to HPOS (custom tables) as default for new stores since ~8.2, with legacy postmeta still supported. Neither SPEC nor PLAN-merchant-site says which mode D3's adapter targets or whether behavior is identical across both — and "Woo 11.0" doesn't match WooCommerce's actual current versioning scheme; verify this isn't a stale/invented reference before it ships. **— Version half disproved 2026-09-19:** WooCommerce 11.0 shipped 4 Aug 2026, 11.1.1 is current (18 Sep 2026). The HPOS half stands.

**WOO-4 — Hosting compatibility for the sidecar is entirely unaddressed for the realistic WooCommerce merchant base.**
The realistic Woo merchant is on managed hosting (WP Engine, Kinsta, SiteGround) where arbitrary long-running Docker containers aren't installable — the same population `openstore up` already assumes VM/compose access for. D3 says nothing about where the sidecar half runs relative to a WordPress install, materially shrinking the addressable audience to self-hosted/VPS WordPress users without saying so.

**WOO-5 — Double-tax-engine risk against WooCommerce's existing GST plugin ecosystem.**
D3's wedge is "correct GST on agent orders," but Woo merchants doing India GST today already run GST-compliant invoicing plugins computing HSN/SAC-based CGST/SGST/IGST inside WooCommerce's own tax engine. SPEC/ADR-0010 insist the sidecar never computes tax, only verifies door 9's output — meaning door 9 has to *read* the existing plugin's tax calculation rather than reimplement it, and nothing in D3 or M3 (which specs the demo store's own hand-rolled tables) addresses this. If door 9 ends up re-deriving GST independently, that's a second tax engine racing the merchant's real one — the exact "silently disagree" bug class ADR-0010 exists to prevent, moved one layer out.

**WOO-6 — Zoho comparison: the e-invoicing/IRN gap is already closed on Zoho's side, open on OpenStore's.**
Zoho Books/Commerce already ships IRN generation against the government IRP, auto-triggered above the e-invoicing turnover threshold, integrated with GSTR-1/GSTR-3B filing. ADR-0020 explicitly defers this. For merchants above that threshold — a meaningful fraction of the D2C segment D3 targets — Zoho is already doing the harder, government-integration half of the compliance story OpenStore claims as differentiation. The "GST-correct" pitch needs to narrow to what's actually differentiated (the Gate/Transcript/receipt model), or a Zoho rebuttal isn't pre-empted anywhere.

**WOO-7 — Firewall claim doesn't survive WordPress Multisite.**
D3's "fourth root, imports nothing from sidecar" works for single-site WordPress. Multisite (network-activated plugins sharing one DB) isn't mentioned anywhere in D3's DONE WHEN — ADR-0007's 1:1 sidecar-per-merchant assumption is a single-site assumption baked in silently.

*Overall assessment:* the wedge argument (GST depth + audit artifact for agent orders, serving merchants Shopify/Zoho's agentic routes don't reach) is directionally real, not dismissible. But D3 understates WordPress.org's review gate, is untested against Woo's real concurrency/HPOS behavior, is silent on hosting compatibility for the exact segment the channel reaches, and doesn't pre-empt the double-tax-engine and e-invoicing-gap rebuttals a Zoho counterpart raises immediately. None are money-core problems — all are timely input for D3's still-unheld grill (per `EXPANSION.md` §5, D3 "gets its own grill when that gate goes green").

---

## Regulator perspective

*(All legal claims verified against current sources as of this review; confidence noted per item; sources listed at end.)*

**REG-1 — ~~Reserve Pay cap cited in ADR-0016/ADR-0018 is materially wrong.~~ DISPROVED 2026-09-19.** *(Reviewer's stated confidence: high. Outcome: wrong.)*
The circular this finding cites was opened directly (it is a scanned PDF; read as page images). NPCI/UPI/OC-228/2025-26, 8 October 2025, states twice — once under issuer obligations, once under merchant/acquirer obligations — "maximum of Rs.10,000 of block limit and up to 90 days." **The ADRs were right and this finding was wrong**; acting on it would have introduced the 10x error it alleged. The circular did turn up a far more serious problem with ADR-0018, now tracked as remediation item #11. Retained here as a record of how the finding failed, not as work. Original text follows.

~~Both ADRs state "around ₹10,000 for up to 90 days." Current NPCI circular (OC No. 228, FY 2025-26) sets the Reserve Pay block limit at **₹1,00,000** for most MCCs (90-day window is correct).~~ This is a 10x understatement that undermines ADR-0016's entire "we compete above the delegation cap, concede below it" argument — the real addressable band is far larger than claimed, and any external-facing statement built on the wrong figure misstates the product's own reach.

**REG-2 — ADR-0021's ECO/TCS read is narrower than the question it claims to answer.** *(Medium confidence.)*
§52(1) TCS liability is correctly conditioned on the operator collecting consideration — ADR-0021's core argument holds on this narrow point. But §2(45)'s ECO definition ("owns, operates or manages... a platform for electronic commerce") is broader than the TCS trigger, and separate mechanisms (§9(5)-style deemed-supplier liability for notified categories) don't depend on payment custody either. ADR-0021 jumps from "not §52" to "isn't an ECO" without engaging the broader question — an unstated leap.

**REG-3 — Invoice-numbering fix (ADR-0020) is sound on its narrow question but undersells a live compliance cliff.** *(High confidence.)*
Rule 46(b) and §31(1)(a) are accurately stated and correctly solved by dispatch-time numbering. But e-invoicing (IRN via GSTN) has been mandatory since 2020 above a declining turnover threshold, and non-compliant invoices for a covered business aren't valid tax invoices for ITC purposes. Any merchant already over that threshold issues legally defective invoices from day one, not "eventually" — ADR-0020's "stays phase-two, not decided here" folds a live compliance cliff into a deferred nice-to-have.

**REG-4 — DPDP: two PII components aren't actually anonymized, and the docs don't say so.** *(Medium-high confidence.)*
Destination/Contact Point commitment (`HMAC(order_salt, bytes)`, salt destroyed with the row) is legitimate anonymization *provided* "destroyed" is genuinely irrecoverable — nothing states this guarantee explicitly against backups/replicas/WAL. `consumer_id = HMAC(deploy_pseudonym_key, payer_handle)` is pseudonymization, not anonymization, by DPDP's own definition — the merchant (holding the key) can always re-derive the link, and erasing one order's row does nothing to that consumer's trail across every other order. A DPDP erasure request scoped to "this person" isn't actually satisfiable by the mechanism as specified.

**REG-5 — Data-fiduciary allocation (SPEC §14) is plausible only because of an unstated invariant.** *(Medium confidence.)*
"DPDP obligations sit with the Merchant" is reasonable given the self-hosted, keys-never-leave-the-box model — but this rests entirely on "OpenStore Inc. collects zero telemetry/logs/support data containing Consumer PII from any deployment, ever," which appears nowhere as a binding rule. It's currently an emergent property of not having built a phone-home feature, not a guardrail anyone would catch breaking.

**REG-6 — UAP/NPCI-chairman alignment is directionally fine but risks being read as more sanctioned than it is.** *(Medium confidence, process risk not text defect.)*
The quote and timeline are represented accurately, and ADR-0016 already caveats "positioning, not a design change... needs a legal opinion before it is made to a counterparty." That caveat is exactly the kind of line that gets dropped from a pitch deck under normal editing pressure — worth preserving verbatim wherever this argument travels.

**REG-7 — Consumer Protection (E-Commerce) Rules 2020 obligations are entirely unaddressed.** *(High confidence.)*
Grievance-officer contact display, defined return/refund/exchange timelines, country-of-origin/importer disclosures — none appear in SPEC or any PLAN file. Unlike GST/DPDP, which clearly got real research, this area appears not to have been considered at all.

**REG-8 — ADR-0021 and SPEC §2 aren't cross-linked.** *(Low severity, documentation hygiene.)*
ADR-0021's entire argument depends on SPEC §2's "Payment Provider = the Merchant's own account" staying true forever; ADR-0021 says to revisit if that changes, but SPEC §2 carries no pointer back, so a future editor changing §2 wouldn't be flagged to reopen the ADR.

**Sources:** [CGST Act §52](https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/acts/2017_CGST_act/active/chapter10/section52_v1.00.html) · [TCS consideration-collection test](https://www.taxmanagementindia.com/visitor/detail_article.asp?ArticleID=9784) · [§24 compulsory registration](https://www.taxbuddy.com/blog/compulsory-registration-gst-section-24) · [DPDP anonymization scope](https://chambers.com/articles/digital-personal-data-under-the-dpdp-act-what-data-does-it-cover) · [NPCI Reserve Pay circular OC No. 228, FY 2025-26](https://www.npci.org.in/uploads/UPI_OC_No_228_FY_2025_26_Enhancement_in_UPI_Single_Block_Multiple_Debits_UPI_Reserve_Pay_a9095c181d.pdf) · [UPI Circle delegation limits](https://www.ujjivansfb.bank.in/banking-blogs/banking-services/upi-circle-delegated-payments-how-it-works-limits) · [NPCI UAP / GFF 2026](https://www.business-standard.com/finance/news/india-may-allow-agentic-ai-led-upi-transactions-under-new-npci-protocol-126070801343_1.html)

---

## Cross-stakeholder consensus items

Findings independently raised by two or more reviewers, in their own words:

- **"Any Buyer Agent, no pre-registration" is not true today** — MER-4 and SHF-2 arrive at this independently, from different evidence (merchant: no in-product disclosure; Shopify: verified against how ChatGPT/Perplexity Shopping actually source catalogs).
- **The Agent Profile schema gap is worse than `docs/REVIEW-findings.md` R2 states** — MER-4 and SHF-1 both treat it as a structural non-interop, not a style mismatch.
- **Reserve Pay's real-world mechanics are under-specified/wrong** — PSP-2 (production failure modes unmodeled) and REG-1 (the cap figure itself is wrong) compound into the same underlying weak spot: ADR-0016/0018's COD-via-Reserve-Pay story is the least production-tested claim in the document set.
- **The ECO/TCS legal read (ADR-0021) is narrower than its own framing claims** — REG-2 states this directly; MER-2 flags the merchant-facing consequence of that read being wrong.
- **E-invoicing/IRN deferral is a live gap, not a phase-two nicety** — REG-3 (legal basis) and WOO-6 (competitive consequence — Zoho already ships it) are the same finding from two angles.
