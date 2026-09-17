# The Provider declares its methods; v1 hard-codes none

"UPI-only" was written as a core rule, which made every card or wallet attempt look like an architectural limit rather than a configuration. It is a configuration: the provider trait already abstracts `make-link / check-status / cancel / refund`, and which instruments a link may offer is the Provider's fact, surfaced by the Merchant.

Each provider adapter declares its supported method set at boot and the Merchant enables a subset in `/agentic`. The Gate refuses any method outside the enabled set with `method-not-supported` naming the enabled ones. Razorpay ships v1 declaring UPI, with cards and netbanking enableable by the Merchant without touching the money core; the fake provider declares everything for tests.

Consequences: the S6 translators stop special-casing "UPI-only" and simply map a foreign instrument request to the enabled set, so ACP/UCP card attempts produce a correct, actionable refusal on a store that has not enabled cards and succeed on one that has. COD stays out of v1 (SPEC §13) for a different and real reason: it has no prepayment, so it needs a capture-on-delivery ledger path the escrow-zero invariant does not yet model. Delegated agent-held payment credentials (Shop Pay tokens, ACP shared payment tokens) remain refused on purpose — see ADR-0008.
