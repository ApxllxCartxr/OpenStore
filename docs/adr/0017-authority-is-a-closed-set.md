# Authority is a closed set, and in India its default member is the UPI PIN

SPEC §7 made one ceremony the definition of authority: a per-domain passkey tap bound to `cart_hash` + amount + expiry, with §12 refusing `confirm` without one "regardless". Three things now need to authorize a spend and none of them is that tap — the UPI PIN, a Cash-on-Delivery confirmation where no money moves at all, and the bank-held mandate ADR-0016 expects UAP to bring. Admitting each as a special case would give us three spend paths, which ADR-0002 exists to forbid.

So authority stops being a ceremony and becomes a closed set, recorded in the Transcript and tagged in the receipt's `tapped` section:

- **`upi-pin`** — the default in India. The payer authenticates in their own PSP app, against a named payee and an exact amount, with the bank as authenticator.
- **`passkey`** — the original tap (ADR-0008), now opt-in rather than the price of entry: for baskets where the extra binding is worth a per-domain enrollment.
- **`confirmed-intent`** — no money moves. The COD case, where the Merchant ships on the strength of a verified commitment rather than a payment.
- **`mandate`** — defined, registered in `docs/CODES.md`, recorded, and refused with a named code in v1. The seam a UAP adapter lands on (ADR-0016) instead of an ADR reopening.

The Gate's first check is renamed accordingly, from `human-authority-present` to `authority-present-and-accepted`, and which kinds a Merchant accepts is a `/agentic` setting like the enabled payment methods beside it (ADR-0013).

## What each kind actually binds, stated plainly

A passkey tap binds the exact `cart_hash`, the amount, and the expiry, and the Consumer's authenticator signs over all three. It is the strongest thing in this document and it costs an enrollment per Merchant domain.

A UPI PIN does not bind a cart and never will. What the payer sees in their bank app is a payee and a number; what they authenticate against is that number. So the binding is split and must be written down as split: the **amount** is bound directly by the payer's own bank, and the **basket** is bound by reference — the approve page renders the Merchant-signed Quote on the Merchant's own domain, pins `cart_hash → order → payment reference` before the collect or intent goes out, and the PSP's signed record closes the chain from the other end. The number the Consumer authorises is the number the page showed them, and the evidence must carry the fact that this page rendered this Quote to this session. Claiming the payer approved a hash would be a lie a dispute would expose.

`confirmed-intent` carries no payment, so it binds whatever the Merchant chose from a closed set of two — `upi-verify` (a ₹1 verification; a Reserve Pay block would prove more and is an open acquirer question per ADR-0018, not a v1 mechanism) or `passkey` — and the Transcript records which, because "confirmed" with no named mechanism is not evidence. An OTP to the Contact Point is deliberately not a member: it proves control of a phone number, which is precisely what RTO fraud already defeats, and it binds no funding instrument. SPEC §7's ban on OTPs to money therefore holds here without needing the lawyer's argument that COD has not moved any yet.

Because those three bind differently and no reader can be expected to infer which one they are holding, every Authority carries a **Binding** beside it, and it is normative: what was bound (`cart` / `amount` / `none`), by whom (`payer-device` / `payer-bank` / `merchant`), and how. The verifier prints it, and a bundle never reads "verified" unqualified. Ranking the kinds would have been the easier move and the wrong one — a standard admitting only the strongest claim describes one rail, while one that makes every implementer declare the strength of its own claim describes all of them, which is what deterministic and auditable has to mean across heterogeneous rails.

## Why the PIN is the default and not the fallback

Penetration, and whose word the receipt rests on. A passkey enrollment for a ₹399 basket is friction ADR-0008 called accepted pain, on a device where the Consumer already holds a phishing-resistant, per-transaction, payer-app-native authorization with a bank behind it. NPCI's stated architecture wants authentication and settlement to follow deterministic auditable rules with the authorizer verifying identity (ADR-0016); a bank-attested authorization is strictly better evidence in that frame than one we issue to ourselves.

The ceremony does not disappear — its terminal step changes. `/agentic/approve` still renders the signed Quote, still takes the private Discount Code (ADR-0015), still rebinds to the post-application `cart_hash`, still issues a single-use token. It ends in a UPI intent or collect instead of a biometric prompt.

