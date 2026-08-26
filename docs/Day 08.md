# Day 8 — Genericity, Polish, Freeze

**Date: Wednesday, September 2, 2026.**

**Feature freeze happens at the end of today.** After today, you write no new features — only fixes to things that are broken, and rehearsal. This isn't a soft guideline; it's the mechanism that makes September 4–5 into real slack instead of two more days of scope creep eating your buffer. Read that sentence again before you start today's work, because the temptation to "just add one more thing" is highest exactly when you're this close to done.

## What you'll have by tonight

Genericity proven with a second merchant config (if not already done on Day 7), the four Discord channels visually coherent as a real observability dashboard, the `/admin/audit` page and storefront given a basic pass of polish, the `cloudflared` tunnel URL problem (Risk R4) solved with a startup script, and a fully rehearsed run-through of the Day 9 demo script — timed.

---

## Concepts

### 1. Why polish is explicitly bounded, not open-ended

The plan's cut-line language for polish is different in kind from the other cut-lines — it doesn't say "cut polish if you're behind," it treats polish as inherently timeboxed from the start, because unlike the security architecture (which has a correct/incorrect state), visual polish has no natural stopping point — you can always make a Discord embed's color scheme slightly nicer, always tighten a sentence in the storefront copy. The discipline today is: polish gets a fixed, small budget, and once you notice yourself iterating on font choices or color hex values for the third time, that's the signal to stop and move to rehearsal instead. Rehearsal has a much higher return on the remaining hours than an incremental visual improvement nobody will consciously register during a live demo.

### 2. Why the tunnel URL problem needs solving as automation, not memorization

Risk R4 (`cloudflared` tunnel URL churn) is a small technical problem with an outsized capacity to embarrass you on Day 9 specifically because it's exactly the kind of thing that's easy to forget under the mild stress of a live demo: you restart the tunnel (because you restarted your machine, or the previous tunnel session died), get a new random URL, and then live-demo a broken webhook because Razorpay is still trying to reach the _old_ URL. The fix isn't "remember to re-register it" — humans under light demo pressure are bad at remembering exactly this kind of fiddly manual step. The fix is a script that does it for you, every time, as part of your normal startup sequence, so there's structurally nothing to forget.

---

## Build

### Step 1 — Finish genericity, if not done on Day 7

Refer back to Day 7's "second merchant config" section if you haven't completed this yet. Non-negotiable minimum bar: a second `config/<second-merchant>.yaml`, and a full storefront-through-checkout run against it with `MERCHANT_CONFIG_PATH` as the _only_ thing that changed. If your `checkout_initiate`/`checkout_confirm` logic has anything gelateria-specific hardcoded anywhere (a stray reference to "gelato," a hardcoded merchant name instead of reading it from the loaded config), find it now and parameterize it — this is exactly the kind of thing genericity testing is supposed to surface.

### Step 2 — Tunnel automation script

**Signature:**

```python
def start_tunnel_and_register_webhook(local_port: int, razorpay_client) -> str:
    """Launches `cloudflared tunnel --url http://localhost:{local_port}` as a
    subprocess, parses its stdout for the assigned https://*.trycloudflare.com
    URL (cloudflared prints this to stderr/stdout on startup — capture and
    regex-match it), then calls the Razorpay API to update the registered
    webhook URL for this test account to {tunnel_url}/webhooks/razorpay,
    replacing whatever was registered before. Returns the tunnel_url."""
```

Wrap this into a single `scripts/dev_up.sh` (or a small Python launcher) that starts, in order: the tunnel (and re-registers the webhook), then the merchant server, then the notifier bot, then the merchant reasoning agent, then the buyer agent — so a single command brings up the whole demo environment identically every time, rather than five manually-remembered terminal tabs started in a specific, easy-to-forget order.

### Step 3 — Discord dashboard color/format pass

Apply the `LEVEL_COLORS` scheme from Day 1's `trace.py` consistently everywhere `emit(...)` is called across all four processes — audit this by grepping for every `emit(` call site in your codebase and confirming each one passes a `level` that actually matches its semantic meaning (a rejection that's currently emitting with `level="info"` because you were in a hurry on Day 3 is worth fixing now — the whole value of the color coding is that a judge glancing at `#merchant-server` mid-demo can distinguish "this is normal" from "this is a rejection" at a glance, without reading every word).

### Step 4 — Storefront and audit page polish (timeboxed — see Concepts)

Give the storefront HTML (Day 1) and `/admin/audit` (Day 3) one pass of basic, unfussy visual improvement — a small amount of CSS (inline `<style>` block is fine, no need for a build step), consistent spacing, readable typography. Budget: no more than an hour or two total across both pages. If you find yourself still tweaking after that, stop — this is the exact trap Concepts section 1 warned about.

### Step 5 — Full rehearsal, timed

Run the entire Day 9 demo script (you'll write the script itself as part of tomorrow's file, but rehearse the _system_, end to end, today) using your `dev_up.sh` from Step 2: fresh start, storefront, discovery, OAuth consent, a full Discord-driven purchase, the cross-sell moment from Day 6, and 2–3 of Day 7's failure-mode demos. Time the whole thing. If it runs long, this is the day to trim _narration_, not to cut a technical component — the technical content is what's being evaluated; a slightly rushed but complete walkthrough beats a smooth but incomplete one.

---

## Exit check (from the plan)

> ✅ Exit: genericity proven, dashboard looks intentional, full rehearsal completed, feature freeze.

Say the freeze out loud, to yourself or to whoever else is around, once you've verified all four. From this point forward, any change you make should be answerable with "this fixes a real bug found during rehearsal," never "this adds something new."

## What could go wrong

- **You discover during rehearsal that a Day 4–7 component is flakier than you thought**: this is precisely what rehearsal is _for_ — better to find it today than live on Day 9. Fix the specific flakiness (don't rewrite the component; find the actual bug), re-rehearse just that segment, move on.
- **The tunnel-registration script works once and then silently stops updating the webhook on subsequent runs**: check whether Razorpay's API call you're using is actually an _update_ (replacing the existing webhook config) versus accidentally _creating a new, additional_ webhook registration every restart, leaving multiple stale ones pointed at dead URLs alongside the current one — Razorpay may then deliver (or attempt to deliver) to all of them, which can look like "sometimes it works" flakiness that's actually a registration bug.
- **You notice a real, previously-undiscovered security gap while polishing**: this is the one legitimate exception to "no new features after today" — a bug in the core money/mandate/auth path is always in scope to fix, at any point up to the literal start of the Day 9 demo. The freeze is about _scope_, not about ignoring a genuine correctness problem you're lucky enough to catch in time.

---

Tomorrow: showtime.