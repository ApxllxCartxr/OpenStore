/**
 * §16.11's arithmetic, implemented Merchant-side.
 *
 * This is the **second** implementation of one pinned specification. The
 * sidecar's Gate re-checks these sums independently in `quote-consistent`, and
 * the two do not share a module on purpose: if they did, the check would prove
 * only that the sidecar agrees with itself.
 *
 * Two implementations that round differently fire `quote-inconsistent` on a
 * *correct* quote, which looks like a bug in the money core and is actually a
 * bug in one of them. §16.11 is the authority; where they disagree, both are
 * wrong until they agree with it.
 *
 * Everything here is `bigint` or `number` in paise. There is no float in this
 * file, and the one place division happens uses exact integer arithmetic with
 * an explicit rounding rule.
 */

export type Line = { sku: string; qty: number; parent?: string };

export type Addon = { sku: string; amount_minor: number };

export type QuoteLine = {
	sku: string;
	qty: number;
	unit_price_minor: number;
	line_total_minor: number;
	hsn_sac: string;
	gst_rate_bp: number;
	place_of_supply: string;
	addons: Addon[];
};

export type TaxLine = {
	kind: 'CGST' | 'SGST' | 'IGST';
	label: string;
	rate_bp: number;
	amount_minor: number;
	informational: boolean;
};

export type Quote = {
	currency: 'INR';
	subtotal_minor: number;
	lines: QuoteLine[];
	discount_lines: { label: string; code: string; amount_minor: number }[];
	fulfillment_options: { id: string; label: string; cost_minor: number; eta_days: number }[];
	fulfillment_chosen: { id: string; cost_minor: number };
	tax_lines: TaxLine[];
	round_off_minor: number;
	total_minor: number;
	tax_inclusive: boolean;
};

/**
 * Step 5: extract the tax already inside an inclusive amount.
 *
 * `ROUND_HALF_UP`, not banker's rounding — they disagree on exactly the .5
 * cases money hits. Done in integers: `(inclusive * rate * 2 + divisor) / (2 *
 * divisor)` is half-up without ever touching a float.
 */
export function extractTax(inclusiveMinor: number, rateBp: number): number {
	if (rateBp === 0) return 0;
	const divisor = 10000 + rateBp;
	const numerator = BigInt(inclusiveMinor) * BigInt(rateBp) * 2n + BigInt(divisor);
	return Number(numerator / (BigInt(divisor) * 2n));
}

/**
 * Step 4: largest remainder, stated once so it is never re-derived.
 *
 * Floor each raw share; the leftover paise go one each to the largest
 * fractional parts, descending, ties broken by SKU ascending. The shares sum
 * **exactly** to the amount — an apportionment that loses a paise is a total
 * that does not add up, which the Gate refuses.
 */
export function apportion(amount: number, weights: [string, number][]): Map<string, number> {
	const total = weights.reduce((sum, [, w]) => sum + w, 0);
	const shares = new Map<string, number>();
	if (total === 0) {
		for (const [sku] of weights) shares.set(sku, 0);
		return shares;
	}

	// Remainders compared as integers: `amount * w % total`, so no float ever
	// decides which line gets the spare paise.
	const remainders: [string, number][] = [];
	let assigned = 0;
	for (const [sku, weight] of weights) {
		const exact = amount * weight;
		const floor = Math.floor(exact / total);
		shares.set(sku, floor);
		assigned += floor;
		remainders.push([sku, exact % total]);
	}

	remainders.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
	for (let i = 0; i < amount - assigned; i += 1) {
		const entry = remainders[i];
		if (!entry) break;
		shares.set(entry[0], (shares.get(entry[0]) ?? 0) + 1);
	}

	const sum = [...shares.values()].reduce((a, b) => a + b, 0);
	if (sum !== amount) throw new Error(`apportionment lost ${amount - sum} paise`);
	return shares;
}

/** Step 6, the trap this section exists for. SGST takes the odd paise. */
export function splitCgstSgst(tax: number): [number, number] {
	const cgst = Math.floor(tax / 2);
	return [cgst, tax - cgst];
}

export function rateLabel(rateBp: number): string {
	const whole = Math.floor(rateBp / 100);
	const frac = rateBp % 100;
	return frac === 0 ? `${whole}%` : `${whole}.${Math.floor(frac / 10)}%`;
}

export type PricingItem = {
	sku: string;
	price_minor: number;
	hsn_sac: string;
	gst_rate_bp: number;
	tags: string[];
};

export type PricingInput = {
	lines: Line[];
	items: Map<string, PricingItem>;
	destinationState: string;
	homeState: string;
	zone: { id: string; label: string; cost_minor: number; eta_days: number };
	discount?: { code: string; label: string; amount_minor: number };
	taxInclusive: boolean;
};

export class PricingError extends Error {
	constructor(
		readonly code: string,
		message: string
	) {
		super(message);
	}
}

/**
 * The whole Quote, in §16.11's order of operations.
 *
 * Carries **no clock**: an ETA is a day count and never a date, or midnight
 * turns every re-quote into a spurious `price-changed`.
 */
