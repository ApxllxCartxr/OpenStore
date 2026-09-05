# OpenStore — 6-Minute Walkthrough Script

Track 01: AI Growth & Agentic Commerce. Recording script for a ~6:00 walkthrough
covering both halves of the track — making a merchant transactable by an AI
buyer end-to-end, and growing the merchant's revenue autonomously — with the
OpenStore sidecar as the throughline.

**Before you start recording — have these open:**
- Terminal, repo root, font large enough to read on a recording
- Browser tab 1: `http://localhost:8000/intent/studio` (Gelateria Policy Studio)
- Browser tab 2: Discord, DM open with the buyer bot
- Browser tab 3: `http://localhost:8001/campaign/studio` (Chai House Campaign Studio)
- All four processes already running (`gelateria`, `chai`, buyer bot, merchant
  bot) — confirm with a quick `curl localhost:8000` / `curl localhost:8001`
  off-screen before you hit record, don't debug live. See `docs/RUN.md` for
  the full boot sequence.
- **The evidence beat uses pre-existing files, not a live checkout** — no ID
  to type or fill in on camera. `docs/demo_assets/bundle.json` (a completed
  order's evidence bundle) and `docs/demo_assets/bundle_tampered.json` (the
  same bundle with one digit flipped in `amount_minor`) are already checked
  into the repo, verified working. Confirm once before recording:
  ```bash
  uv run openstore-verify docs/demo_assets/bundle.json --merchant-jwks docs/demo_assets/jwks/
  # → exit 0, all 14 checks [PASS], VERDICT: AAL2
  uv run openstore-verify docs/demo_assets/bundle_tampered.json --merchant-jwks docs/demo_assets/jwks/
  # → exit 1, chain_integrity + amount_consistency [FAIL]
  ```

---

**[0:00–0:20] — Intro**
> Hello there, my name is Joseph Fernando from CIT Chennai. So, I'll cut to
> the chase. Here's what I've built, it's called OpenStore. Before I get into
> the solution, the track I chose was 01, i.e. the "AI Growth & Agentic
> Commerce" track. The second half of the track explicitly mentions "make a
> merchant transactable by an AI buyer, end to end", so that has been my main
> focus. What I've essentially built is a Sidecar application which does...

**SHOW:** nothing yet — camera/face or a title card. No screen share this beat.

---

**[0:20–0:50] — What a sidecar means, and the adapter point**
> ...exactly that — it bolts onto a merchant's *existing* store without
> replacing it. And I designed it deliberately around one seam: every piece
> of catalog and order data flows through a single access point. Right now
> that's reading a YAML file, because that's what I could build and prove
> correct in the time I had. But nothing above that line — the compiler, the
> checkout flow, the agent layer — knows or cares that it's YAML. Point that
> same function at a Postgres query or a Shopify Admin API call, and the
> merchant's real inventory shows up the same way, with zero changes anywhere
> else in the system.

**SHOW:** Terminal — `cat configs/gelateria.yaml` for two seconds (just enough
to flash the `catalog_path` line), then `cat configs/catalog.yaml` for two
seconds. Don't linger — this is a 5-second visual beat, not a code read-through.

---

**[0:50–1:15] — The gap this closes**
> Every agentic-commerce protocol racing right now — ACP, UCP, AP2, x402,
> Visa TAP — answers "how does an agent pay?" None of them answer the harder
> question: what does the merchant hand an arbitrator ninety days later, when
> the human disputes the charge? That gap is what OpenStore closes. I'm not
> just making a merchant transactable — I'm making every transaction
> *defensible*.

**SHOW:** stay on terminal or cut to face — no demo yet, this is the pitch.

---

**[1:15–1:20] — Transition**
> Let me show you, live. Two real merchants running on this one sidecar, both
> reachable by one real AI buyer over Discord.

