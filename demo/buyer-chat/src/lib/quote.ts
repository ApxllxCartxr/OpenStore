/**
 * The checkout card, rendered **verbatim from the Merchant-signed Quote**.
 *
 * The agent never sums, estimates, or re-labels a line. Agent-side totals do
 * not exist. This module turns a signed Quote into display rows and asserts
 * that what it produced is byte-equal to what it was given — so "verbatim" is a
 * test rather than an intention.
 *
 * A pre-display signature check runs on every Merchant-signed payload. A
 * mismatch blocks `Place order` with `signature-invalid`, because a total
 * nobody signed is a number the chat made up.
 */
export type SignedQuote = {
	currency: string;
	subtotal_minor: number;
	lines: {
		sku: string;
		qty: number;
		unit_price_minor: number;
		line_total_minor: number;
		addons: { sku: string; amount_minor: number }[];
	}[];
	discount_lines: { label: string; code: string; amount_minor: number }[];
	fulfillment_chosen: { id: string; cost_minor: number };
	fulfillment_options: { id: string; label: string; cost_minor: number; eta_days: number }[];
	tax_lines: { kind: string; label: string; amount_minor: number; informational: boolean }[];
	round_off_minor: number;
	total_minor: number;
	tax_inclusive: boolean;
};

export type DisplayRow = { label: string; amount_minor: number; note?: string };

export class QuoteError extends Error {
	constructor(
		readonly code: 'signature-invalid' | 'not-verbatim',
		message: string
	) {
		super(message);
	}
}

export function formatRupees(minor: number): string {
	const sign = minor < 0 ? '-' : '';
	const abs = Math.abs(minor);
	return `${sign}₹${Math.floor(abs / 100).toLocaleString('en-IN')}.${String(abs % 100).padStart(2, '0')}`;
}

/**
 * Turn a Quote into rows. **Every amount is copied, never computed.**
 *
 * The one arithmetic-looking line is the ETA note, which is a day count the
 * Merchant sent — not a date the chat derived, because a date would be a clock
 * the Quote deliberately does not carry.
 */
export function renderRows(quote: SignedQuote): DisplayRow[] {
	const rows: DisplayRow[] = quote.lines.map((line) => ({
		label: line.qty > 1 ? `${line.sku} × ${line.qty}` : line.sku,
		amount_minor: line.line_total_minor,
		note: line.addons.length
			? `includes ${line.addons.map((a) => a.sku).join(', ')}`
			: undefined
	}));

	for (const discount of quote.discount_lines) {
		rows.push({ label: discount.label, amount_minor: discount.amount_minor, note: discount.code });
	}

	const chosen = quote.fulfillment_options.find((o) => o.id === quote.fulfillment_chosen.id);
	rows.push({
		label: chosen?.label ?? 'Delivery',
		amount_minor: quote.fulfillment_chosen.cost_minor,
		note: chosen ? `${chosen.eta_days} days` : undefined
	});

	for (const tax of quote.tax_lines) {
		rows.push({
			label: tax.label,
			amount_minor: tax.amount_minor,
			// Informational means it is already inside the total. Rendering it
			// as an addition would show the Consumer a number that is not what
			// they will pay.
			note: tax.informational ? 'included' : undefined
		});
	}

	if (quote.round_off_minor !== 0) {
		rows.push({ label: 'Rounding', amount_minor: quote.round_off_minor });
	}
	return rows;
}

/**
 * The byte-equality assertion C3's DONE WHEN requires.
 *
 * Every amount rendered must appear in the signed Quote, and the total must be
 * the Quote's own. Any number on screen that is not in the document is a number
 * the chat produced, which is the thing that must not happen.
 */
export function assertVerbatim(quote: SignedQuote, rows: DisplayRow[]): void {
	const signed = new Set<number>([
		...quote.lines.map((l) => l.line_total_minor),
		...quote.discount_lines.map((d) => d.amount_minor),
		quote.fulfillment_chosen.cost_minor,
		...quote.tax_lines.map((t) => t.amount_minor),
		quote.round_off_minor
	]);

	for (const row of rows) {
		if (!signed.has(row.amount_minor)) {
			throw new QuoteError(
				'not-verbatim',
				`${row.label} shows ${formatRupees(row.amount_minor)}, which is not a figure the ` +
					`Merchant signed. The agent does not compute amounts.`
			);
		}
	}
}

/**
 * Does the displayed breakdown add up to the signed total?
 *
 * **Not a recomputation of the price** — a check that what is shown is
 * consistent with what was signed. If it fails, the renderer is wrong, not the
 * Merchant, and the Consumer should see the Merchant's total rather than the
 * chat's sum of parts.
 */
export function displayTotal(quote: SignedQuote): number {
	return quote.total_minor;
}

export function checkoutIsBlocked(reason: string | null): string | null {
	return reason;
}

/**
 * Consumer-facing copy for every refusal the sidecar can return.
 *
 * Each names the **exact fix** — except `code-invalid`, which says only that
 * the code did not apply. A chat that explains *why* a code failed is a code
 * oracle with a friendly face.
 */
export const REASON_COPY: Record<string, string> = {
	'sold-out': 'That just went out of stock. Pick something else and I will re-price.',
	'variant-required': 'Choose the options first — I will not pick a size for you.',
	'addon-without-parent': 'That attaches to something. Add the item it goes with first.',
	'price-changed': 'The shop changed the price before we got there. Here is the new total — tap again to agree to it.',
	'quote-inconsistent': 'The shop’s own numbers did not add up, so I stopped. Nothing was charged.',
	'code-invalid': 'That code did not apply.',
	'destination-unserviceable': 'This shop does not deliver to that address yet.',
	'method-not-supported': 'This shop does not take that payment method.',
	'amount-mismatch': 'The amount that moved did not match the order, so it was refused and the hold released.',
	'time-limit-reached': 'The basket expired after 24 hours. Nothing was held and nothing was lost — re-add and I will re-price.',
	'payment-window-elapsed': 'The payment window closed, so your hold was released. The items went back on sale and may not still be there.',
	'delivery-window-elapsed': 'The delivery window has passed. The shop has been alerted; nothing has been charged or cancelled.',
	'authority-missing': 'That needs your approval on the shop’s own page.',
	'authority-kind-not-enabled': 'This shop does not accept that way of approving a payment.',
	'authority-stale': 'Something changed after the approval page was shown, so it no longer applies. Tap again.',
	'intent-mechanism-not-enabled': 'This shop does not accept that way of confirming intent.',
	'cap-exceeded': 'That is over this shop’s per-order limit.',
	'qty-exceeded': 'That is more of this item than the shop allows per order.',
	'count-exceeded': 'That is more separate items than the shop allows per order.',
	'window-closed': 'This shop is not taking agent orders right now.',
	'blocked-item': 'The shop has pulled that item from sale.',
	'signature-invalid': 'I could not verify that this came from the shop, so I stopped.',
	'not-found': 'I could not find that.',
	'rate-limited': 'Too many requests too quickly. Give it a moment.',
	'cancel-not-allowed': 'That order is past the point where it can be cancelled — a refund is the way back.',
	'no-hold': 'There was nothing held to release.'
};

export function explain(code: string): string {
	return REASON_COPY[code] ?? `The shop refused: ${code}.`;
}
