/**
 * CircuitYard: electronics and small gadgets, home state Maharashtra.
 *
 * Third shop, third state (SpoiledDuckie is Karnataka, Dog-Eared West
 * Bengal): a Consumer address in Maharashtra now produces CGST/SGST here
 * while it produces IGST at the other two, and vice versa for a Karnataka or
 * West Bengal address — the split stays a property of the pair, not of one
 * shop's code path.
 *
 * `CY-BATT-AA4` is deliberately the same product IronList also sells
 * (`IL-BATT-AA4`, its own SKU, its own price and stock) — two merchants
 * selling AA batteries is the ordinary case a multi-shop agent has to reason
 * about: which one has it, and which one is cheaper.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['usbc-cable', 'USB-C cable', { length: ['1m', '2m'] }],
	['wall-charger', 'Wall charger', {}],
	['earbuds', 'Wireless earbuds', {}],
	['powerbank', 'Power bank', {}],
	['aa-batteries', 'AA batteries, 4-pack', {}],
	['hdmi-cable', 'HDMI cable', {}],
	['phone-case', 'Phone case', { colour: ['black', 'clear'] }],
	['screen-protector', 'Screen protector', {}]
];

const ITEMS: Row[] = [
	{ sku: 'CY-USBC-1M', group: 'usbc-cable', name: 'USB-C cable — 1m', price: 39900, hsn: '8544', gst: 1800, stock: 40, low: 8, options: { length: '1m' }, tags: [] },
	{ sku: 'CY-USBC-2M', group: 'usbc-cable', name: 'USB-C cable — 2m', price: 59900, hsn: '8544', gst: 1800, stock: 25, low: 6, options: { length: '2m' }, tags: [] },
	{ sku: 'CY-CHARGER-20W', group: 'wall-charger', name: 'Wall charger, 20W', price: 129900, hsn: '8504', gst: 1800, stock: 18, low: 4, options: {}, tags: [] },
	{ sku: 'CY-EARBUDS', group: 'earbuds', name: 'Wireless earbuds', price: 249900, hsn: '8518', gst: 1800, stock: 2, low: 3, options: {}, tags: [] },
	{ sku: 'CY-POWERBANK-10K', group: 'powerbank', name: 'Power bank, 10,000mAh', price: 189900, hsn: '8507', gst: 1800, stock: 12, low: 4, options: {}, tags: [] },
	// The overlap: see file header.
	{ sku: 'CY-BATT-AA4', group: 'aa-batteries', name: 'AA batteries, 4-pack', price: 24900, hsn: '8506', gst: 2800, stock: 60, low: 10, options: {}, tags: [] },
	{ sku: 'CY-HDMI-1M', group: 'hdmi-cable', name: 'HDMI cable — 1m', price: 34900, hsn: '8544', gst: 1800, stock: 30, low: 6, options: {}, tags: [] },
	{ sku: 'CY-CASE-BLK', group: 'phone-case', name: 'Phone case — black', price: 29900, hsn: '3926', gst: 1800, stock: 22, low: 5, options: { colour: 'black' }, tags: [] },
	{ sku: 'CY-CASE-CLEAR', group: 'phone-case', name: 'Phone case — clear', price: 27900, hsn: '3926', gst: 1800, stock: 0, low: 5, options: { colour: 'clear' }, tags: [] },
	{ sku: 'CY-SCREENGUARD', group: 'screen-protector', name: 'Screen protector', price: 14900, hsn: '', gst: 0, stock: 80, low: 10, options: {}, tags: ['addon'] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const CIRCUITYARD: Profile = {
	shop: 'CircuitYard',
	domain: 'circuityard.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'CircuitYard Electronics',
		gstin: '27AABCC1234M1ZQ',
		state: 'MH',
		taxInclusive: true,
		invoicePrefix: 'CY'
	},
	zones: [
		{ id: 'maharashtra', label: 'Maharashtra', states: ['MH'], cost: 4900, eta: 2 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 12900, eta: 5 }
	],
	unserviceable: ['79'],
	codes: [
		{ code: 'CIRCUIT10', label: 'Launch discount', amount: 10000, visibility: 'public', maxUses: 50 },
		{ code: 'CYPRIVATE-M4R7-9XQF', label: 'Private code', amount: 25000, visibility: 'private', maxUses: 1 }
	]
};
