/**
 * The Merchant's half of §16.11, checked against the pinned worked example.
 *
 * These are the same numbers the sidecar's golden vectors pin and the Gate
 * re-derives independently. Three implementations agreeing is the point; if
 * this file and the Gate disagree, §16.11 is right and both are wrong.
 */
import { describe, expect, it } from 'vitest';
import {
	apportion,
	buildQuote,
	extractTax,
	formatRupees,
	PricingError,
	splitCgstSgst,
	type PricingItem
} from '../src/lib/pricing.ts';

const ITEMS = new Map<string, PricingItem>([
	['SD-TOTE-BLK-M', { sku: 'SD-TOTE-BLK-M', price_minor: 89900, hsn_sac: '4202', gst_rate_bp: 1800, tags: [] }],
	['SD-CHARMBAR-SEAT', { sku: 'SD-CHARMBAR-SEAT', price_minor: 150000, hsn_sac: '999799', gst_rate_bp: 1800, tags: ['service'] }],
	['SD-GIFTWRAP', { sku: 'SD-GIFTWRAP', price_minor: 9900, hsn_sac: '', gst_rate_bp: 0, tags: ['addon'] }],
	['SD-STICKERS', { sku: 'SD-STICKERS', price_minor: 19900, hsn_sac: '4911', gst_rate_bp: 1800, tags: [] }],
	['SD-PHONECHARM', { sku: 'SD-PHONECHARM', price_minor: 44900, hsn_sac: '7117', gst_rate_bp: 300, tags: [] }]
]);

const ROI = { id: 'rest-of-india', label: 'Rest of India', cost_minor: 9900, eta_days: 5 };
const KA = { id: 'karnataka', label: 'Karnataka', cost_minor: 4900, eta_days: 2 };

describe('§16.11 arithmetic', () => {
	it('extracts tax half-up, not banker’s', () => {
		// round() would give 2 here; ROUND_HALF_UP gives 575 on the real case.
		expect(extractTax(1150, 10000)).toBe(575);
		expect(extractTax(24800, 1800)).toBe(3783);
		expect(extractTax(0, 1800)).toBe(0);
		expect(extractTax(100, 0)).toBe(0);
	});

	it('apportions by largest remainder, exactly, ties by SKU', () => {
		expect([...apportion(9900, [['A', 99800], ['B', 150000]]).values()]).toEqual([3955, 5945]);
		const discount = apportion(10000, [['SD-TOTE-BLK-M', 89900], ['SD-PHONECHARM', 44900]]);
		expect(discount.get('SD-TOTE-BLK-M')).toBe(6669);
		expect(discount.get('SD-PHONECHARM')).toBe(3331);

		// An exact tie goes to the lower SKU, deterministically.
		expect(apportion(1, [['b-sku', 50], ['a-sku', 50]]).get('a-sku')).toBe(1);
	});

	it('never loses a paise', () => {
		for (const amount of [1, 7, 99, 4900, 9900, 123457]) {
			const shares = apportion(amount, [['a', 3], ['b', 5], ['c', 7]]);
			expect([...shares.values()].reduce((x, y) => x + y, 0)).toBe(amount);
		}
	});

	it('gives the odd paise to SGST', () => {
		expect(splitCgstSgst(3783)).toEqual([1891, 1892]);
		expect(splitCgstSgst(23788)).toEqual([11894, 11894]);
	});
});

describe('the pinned worked example', () => {
	const quote = buildQuote({
		lines: [
			{ sku: 'SD-TOTE-BLK-M', qty: 1 },
			{ sku: 'SD-GIFTWRAP', qty: 1, parent: 'SD-TOTE-BLK-M' },
			{ sku: 'SD-CHARMBAR-SEAT', qty: 1 }
		],
		items: ITEMS,
		destinationState: 'MH',
		homeState: 'KA',
		zone: ROI,
		taxInclusive: true
	});

	it('reaches §16.11’s numbers', () => {
		expect(quote.subtotal_minor).toBe(249800);
		expect(quote.total_minor).toBe(259700);
		expect(quote.round_off_minor).toBe(0);
	});

	it('carries both GST splits in one quote', () => {
		const amounts = quote.tax_lines.map((t) => [t.kind, t.amount_minor]);
		expect(amounts).toContainEqual(['IGST', 15827]);
		expect(amounts).toContainEqual(['CGST', 11894]);
		expect(amounts).toContainEqual(['SGST', 11894]);
	});

	it('taxes the service where it is performed, whatever the destination', () => {
		const seat = quote.lines.find((l) => l.sku === 'SD-CHARMBAR-SEAT')!;
		const tote = quote.lines.find((l) => l.sku === 'SD-TOTE-BLK-M')!;
		expect(seat.place_of_supply).toBe('KA');
		expect(tote.place_of_supply).toBe('MH');
	});

	it('folds the add-on into its parent and never emits it as a quote line', () => {
		expect(quote.lines.map((l) => l.sku)).toEqual(['SD-CHARMBAR-SEAT', 'SD-TOTE-BLK-M']);
		const tote = quote.lines.find((l) => l.sku === 'SD-TOTE-BLK-M')!;
		expect(tote.line_total_minor).toBe(99800);
		expect(tote.addons).toEqual([{ sku: 'SD-GIFTWRAP', amount_minor: 9900 }]);
	});

	it('carries a day count, never a date', () => {
		expect(quote.fulfillment_options[0]!.eta_days).toBe(5);
		expect(JSON.stringify(quote)).not.toMatch(/\d{4}-\d{2}-\d{2}/);
	});
});

