"""`openstore-conform` — does this store actually implement the trait?

The conformance suite existed already, and ran against exactly two
implementations: the fake, and the storefront written for this repository. That
is not a contract, it is a description of two codebases that happen to agree.

This is the same claim made portable. It points at any URL, picks its own
subjects out of whatever catalogue it finds, and reports what holds and what
does not — so an implementer can answer "does mine work?" without reading a
line of the sidecar, and a Merchant can answer "is this integration real?"
without trusting either of us.
"""

from openstore.sidecar.conform.checks import Check, Outcome, Report, run

__all__ = ["Check", "Outcome", "Report", "run"]
