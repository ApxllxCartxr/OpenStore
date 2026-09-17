# Passkeys enroll per Merchant domain; money always needs a fresh tap

Passkeys only work on one origin, so a Consumer roaming five stores enrolls five times — accepted pain for phishing-resistant taps bound to exact `cart_hash` + amount + expiry. Standing `Always allow` covers reads/drafts only; every cart-with-spend, checkout, or place-order needs a fresh Allow-once modal plus a fresh tap, and `confirm` scope without a tap is rejected by the Gate.

Consequences: buying a latte is Allow-once + biometric every bag; sign-count is tracked but warns instead of hard-locking on platform counter reset.
