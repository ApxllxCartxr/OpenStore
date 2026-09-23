# `design/` — assets only

Copied whole from `~/projects/portfolío/frontend` (§4). **Assets only**: the
import firewall refuses anything importable in here, because a `.ts` module in
a directory both SvelteKit roots read is shared code between two roots wearing
an asset's hat.

## Font roles

Two families, and only two.

- **EB Garamond** — the one display face, used **once per page**. Here: the shop
  name, the chat name, the receipt's title. It is also `--font-serif`, so the
  flourish role resolves to the same face rather than to a second serif.
- **Open Sauce Sans** — everything else: body, titles, and the money surfaces.
  400/500/600 only.

`--font-mono` survives as a **role, not a family**: every price, paise figure,
SKU, reason code, hash, ledger entry and countdown still names it, and it
resolves to Open Sauce Sans with `font-variant-numeric: tabular-nums`. The
figures line up, which is the part that made those surfaces read as
instruments; the monospaced face was how that used to be bought, not the point
of it. Keeping the token means no surface has to learn that Iosevka went away.

## One change from the portfolio's `app.css`

`@import 'tailwindcss'` has been lifted out of `tokens.css` and into each app's
own `app.css`. This directory is outside every package, so a bare package import
here has no `node_modules` to resolve against — it works in local dev and fails
at image build time, which is the worst place to find out. Everything else is
the portfolio's, unchanged.

## Palette

Token names are unchanged, so **no component ever references a literal colour**.
There is **one theme**: warm paper (`#f6f5f2`) under brighter cards. The dark
palette, the `prefers-color-scheme` branch and the `data-theme` stamp in each
`app.html` are gone — nothing chooses a theme any more, so nothing had to be
stamped before first paint.

Overall text size has **one lever**: `--t-body`, which `html` sets its
`font-size` from. Every rem on every surface is measured against it. The two
surfaces that size in px instead — the `/agentic` console and the admin
charts' SVG labels — are out of that lever's reach and carry their own ladder.

## The three surfaces, same tokens, different weightings

- **Merchant site** — the portfolio look at full strength. Warm paper, generous
  whitespace, EB Garamond on the shop name, `--accent` for buy actions.
- **Buyer chat** — Claude-shaped. Centred 68ch column, `--accent-2` primary, so
  chat and shop never look like the same app.
- **`/agentic` console** — instrument panel. Dense, hairline rules
  (`--line`, never the accent), `--bg-sunken` panels, tabular figures, **no
  animation**. Data density over whitespace. It reads like something you check
  at 2am, because SPEC §14 says that is what it is for.

## Licensing — read before recording, hosting, or sharing

The folder ships `LICENSE-EBGaramond.txt` (OFL 1.1) and **no licence file for
Open Sauce Sans**, which is believed OFL — that is **unverified here**. Verify
it before anything is recorded, hosted, or shared.

The commercial PP Kyoto face and the OFL Iosevka are no longer loaded, and
their files have been deleted along with the roles that used them.

## Accessibility, at any density

`:focus-visible` outlines stay. `prefers-reduced-motion` kills every animation.
`--comment` is decoration-only and never body copy. Every interactive target is
≥ 44px on touch.
