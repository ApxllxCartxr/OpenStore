# Cash on Delivery: the Ledger holds stock and money separately, which is the whole of it

COD sat outside v1 because "it has no prepayment, so it needs a capture-on-delivery Ledger path the escrow-zero invariant does not model yet" (SPEC §13). That was true and it was expensive: COD is roughly 60% of Indian ecommerce and drives 76–83% of all RTO volume, so a commerce product for India that cannot express it has excluded the majority of the market and all of its hardest problem.

Worse, the exclusion was hiding a conflation rather than a limit. SPEC §6 says `confirmed` means "stock held by `RESERVE`", which makes the Ledger entry do two jobs at once: hold inventory and hold money. For a prepaid order those coincide and nobody notices. For COD they come apart completely — the goods are committed on day one and the money appears at a doorstep on day four, or never appears at all.

## The disentangling

Holding stock is door 3 `reserve` on the Merchant side, recorded in `stock_moves`. Holding money is the Ledger's `RESERVE`, which carries `amount_minor`. They fire together on a prepaid order and they are still two different facts. From here the Ledger's entries are about money only, and `confirmed` means the stock is held by the trait, with whatever money hold the rail supports recorded separately or not at all.

Escrow-zero is unchanged and needs no exception: it quantifies over `RESERVE` entries, so an order that never writes one cannot violate it.

## One COD path

**`cash-on-delivery` — no money is held at all.** Nothing can be reserved, because no UPI primitive available to us holds money across a delivery (see the next section). The Ledger writes no entry at order time; stock is held by door 3 alone. On delivery the Merchant records collection and the Ledger writes a single `CAPTURE` with no preceding `RESERVE` — permitted explicitly, because money that never passed through a hold cannot close one. On RTO the Ledger writes nothing, because nothing moved, and the goods return through `restock`.

Authority on this path is `confirmed-intent` (ADR-0017), whose mechanisms are `upi-verify` or `passkey` and never an OTP. That choice is the entire product here: a merchant shipping unpaid goods is buying evidence that this basket was committed to by someone who controls a real funding instrument, and a phone number proves neither.

## Why there is no second path: Reserve Pay is not authorize-then-capture

An earlier revision of this ADR carried a `upi-block` path — `RESERVE` at order against a Reserve Pay block, `CAPTURE` at delivery, `RELEASE` on RTO — and called Reserve Pay "the missing primitive" that let COD in. That was written from a remembered description of the feature. Reading NPCI/UPI/OC-228/2025-26 (8 October 2025) directly retires it: Reserve Pay does not do this, and the path was cut rather than repaired.

Four things in the circular say so, and the fourth is decisive:

1. The feature is defined as a standing reserve funding a **series** of debits: funds blocked "for multiple debits which can be initiated by the customer on the merchant's platform, till the reserved funds gets exhausted or the block has been revoked or expired."
2. Payment counts only on "the successful debit response received by the merchant (for the debit initiated by the **customer action on merchant's platform**)". Accepting a parcel at a doorstep is not a customer action on the merchant's platform, so there is no merchant-initiated capture in the model at all.
3. For fixed-amount goods the money must move first: "the delivery of goods and service should only be after the confirmation of successful debit," with post-delivery debit permitted only "for use cases wherein amount is not fixed and is determined based on the services consumed (e.g.: cab aggregators, EVs, etc.)." A D2C basket is a fixed amount.
4. **One block per customer per merchant at a time** ("One mobile number (assumed as one customer) is allowed to create only one block at a time for the particular merchant"). If a block were a per-order authorization, a Consumer could not place a second order while the first was in transit. That constraint is only coherent if the block is a standing per-consumer reserve, which is what points 1–3 already describe.

The block is also explicitly not a guarantee: "The block created shall not be treated as the guarantee of payment." So even under a friendlier reading it never carried the assurance the cut path claimed.