export function buildQuote(input: PricingInput): Quote {
	const { items, lines, destinationState, homeState, zone } = input;

	// 1. Fold Add-ons into their parents. An Add-on is a cart line with its own
	//    SKU and Attestation, and never a Quote Line of its own: as a composite
	//    supply its amount folds into the parent's taxable value and inherits
	//    the parent's rate, HSN/SAC and Place of Supply.
	const parents = new Map<string, Line>();
	const addons = new Map<string, Line[]>();
	for (const line of lines) {
		const item = items.get(line.sku);
		if (!item) throw new PricingError('not-found', `no Catalogue Item ${line.sku}`);
		if (item.tags.includes('addon')) {
			if (!line.parent) {
				throw new PricingError(
					'addon-without-parent',
					`${line.sku} attaches to a line; it is never sold alone`
				);
			}
			addons.set(line.parent, [...(addons.get(line.parent) ?? []), line]);
		} else {
			parents.set(line.sku, line);
		}
	}
	for (const parentSku of addons.keys()) {
		if (!parents.has(parentSku)) {
			throw new PricingError(
				'addon-without-parent',
				`no line ${parentSku} for its Add-on to attach to`
			);
		}
	}

	const quoteLines: QuoteLine[] = [];
	for (const [sku, line] of parents) {
		const item = items.get(sku)!;
		const folded = (addons.get(sku) ?? [])
			.slice()
			.sort((a, b) => (a.sku < b.sku ? -1 : 1))
			.map((a) => ({ sku: a.sku, amount_minor: items.get(a.sku)!.price_minor * a.qty }));
		quoteLines.push({
			sku,
			qty: line.qty,
			unit_price_minor: item.price_minor,
			line_total_minor:
				item.price_minor * line.qty + folded.reduce((s, a) => s + a.amount_minor, 0),
			hsn_sac: item.hsn_sac,
			gst_rate_bp: item.gst_rate_bp,
			// Per line: the Destination state for goods, the place of performance
			// for a service sold at the premises. This is what lets one basket
			// carry IGST on one line and CGST/SGST on another.
			place_of_supply: item.tags.includes('service') ? homeState : destinationState,
			addons: folded
		});
	}
	quoteLines.sort((a, b) => (a.sku < b.sku ? -1 : 1));

	const subtotal = quoteLines.reduce((s, l) => s + l.line_total_minor, 0);
	const weights: [string, number][] = quoteLines.map((l) => [l.sku, l.line_total_minor]);

	// 2–3. Discount, then fulfillment. Both apportion on the same basis: the
	//      line inclusive total from step 1.
	const discountLines = input.discount ? [{ ...input.discount }] : [];
	const discountShares = input.discount
		? apportion(Math.abs(input.discount.amount_minor), weights)
		: new Map<string, number>();
	const shippingShares = apportion(zone.cost_minor, weights);

	// 5–6. Extract tax per line, then split by Place of Supply.
	const taxes = new Map<string, { kind: TaxLine['kind']; rate_bp: number; amount: number }>();
	const add = (kind: TaxLine['kind'], rate_bp: number, amount: number) => {
		const key = `${kind}:${rate_bp}`;
		const existing = taxes.get(key);
		taxes.set(key, { kind, rate_bp, amount: (existing?.amount ?? 0) + amount });
	};

	for (const line of quoteLines) {
		const inclusive =
			line.line_total_minor -
			(discountShares.get(line.sku) ?? 0) +
			(shippingShares.get(line.sku) ?? 0);
		const tax = extractTax(inclusive, line.gst_rate_bp);
		if (line.place_of_supply === homeState) {
			const [cgst, sgst] = splitCgstSgst(tax);
			add('CGST', Math.floor(line.gst_rate_bp / 2), cgst);
			add('SGST', Math.floor(line.gst_rate_bp / 2), sgst);
		} else {
			add('IGST', line.gst_rate_bp, tax);
		}
	}

	const taxLines: TaxLine[] = [...taxes.values()]
		.sort((a, b) => (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : a.rate_bp - b.rate_bp))
		.map((t) => ({
			kind: t.kind,
			label: `${t.kind} ${rateLabel(t.rate_bp)}`,
			rate_bp: t.rate_bp,
			amount_minor: t.amount,
			// Everything seeded is tax-inclusive, so tax is reported and never
			// added. Getting this backwards double-charges every order.
			informational: input.taxInclusive
		}));

	const discountTotal = discountLines.reduce((s, d) => s + d.amount_minor, 0);

	return {
		currency: 'INR',
		subtotal_minor: subtotal,
		lines: quoteLines,
		discount_lines: discountLines,
		fulfillment_options: [
			{ id: zone.id, label: zone.label, cost_minor: zone.cost_minor, eta_days: zone.eta_days }
		],
		fulfillment_chosen: { id: zone.id, cost_minor: zone.cost_minor },
		tax_lines: taxLines,
		// 7. Always 0 in v1. The seeded Merchant does not round to the rupee, so
		//    the line exists and carries zero. A non-zero value is a bug.
		round_off_minor: 0,
		// 8. subtotal + fulfillment + discounts (negative) + round_off.
		total_minor: subtotal + zone.cost_minor + discountTotal,
		tax_inclusive: input.taxInclusive
	};
}

/** Paise to a rupee string, by integer arithmetic. */
export function formatRupees(minor: number): string {
	const sign = minor < 0 ? '-' : '';
	const abs = Math.abs(minor);
	const rupees = Math.floor(abs / 100);
	const paise = abs % 100;
	return `${sign}₹${rupees.toLocaleString('en-IN')}.${String(paise).padStart(2, '0')}`;
}
