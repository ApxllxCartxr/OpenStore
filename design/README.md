# `design/` — assets only

Copied whole from `~/projects/portfolío/frontend`. Assets only. The import firewall refuses anything importable here. A `.ts` module in a directory both SvelteKit roots read is shared code between two roots wearing an asset's hat.

## Font roles

Two families, and only two.

### EB Garamond

The one display face, used once per page. It covers the shop name, the chat name, and the receipt title. It is also `--font-serif`. The flourish role resolves to the same face rather than to a second serif.

### Open Sauce Sans

Everything else: body, titles, and the money surfaces. Weights 400, 500, and 600 only.

`--font-mono` survives as a role, not a family. Every price, paise figure, SKU, reason code, hash, ledger entry, and countdown still names it. It resolves to Open Sauce Sans with `font-variant-numeric: tabular-nums`. The figures line up. That alignment is what made those surfaces read as instruments. The monospaced face was how that used to be bought, not the point of it. Keeping the token means no surface must learn that Iosevka went away.

## One change from the portfolio's `app.css`

`@import 'tailwindcss'` moved out of `tokens.css` and into each app's own `app.css`. This directory sits outside every package. A bare package import here has no `node_modules` to resolve against. It works in local dev and fails at image build time. That is the worst place to find out. Everything else is the portfolio's, unchanged.

## Palette

Token names are unchanged. No component references a literal colour. There is one theme: warm paper (`#f6f5f2`) under brighter cards. The dark palette is gone. The `prefers-color-scheme` branch is gone. The `data-theme` stamp in each `app.html` is gone. Nothing chooses a theme any more, so nothing had to be stamped before first paint.

Text size has one lever: `--t-body`. `html` sets its `font-size` from it. Every rem on every surface is measured against it. Two surfaces size in px instead: the `/agentic` console and the admin charts' SVG labels. They sit outside that lever's reach and carry their own ladder.

## The three surfaces, same tokens, different weightings

- Merchant site: the portfolio look at full strength. Warm paper, generous whitespace, EB Garamond on the shop name, `--accent` for buy actions.
- Buyer chat: Claude-shaped. Centred 68ch column, `--accent-2` primary. Chat and shop never look like the same app.
- `/agentic` console: instrument panel. Dense, hairline rules (`--line`, never the accent), `--bg-sunken` panels, tabular figures, no animation. Data density over whitespace. It reads like something you check at 2am, because Specification §14 says that is what it is for.

## Licensing — read before recording, hosting, or sharing

The folder ships `LICENSE-EBGaramond.txt` (OFL 1.1). It ships no licence file for Open Sauce Sans, which is believed OFL. That belief is unverified here. Verify it before anything is recorded, hosted, or shared.

The commercial PP Kyoto face and the OFL Iosevka are no longer loaded. Their files are deleted along with the roles that used them.

## Accessibility, at any density

`:focus-visible` outlines stay. `prefers-reduced-motion` kills every animation. `--comment` is decoration-only and never body copy. Every interactive target is 44px or larger on touch.
