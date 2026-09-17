# Merchant is truth for everything

The sidecar never owns catalogue, stock, or orders. It reads catalogue items, options, and available counts fresh from the Merchant system at every gate, and mirrors moves (reserve/commit/release/restock) back idempotently. The `.yaml` counts only when the basic site is itself the Merchant system.
