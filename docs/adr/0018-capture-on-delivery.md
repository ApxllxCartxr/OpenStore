# Cash on Delivery: the Ledger holds stock and money separately, and Reserve Pay is the missing primitive

COD sat outside v1 because "it has no prepayment, so it needs a capture-on-delivery Ledger path the escrow-zero invariant does not model yet" (SPEC §13). That was true and it was expensive: COD is roughly 60% of Indian ecommerce and drives 76–83% of all RTO volume, so a commerce product for India that cannot express it has excluded the majority of the market and all of its hardest problem.

Worse, the exclusion was hiding a conflation rather than a limit. SPEC §6 says `confirmed` means "stock held by `RESERVE`", which makes the Ledger entry do two jobs at once: hold inventory and hold money. For a prepaid order those coincide and nobody notices. For COD they come apart completely — the goods are committed on day one and the money appears at a doorstep on day four, or never appears at all.

## The disentangling

Holding stock is door 3 `reserve` on the Merchant side, recorded in `stock_moves`. Holding money is the Ledger's `RESERVE`, which carries `amount_minor`. They fire together on a prepaid order and they are still two different facts. From here the Ledger's entries are about money only, and `confirmed` means the stock is held by the trait, with whatever money hold the rail supports recorded separately or not at all.

Escrow-zero is unchanged and needs no exception: it quantifies over `RESERVE` entries, so an order that never writes one cannot violate it.

## Two COD paths, because the rail decides which is available

**`upi-block` — Reserve Pay.** Funds are blocked on the payer's account at order time within NPCI's caps, which is precisely an authorize-then-capture primitive on UPI and the thing whose absence kept COD out. `RESERVE` at order for the blocked amount, `CAPTURE` on delivery, `RELEASE` on RTO or block expiry. Escrow-zero holds exactly as it does for a card authorization, and the published block window comfortably exceeds any domestic delivery. Authority is `upi-pin` (ADR-0017): the payer entered a PIN in their own app to place the block, so the amount is bank-attested at order time and the merchant ships against a real, third-party-attested commitment rather than a form submission. What that commitment is *not* is a guarantee of payment, and the circular says so in terms: "The block created shall not be treated as the guarantee of payment, only the successful debit response received by the merchant ... shall be considered for payment" (NPCI/UPI/OC-228/2025-26, 8 October 2025). A block is attested intent plus earmarked funds, which is exactly what this path claims and no more.

The cap is a maximum of ₹10,000 per block, for up to 90 days, from the same circular — so the block window does comfortably exceed any domestic delivery, and the basket ceiling is low enough that `upi-block` is the minority COD path by value even where it is available.

**`cash-on-delivery` — no money is held at all.** Above the cap, or where the payer has no blocking-capable PSP, nothing can be reserved. The Ledger writes no entry at order time; stock is held by door 3 alone. On delivery the Merchant records collection and the Ledger writes a single `CAPTURE` with no preceding `RESERVE` — permitted explicitly, because money that never passed through a hold cannot close one. On RTO the Ledger writes nothing, because nothing moved, and the goods return through `restock`.

Authority on this path is `confirmed-intent` (ADR-0017), whose mechanisms are `upi-verify` or `passkey` and never an OTP. That choice is the entire product here: a merchant shipping unpaid goods is buying evidence that this basket was committed to by someone who controls a real funding instrument, and a phone number proves neither.

## Unresolved: the circular may forbid capture-on-delivery for goods

Added after reading NPCI/UPI/OC-228/2025-26 (8 October 2025) directly rather than from memory. This ADR's `upi-block` path captures at delivery. The circular's obligations on acquiring entities say the opposite for fixed-amount goods:

> The purchase action by the customer must result into instant debit request without any delay, and the delivery of goods and service should only be after the confirmation of successful debit to the Acquiring bank for categories such as quick commerce, food delivery, etc. For use cases wherein amount is not fixed and is determined based on the services consumed (e.g.: cab aggregators, EVs, etc.), merchant may debit post successful delivery of services.

A D2C basket is a fixed amount, which puts it in the first sentence, not the exception. Read strictly, Reserve Pay is a *block-then-debit-at-purchase-action* primitive with the block existing to fund a sequence of instant debits — not the authorize-then-capture-days-later primitive this ADR treats it as. That would remove the whole reason `upi-block` was introduced here: if the debit must precede dispatch, the path collapses into prepaid, and COD's actual problem (goods move before money) is untouched.

Three readings are open and this ADR does not pick one — it goes to a grill before S3 freezes the Gate's check order:
1. **Strict.** `upi-block` is not a COD instrument at all; COD is the `cash-on-delivery` path only, and Reserve Pay is repurposed as a prepaid convenience (block once, debit per shipment on a split order).
2. **Narrow.** "Purchase action" is the *delivery* acceptance rather than the order placement, making capture-at-doorstep the instant debit the circular asks for. Defensible in the words, untested against any acquirer.
3. **Segment.** The rule binds the acquirer's merchant selection ("online verified merchants with low ticket and high frequency transactions"), and a merchant admitted to Reserve Pay has already been scoped to categories where this works.

Resolving this needs an acquirer's own answer, not a close reading: it is the first question to put to the Provider counterparty in the PA/PG engagement (`docs/REVIEW-remediation.md` item #2), because it decides whether `upi-block` exists.

Three further constraints from the same circular that this ADR does not yet model, independent of which reading wins:
- **One block per customer per merchant at a time** ("One mobile number (assumed as one customer) is allowed to create only one block at a time for the particular merchant"). A second concurrent `upi-block` order from the same Consumer at the same Merchant cannot be placed, and needs its own refusal code rather than a Provider error.
- **Consumer-initiated revocation is mandated**, not just expiry: issuers and UPI apps must give "easy access to revoke the block," and unutilised limits "are always checked before initiating a debit." So `RELEASE` has a third trigger beside RTO and expiry, it can fire mid-transit, and a capture can fail against a live block that was revoked or partly spent elsewhere.
- **Debit timeouts are declines**, reversed real-time, retryable a maximum of 3 times in 24 hours, with no retries permitted for any other decline — a reason-code family and a retry policy, both closed-set work (SPEC §12).

## No ninth status

`confirmed` (intent verified, stock held, money blocked or not) → `paid` (block captured or cash collected at delivery) → `completed`. An RTO is `cancelled` with an `rto` reason and a restock, which is already what `cancelled` means — pre-money, stock returned — and is honest: no money moved and the order did not complete. Full versus partial stays derived (SPEC §6), and the canonical set stays 8, so every adapter mapping holds.

The one column that changes meaning is `expires_at`, which already means "the next deadline the sidecar will act on" and now takes a third deadline: the delivery window, or the block's own expiry where one exists.

## What we are trusting, said out loud

On the cash path the Merchant asserts that cash was collected, and nothing in the system can check it. That is correct and not a weakness — it is the Merchant's own money and their own Ledger. What the evidence proves is the half that is actually disputed: that this Consumer committed to this basket at this total, with a named Binding, before the goods moved.

Consequences: SPEC §13 loses COD from the exclusion list and §4's Ledger gains the capture-without-reserve case with its reason. SPEC §6 gains the COD lifecycle and the `rto` cancel reason. The Provider trait grows a declared, optional block capability (`block / capture-block / release-block`) beside `make-link / check-status / cancel / refund`, announced at boot like any other method (ADR-0013), so an adapter without it simply never offers `upi-block`. ADR-0017's `confirmed-intent` member stops being speculative and acquires its only real use. Restocking fees, partial COD collection, and NDR/reattempt flows stay out — they are policy on top of this path, not changes to it.