**What survives is the part that was never about Reserve Pay.** The disentangling above is the whole enabler: once the Ledger's entries are about money only and stock is held by door 3, an order that holds no money is expressible, and COD is that order. The earlier revision credited the rail for work the invariant was doing. COD stays in, unchanged, on the cash path.

## Open by design: a money guarantee at order time

Cutting `upi-block` removes a *capability we wanted*, not a capability we had — it never existed. The want is real and stays on the table: the Merchant ships against something better than a promise, and the Consumer is not asked to prepay.

No UPI primitive available today supplies it. Reserve Pay is a prepaid tab, not an escrow; a card-style authorization has no UPI equivalent; and anything where OpenStore holds the funds is refused outright, because pooling money is the single fact that would convert this from a payment gateway into a regulated aggregator (ADR-0023) and into a GST Electronic Commerce Operator (ADR-0021). That constraint is not negotiable and it rules out the obvious clever answer.

What is left to design against, and is worth designing against rather than waiting for:
- **Evidence in place of custody.** `confirmed-intent` (ADR-0017) already buys a bank-attested funding instrument rather than a phone number. Strengthening what that attestation proves is cheaper than holding money and is the direction this product is already good at.
- **A block used as evidence, then released.** A Reserve Pay block does prove real funds exist, even if it can never be debited at a doorstep. Blocking and releasing without ever debiting is a use the circular does not contemplate, so it is an acquirer question before it is a design (`docs/COUNSEL-BRIEF.md` Q4), but it is the nearest sanctioned thing to the property we wanted.
- **Partial prepayment**, where a small prepaid amount covers the RTO cost and the balance is cash at the door. Needs no new rail at all, and turns RTO from a loss into a smaller loss.

None of these is v1. They are recorded so the gap stays a known open problem with named candidates, rather than quietly becoming a thing nobody remembers wanting.

Two further constraints from the same circular, kept because any future Reserve Pay work inherits them: consumer-initiated revocation is mandated, not merely possible ("easy access to revoke the block", with unutilised limits "always checked before initiating a debit"), so a block can vanish mid-flight; and debit timeouts are declines, reversed real-time, retryable at most 3 times in 24 hours, with no retries permitted for any other decline.

## No ninth status

`confirmed` (intent verified, stock held, no money held) → `paid` (cash collected at delivery) → `completed`. An RTO is `cancelled` with an `rto` reason and a restock, which is already what `cancelled` means — pre-money, stock returned — and is honest: no money moved and the order did not complete. Full versus partial stays derived (SPEC §6), and the canonical set stays 8, so every adapter mapping holds.

The one column that changes meaning is `expires_at`, which already means "the next deadline the sidecar will act on" and now takes a third deadline: the delivery window.

## What we are trusting, said out loud

On the cash path the Merchant asserts that cash was collected, and nothing in the system can check it. That is correct and not a weakness — it is the Merchant's own money and their own Ledger. What the evidence proves is the half that is actually disputed: that this Consumer committed to this basket at this total, with a named Binding, before the goods moved.

Consequences: SPEC §13 loses COD from the exclusion list and §4's Ledger gains the capture-without-reserve case with its reason. SPEC §6 gains the COD lifecycle and the `rto` cancel reason. ADR-0017's `confirmed-intent` member stops being speculative and acquires its only real use. Restocking fees, partial COD collection, and NDR/reattempt flows stay out — they are policy on top of this path, not changes to it.

The Provider trait keeps its declared, optional block capability (`block / capture-block / release-block`) beside `make-link / check-status / cancel / refund`, announced at boot like any other method (ADR-0013), even though nothing declares it in v1 and no path consumes it. It costs nothing to leave the seam and ADR-0024 names what would land in it. Deleting a DONE WHEN needs an ADR note (`docs/EXPANSION.md` §1): `PLAN-sidecar.md` S3's "a `upi-block` order captures at delivery and releases at RTO" is removed by this revision, because it asserts a behaviour the rail does not offer.
