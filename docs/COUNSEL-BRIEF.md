# Counsel brief — one engagement, four questions

Written 2026-09-19, from `docs/REVIEW-remediation.md` items #2, #5 and #8. These arrived as three separate findings from three reviewers and were priced as three engagements. They are one fact pattern asked by three regulators, and the first answer drives the third, so they go out together.

Give counsel this file plus `SPEC.md` §2, §4, §8, §14, and ADRs 0007, 0013, 0018, 0021, 0023.

## The fact pattern (identical for every question below)

OpenStore is self-hosted software. One deployment serves exactly one merchant (ADR-0007). It runs on the merchant's own domain and infrastructure; OpenStore Inc. operates no instance, holds no merchant's keys, and receives no data from any deployment.

The software sits between a buyer's AI agent and the merchant's own store. It reads the merchant's catalogue and stock, prices the basket by asking the merchant's own system (it computes no tax and no price of its own — ADR-0010), takes the buyer's authorisation, and calls the merchant's own payment provider account (Razorpay / Justpay / Airpay) to create a payment link or a UPI block. Money moves from the buyer to the merchant's provider account. It never enters an account OpenStore or the software controls, and there is nothing to pool (ADR-0021, ADR-0023).

The merchant is the seller of record in every transaction. The software neither ranks, features, curates, nor selects merchants or products; a future Listing Index is constrained by ADR-0022 to filter by category and region only, with no relevance score and no paid placement.

## Q1 — RBI: payment gateway or payment aggregator? *(Confirm, don't research.)*

Our read (ADR-0023): the PA/PG line turns on handling funds; the software handles none, so it is a payment gateway / technology provider and needs no RBI authorisation, while the baseline gateway security expectations do apply to the codebase.

Confirm or contradict, and specifically: (a) does *orchestrating* the payment request — calling the provider's API and receiving its webhooks on the merchant's behalf — change the analysis where no funds are handled; (b) which baseline gateway obligations attach to the software vendor versus the merchant operating the instance, given nobody operates a shared instance; (c) does the answer change if OpenStore Inc. ever offers a hosted deployment where it operates the box but funds still settle to the merchant's own account.

## Q2 — GST: is the software, or its publisher, an Electronic Commerce Operator?

ADR-0021 concludes there is no §52 TCS liability because the operator never collects consideration. We believe that narrow conclusion is sound and the ADR overreaches when it moves from "not §52" to "not an ECO."

Answer the broader question the ADR skipped: (a) does §2(45)'s "owns, operates or manages ... a platform for electronic commerce" capture a self-hosted single-merchant deployment, and does it capture the *publisher* of that software separately from the merchant running it; (b) does §9(5) deemed-supplier liability for notified categories bite where payment custody never arises; (c) at what point would a Listing Index that only filters (ADR-0022) start to look like operating a platform; and (d) the sharper version of the same question — ADR-0025 contemplates a **Registrar**: an entity that enrols on agentic payment rails as the accountable participant, attests which AI agents are known, and can revoke a misbehaving one, while still receiving no funds, no merchant keys, no consumer personal data, and holding no power to rank or promote merchants. Does the ability to attest and revoke agents make that entity an ECO under §2(45), or attract §9(5) liability, when it never touches consideration or the goods? This is the strongest ECO fact pattern in the document set and the one most likely to be reached commercially.

## Q3 — Consumer Protection (E-Commerce) Rules 2020: who owes the duties?

Currently unaddressed anywhere in the spec (REG-7). The answer depends on Q2: the Rules attach to "e-commerce entities," and the merchant plainly is one.

For each of grievance-officer designation and contact display, return/refund/exchange timeline disclosure, and country-of-origin / importer disclosure: (a) does the obligation sit with the merchant alone, or does the software publisher or a self-hosted deployment attract any of it; (b) is a stated return/refund *turnaround time* mandatory such that it must be displayed at the point of sale and on the receipt — this one is a product design decision for us, not just a form field, because SPEC §6 currently models that refunds happen but not when.

## Q4 — NPCI Reserve Pay: acquirer questions, not counsel

Not legal questions, but they travel with the same engagement because they go to the same payment counterparty. The capture-at-delivery question that originally sat here is **closed**: NPCI/UPI/OC-228/2025-26 (8 October 2025) makes clear that Reserve Pay is a standing reserve debited at the customer's own purchase action, not an authorize-then-capture hold, so ADR-0018 cut that path rather than asking about it. Three questions remain, in priority order:

1. **Repeat purchase.** Can a merchant offer the standing-reserve shape as intended — one block, multiple instant debits at the customer's purchase action over the block's life — through your self-serve APIs today, or does it need a bespoke arrangement? This is the shape ADR-0024 wants.
2. **Eligibility.** The circular restricts Reserve Pay "to begin with" to "online verified merchants with low ticket and high frequency transactions." What does your onboarding actually apply that to, and would a single-merchant D2C store qualify?
3. **Block as evidence.** Is it acceptable to create a block purely to prove a funding instrument is real and funded, and then release it without ever debiting — a stronger substitute for a ₹1 verification (ADR-0017's `upi-verify`)? The circular does not contemplate this use, so we will not build it on our own reading.

## What we are not asking

Tax advice for any individual merchant — every merchant gets their own counsel, and the product says so. E-invoicing/IRN obligations sit with the merchant and are a product gap we have stated rather than a legal question (`docs/REVIEW-remediation.md` item #6). DPDP fiduciary allocation is settled in SPEC §14 on the basis that OpenStore Inc. receives no data at all; flag it only if Q1's hosted-mode variant would change that.