## Authority does not always arrive at the same moment

Under `passkey` the authorization exists before the Gate runs. Under `upi-pin` it arrives *with* the money, because the PIN is the money moving. So the authority check stops being a check at a fixed point and becomes a property that must hold by settlement, with the kind declaring when it lands. The Gate's two responsibilities separate accordingly: `decide()` runs every check including `quote-fresh`, produces the Transcript, and permits the collect; `settle()` verifies the Provider record, records the Authority and its Binding, and captures. SPEC §4's rule that `quote-fresh` never runs after payment stays literally true, because `decide()` is still the last thing before the money path opens.

One consequence looks like a regression and is not. Stock is now held before any authorization exists under `upi-pin`, which SPEC §6 avoided on purpose — a `pending` order holds nothing precisely so a stranger agent cannot deny inventory at will. The protection survives in a different place: a hold is now downstream of the approve token, which is single-use, five minutes, and already separately throttled (SPEC §12), so holds are rate-limited by the scarce resource in front of them rather than by not existing at all. The hold window is `min(collect validity, token lifetime)` and it closes on explicit decline, which UPI reports quickly and unambiguously — a shorter and better-behaved window than a card authorization.

What does *not* change is the three statuses. `pending` → `confirmed` → `paid` survives intact and every adapter mapping holds; `confirmed` simply means the Consumer initiated payment rather than that the Consumer tapped.

## What costs a fresh Authority

The `cart_hash` does not relax. It remains the Merchant-side binding over the whole order, pinned by the Attestation, the Transcript and the receipt and compared by `quote-fresh`, and any change to what it covers still produces a fresh one. What is now kind-dependent is when a *new authorization* is owed, and it falls out of the Binding rather than arriving as a new rule: `passkey` bound the cart, so any preimage change owes a fresh ceremony; `upi-pin` bound the amount, so only a change to `total_minor` or `currency` does.

That rule alone leaves one hole and it is an attack, not an inconvenience: a Destination change that does not move the total sends the parcel elsewhere for the same money, and an amount binding would let it through. It closes on display integrity rather than on hashes — anything the Consumer was shown on the approve page is covered, and any change after render invalidates the token. The approve page is the display of record, which is also the thing that makes the `upi-pin` basket claim legible at all.

## What this makes expressible, and what it does not

Standard UPI has no authorize-then-capture, which is the real reason COD sat outside v1 (SPEC §13) — escrow-zero could not model a hold that becomes cash days later at a doorstep. Reserve Pay looked like that primitive and is not one: it is a standing prepaid reserve debited at the customer's own purchase action, not a hold a merchant captures days later, and ADR-0018 sets out the circular text that retires the idea. What actually made COD expressible was separating the stock hold from the money hold, which needed no rail at all. So COD carries no money hold of any kind: `confirmed-intent` carries the order and the Ledger moves no money until delivery.

This ADR only makes that path expressible. The path itself — the deadlines, the sweep, how RTO reconciles — is a money-core change and needs its own ADR before any of it is built.

Consequences: ADR-0008 is amended, not reversed — a spend still requires a fresh, per-order, human authorization bound to a displayed total, and an agent still holds none of it; what changes is that the passkey is one member of the set rather than its definition, and per-domain enrollment becomes a Merchant's choice. SPEC §4, §7 and §12 move with Sidecar S3 and S4 in one pass: the Gate check renames, the Transcript gains the authority kind, and the receipt's `tapped` section is tagged so the offline verifier reports which kind authorized and what it bound. ADR-0013 grows a second declaration — a provider adapter now states which authority kinds it can produce, alongside which instruments it supports. `docs/REVIEW-findings.md` F3 closes here. The UAP claim in ADR-0016 becomes true as written, because `mandate` now exists as a refused member instead of as a sentence.

Two neighbouring ADRs move with this one. ADR-0004 splits what "revocable" means, because a per-order bank authorization has no standing to revoke and saying so is stronger than pretending otherwise. ADR-0011 takes the payer handle as a third PII class and defines `consumer_id` as a per-Merchant-domain keyed pseudonym for every authority kind, so dual identity survives `upi-pin` without a cross-merchant tracker landing in the evidence. SPEC §14's dispute wording also stops describing card chargebacks, since the default rail does not have them.
