/**
 * Playspool: toys and games, home state Punjab.
 *
 * `PS-TENNISBALL3` mirrors Furrow's `FW-TENNISBALL3` — a tennis-ball 3-pack
 * is exactly the kind of thing sold on both sides of the kids/pets line, at
 * two different prices from two different merchants.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['blocks', 'Wooden building blocks', {}],
	['boardgame', 'Board game', {}],
	['plush-bear', 'Plush bear', {}],
	['puzzle', '500-piece puzzle', {}],
	['rc-car', 'Remote control car', {}],
	['cardgame', 'Card game', {}],
	['colouring-book', 'Colouring book', {}],
	['tennisball', 'Tennis ball, 3-pack', {}]
];

const ITEMS: Row[] = [
	{ sku: 'PS-BLOCKS', group: 'blocks', name: 'Wooden building blocks, 60-piece', price: 89900, hsn: '9503', gst: 1200, stock: 14, low: 4, options: {}, tags: [] },
	{ sku: 'PS-BOARDGAME', group: 'boardgame', name: 'Board game — family strategy', price: 119900, hsn: '9504', gst: 1200, stock: 9, low: 3, options: {}, tags: [] },
	{ sku: 'PS-PLUSHBEAR', group: 'plush-bear', name: 'Plush bear', price: 59900, hsn: '9503', gst: 1200, stock: 2, low: 4, options: {}, tags: [] },
	{ sku: 'PS-PUZZLE-500', group: 'puzzle', name: '500-piece puzzle', price: 44900, hsn: '9503', gst: 1200, stock: 20, low: 5, options: {}, tags: [] },
	{ sku: 'PS-RCCAR', group: 'rc-car', name: 'Remote control car', price: 179900, hsn: '9503', gst: 1200, stock: 6, low: 3, options: {}, tags: [] },
	{ sku: 'PS-CARDGAME', group: 'cardgame', name: 'Card game', price: 34900, hsn: '9504', gst: 1200, stock: 30, low: 6, options: {}, tags: [] },
	{ sku: 'PS-COLOURINGBOOK', group: 'colouring-book', name: 'Colouring book', price: 14900, hsn: '4903', gst: 0, nilRated: true, stock: 45, low: 8, options: {}, tags: [] },
	// The overlap: see file header.
	{ sku: 'PS-TENNISBALL3', group: 'tennisball', name: 'Tennis ball, 3-pack', price: 19900, hsn: '9506', gst: 1200, stock: 40, low: 8, options: {}, tags: [] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const PLAYSPOOL: Profile = {
	shop: 'Playspool',
	domain: 'playspool.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'Playspool Toys',
		gstin: '03AABCP9753T1ZW',
		state: 'PB',
		taxInclusive: true,
		invoicePrefix: 'PS'
	},
	zones: [
		{ id: 'punjab', label: 'Punjab', states: ['PB'], cost: 4900, eta: 2 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 12900, eta: 6 }
	],
	unserviceable: ['79'],
	codes: [
		{ code: 'PLAY10', label: 'Playroom discount', amount: 10000, visibility: 'public', maxUses: 50 },
		{ code: 'PSPRIVATE-6HYD-2FGL', label: 'Private code', amount: 25000, visibility: 'private', maxUses: 1 }
	]
};
