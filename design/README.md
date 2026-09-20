# `design/` — assets only

Copied whole from `~/projects/portfolío/frontend` (§4). **Assets only**: the
import firewall refuses anything importable in here, because a `.ts` module in
a directory both SvelteKit roots read is shared code between two roots wearing
an asset's hat.

## Font roles, kept from the portfolio's own comments

- **EB Garamond** — the one display face, used **once per page**. Here: the shop
  name, the chat name, the receipt's title.
- **Open Sauce Sans** — body and titles. 400/500/600 only.
- **Iosevka Term SS08** — **load-bearing, not decoration.** Every price, every
  paise figure, every SKU, every reason code, every hash, every ledger entry,
  every countdown. This font is what makes the money surfaces look like
  instruments instead of a landing page.
- **PP Kyoto** — serif flourishes and pull quotes only.

## Palette

Token names are unchanged, so **no component ever references a literal colour**.
Light is the default (paper `#f6f5f2`); dark is Tokyo Night. Theme is stamped on
`<html>` before first paint; the media query only decides for visitors who never
chose.

## The three surfaces, same tokens, different weightings

- **Merchant site** — the portfolio look at full strength. Warm paper, generous
  whitespace, EB Garamond on the shop name, `--accent` for buy actions.
- **Buyer chat** — Claude-shaped. Centred 68ch column, `--accent-2` primary, so
  chat and shop never look like the same app.
- **`/agentic` console** — instrument panel. Mono-dominant, hairline rules
  (`--line`, never the accent), `--bg-sunken` panels, tabular figures, **no
  animation**. Data density over whitespace. It reads like something you check
  at 2am, because SPEC §14 says that is what it is for.

## Licensing — read before recording, hosting, or sharing

The folder ships `LICENSE-EBGaramond.txt` and `LICENSE-Iosevka.txt` (both OFL
1.1) and **no licence file for the other two**.

- **Open Sauce Sans** is believed OFL. That is **unverified here**.
- **PP Kyoto is a commercial Pangram Pangram face.**

Fine for a demo on your own machine. A question the moment anything is recorded,
hosted, or shared: verify Open Sauce Sans, and substitute or buy PP Kyoto.
Written down now so it is not discovered later.

## Accessibility, at any density

`:focus-visible` outlines stay. `prefers-reduced-motion` kills every animation.
`--comment` is decoration-only and never body copy. Every interactive target is
≥ 44px on touch.