describe('discounts', () => {
	it('apportions across two rates and lands on the pinned total', () => {
		const quote = buildQuote({
			lines: [
				{ sku: 'SD-TOTE-BLK-M', qty: 1 },
				{ sku: 'SD-PHONECHARM', qty: 1 }
			],
			items: ITEMS,
			destinationState: 'KA',
			homeState: 'KA',
			zone: KA,
			discount: { code: 'SPOILED10', label: 'Launch code', amount_minor: -10000 },
			taxInclusive: true
		});
		expect(quote.subtotal_minor).toBe(134800);
		expect(quote.total_minor).toBe(129700);
		const amounts = quote.tax_lines.map((t) => [t.kind, t.rate_bp, t.amount_minor]);
		expect(amounts).toContainEqual(['CGST', 900, 6597]);
		expect(amounts).toContainEqual(['SGST', 900, 6598]);
		expect(amounts).toContainEqual(['CGST', 150, 629]);
		expect(amounts).toContainEqual(['SGST', 150, 629]);
	});
});

describe('the inclusive/exclusive flag', () => {
	it('is carried both ways, because getting it backwards double-charges', () => {
		const base = {
			lines: [{ sku: 'SD-STICKERS', qty: 1 }],
			items: ITEMS,
			destinationState: 'KA',
			homeState: 'KA',
			zone: KA
		};
		expect(buildQuote({ ...base, taxInclusive: true }).tax_lines.every((t) => t.informational)).toBe(true);
		expect(buildQuote({ ...base, taxInclusive: false }).tax_lines.some((t) => t.informational)).toBe(false);
	});
});

describe('add-ons', () => {
	it('refuses an orphan', () => {
		expect(() =>
			buildQuote({
				lines: [{ sku: 'SD-GIFTWRAP', qty: 1 }],
				items: ITEMS,
				destinationState: 'KA',
				homeState: 'KA',
				zone: KA,
				taxInclusive: true
			})
		).toThrow(PricingError);
	});

	it('refuses one naming a line that is not there', () => {
		expect(() =>
			buildQuote({
				lines: [
					{ sku: 'SD-STICKERS', qty: 1 },
					{ sku: 'SD-GIFTWRAP', qty: 1, parent: 'SD-TOTE-BLK-M' }
				],
				items: ITEMS,
				destinationState: 'KA',
				homeState: 'KA',
				zone: KA,
				taxInclusive: true
			})
		).toThrow(/no line SD-TOTE-BLK-M/);
	});
});

describe('determinism', () => {
	it('is byte-identical across repeat calls', () => {
		const input = {
			lines: [{ sku: 'SD-TOTE-BLK-M', qty: 2 }, { sku: 'SD-STICKERS', qty: 1 }],
			items: ITEMS,
			destinationState: 'MH',
			homeState: 'KA',
			zone: ROI,
			taxInclusive: true
		};
		expect(JSON.stringify(buildQuote(input))).toBe(JSON.stringify(buildQuote(input)));
	});

	it('does not depend on line order', () => {
		const shared = { items: ITEMS, destinationState: 'KA', homeState: 'KA', zone: KA, taxInclusive: true };
		const a = buildQuote({ ...shared, lines: [{ sku: 'SD-STICKERS', qty: 1 }, { sku: 'SD-TOTE-BLK-M', qty: 1 }] });
		const b = buildQuote({ ...shared, lines: [{ sku: 'SD-TOTE-BLK-M', qty: 1 }, { sku: 'SD-STICKERS', qty: 1 }] });
		expect(JSON.stringify(a)).toBe(JSON.stringify(b));
	});
});

describe('money formatting', () => {
	it('uses integer arithmetic', () => {
		expect(formatRupees(259700)).toBe('₹2,597.00');
		expect(formatRupees(5)).toBe('₹0.05');
		expect(formatRupees(-2500)).toBe('-₹25.00');
	});
});