**SHOW:** Terminal, run `ps aux | grep openstore` (or just switch to a
terminal pane where the 4 processes' logs are visibly tailing) — 3 seconds,
proves it's live, not slides.

---

**[1:20–1:45] — Merchant side: the one human moment**
> Before any agent can touch this store, a human signs spending rules with a
> passkey — this is Policy Studio. I'll set a cap: two thousand rupees a
> month, five hundred per order, vegan items only, Gelateria only. That's a
> real WebAuthn ceremony, hardware-backed, not a checkbox. This is the only
> moment a human is in the loop — everything downstream is enforced by a
> deterministic compiler, not by trusting the AI.

**SHOW:** Switch to browser tab 1 (`/intent/studio`). Fill the policy form
live (amount fields, tag checkbox for "vegan"), click **Register passkey** →
touch/approve the platform authenticator prompt when it pops up, then **Sign
policy** → approve again. Two real prompts — don't pre-record this, the
authenticator popup is the proof.

---

**[1:45–2:15] — Buyer side: the happy path**
> Now I'm the buyer, on Discord. I just talk to it.

**SHOW:** Switch to Discord tab. Type: `get me two vegan gelatos`. Let the
bot respond (search → cart summary). Type `checkout` or confirm when it asks.
Show the Razorpay payment link it posts, click it, complete payment in the
test-mode checkout page. Come back to Discord, show the "order HELD,
15-minute cancel window" confirmation message.

> Behind that message: thirteen compiler checks — currency, merchant, tags,
> spend caps — ran before Razorpay was even called. That's real money, gated
> entirely by the policy I just signed.

---

**[2:15–2:30] — One-line negotiation beat (trimmed)**
> Watch what happens if I push past the rules.

**SHOW:** Discord — type `also add the pistachio`. Show the bot's denial
message (tag violation — pistachio isn't vegan).

> Denied. The agent can propose anything — it can never force a payment
> through a rule it didn't earn.

*(Cut the amendment-ceremony walkthrough entirely — don't demo the second
passkey tap live, just say the line above and move on.)*

---

**[2:30–4:00] — Growth loop (this is the track's second half — give it the most time)**
> This is the part of the track most people are missing — growing the
> merchant's revenue, not just processing a sale. Chai House has a SKU,
> chai_green, that hasn't sold in seven days but was selling fine before.
> Nobody told my system to look at that — a background loop checks every
> merchant, on an interval, for exactly this kind of stall. Real
> deterministic Python reading real sales history, not an LLM guessing.

**SHOW:** Terminal — run:
```bash
uv run openstore campaign check-growth configs/chai.yaml
```
Let the output print live (it'll show the drafted campaign: title, discount,
SKUs, "state: PENDING_APPROVAL").

> And it's not just reactive — it remembers. Watch the rationale it wrote.

**SHOW:** Switch to browser tab 3 (`/campaign/studio`, Chai House). Find the
new `PENDING_APPROVAL` campaign, open/expand it so the rationale text is
visible on screen. Point at (or read) the line where it references a past
campaign's null lift.

> It's citing its own past outcome data to justify a different angle this
> time. That's a feedback loop, not a one-shot suggestion.

> But — the rule that makes this safe — that campaign never goes live on its
> own. It sits at PENDING_APPROVAL until a human taps a passkey here in
> Campaign Studio.

**SHOW:** Click **Approve**, touch the passkey prompt.

> The moment it's live, every buyer agent shopping this store discovers it
> automatically through the signed offer feed.

---

**[4:00–5:00] — The receipt**
> Now — every completed order gets one of these: an evidence bundle. Say a
> customer disputes a charge sixty days later — "I never authorized that."
> Here's a completed order's bundle, already sitting on disk. I run the
> verifier completely offline. Wifi off. No trust in me or my server
> required.

**SHOW:** Terminal — type this exact command (already tested, works verbatim,
no ID to fill in):
```bash
uv run openstore-verify docs/demo_assets/bundle.json --merchant-jwks docs/demo_assets/jwks/
```
Let all 14 `[PASS]` lines print, then the `VERDICT: AAL2` line.

> Fourteen checks — hash chain, WebAuthn signature, policy, cart — all
> byte-identical, all reproducible from cryptographic first principles.

**SHOW:** Terminal — same command, pointed at the second pre-staged file
(already has one digit flipped in `amount_minor`, nothing to edit live):
```bash
uv run openstore-verify docs/demo_assets/bundle_tampered.json --merchant-jwks docs/demo_assets/jwks/
```

> Flip one digit — it doesn't just fail, it names the exact broken link.

**SHOW:** Point at the `chain_integrity — link[0] (transaction): hash mismatch`
and `amount_consistency — amount_minor mismatch` failure lines, and the
non-zero exit code if your terminal shows it (`echo $?`).

---

**[5:00–5:40] — Back to the adapter point, land it**
> So — coming back to what makes this a *sidecar* and not just another
> checkout plugin: everything you just watched — the compiler, the policy
> signing, the campaign engine, the evidence bundle — none of it cares where
> the catalog or the order data actually lives. Today it's a YAML file
> because that's what let me prove the money path and the agent layer are
> correct without also debugging a live Shopify integration under a
> deadline. But the integration surface a real merchant would touch is one
> function. Wire that to their Postgres orders table, or their Shopify Admin
> API, and the entire system you just saw — compiler, agents, evidence —
> runs unchanged on top of it.

**SHOW:** face / camera, no screen — this is the closing argument, let it
land without a screen distraction.

---

**[5:40–6:00] — Close**
> That's OpenStore. A sidecar that makes any merchant transactable by an AI
> buyer, grows their revenue autonomously, and leaves behind a receipt no one
> can dispute. Thanks for watching.

**SHOW:** face / camera.

---

## Rehearse once before recording

Two things can blow the 6-minute budget if you don't check timing beforehand:

- **WebAuthn passkey prompt latency** — how long your authenticator actually
  takes to respond, twice (registration + signing).
- **Buyer bot LLM round-trip time** in Discord — if `get me two vegan gelatos`
  takes more than a few seconds to answer, either pre-warm the LLM provider
  chain right before recording, or cut to a jump-cut on that wait.

If either is slow on rehearsal, trim the negotiation beat further (it's
already down to one line) or pre-stage the checkout step instead of typing it
live.
