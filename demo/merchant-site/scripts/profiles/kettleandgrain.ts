/**
 * Kettle & Grain: kitchenware and coffee equipment, home state Rajasthan.
 *
 * `KG-COFFEE-250` mirrors PantryLine's `PL-COFFEE-250` — see that file's
 * header. Different shop, different price, same product.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['coffee', 'Filter coffee powder, 250g', {}],
	['kettle', 'Stovetop kettle', {}],
	['mug-set', 'Ceramic mug set', {}],
	['french-press', 'French press', {}],
	['tea-infuser', 'Tea infuser', {}],
	['coaster', 'Wooden coaster set', {}],
	['trivet', 'Trivet', {}],
	['scoop', 'Coffee scoop', {}]
];

const ITEMS: Row[] = [
	// The overlap: see file header.
	{ sku: 'KG-COFFEE-250', group: 'coffee', name: 'Filter coffee powder, 250g', price: 27900, hsn: '0901', gst: 500, stock: 30, low: 6, options: {}, tags: [] },
	{ sku: 'KG-KETTLE', group: 'kettle', name: 'Stovetop kettle, 1.5L', price: 89900, hsn: '7323', gst: 1200, stock: 14, low: 3, options: {}, tags: [] },
	{ sku: 'KG-MUGSET-4', group: 'mug-set', name: 'Ceramic mug set, 4-piece', price: 119900, hsn: '6912', gst: 1200, stock: 10, low: 3, options: {}, tags: [] },
	{ sku: 'KG-FRENCHPRESS', group: 'french-press', name: 'French press, 600ml', price: 149900, hsn: '7013', gst: 1200, stock: 2, low: 3, options: {}, tags: [] },
	{ sku: 'KG-TEAINFUSER', group: 'tea-infuser', name: 'Tea infuser', price: 34900, hsn: '7323', gst: 1200, stock: 25, low: 5, options: {}, tags: [] },
	{ sku: 'KG-COASTER-4', group: 'coaster', name: 'Wooden coaster set, 4-piece', price: 44900, hsn: '4419', gst: 1200, stock: 20, low: 4, options: {}, tags: [] },
	{ sku: 'KG-TRIVET', group: 'trivet', name: 'Trivet', price: 24900, hsn: '4419', gst: 1200, stock: 18, low: 4, options: {}, tags: [] },
	{ sku: 'KG-SCOOP', group: 'scoop', name: 'Coffee scoop', price: 7900, hsn: '', gst: 0, stock: 70, low: 10, options: {}, tags: ['addon'] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const KETTLEANDGRAIN: Profile = {
	shop: 'Kettle & Grain',
	domain: 'kettleandgrain.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'Kettle & Grain Kitchenware',
		gstin: '08AABCK8765P1ZT',
		state: 'RJ',
		taxInclusive: true,
		invoicePrefix: 'KG'
	},
	zones: [
		{ id: 'rajasthan', label: 'Rajasthan', states: ['RJ'], cost: 4900, eta: 3 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 12900, eta: 6 }
	],
	unserviceable: ['79'],
	codes: [
		{ code: 'GRAIN10', label: 'New-kitchen discount', amount: 10000, visibility: 'public', maxUses: 40 },
		{ code: 'KGPRIVATE-5RTB-8ZWQ', label: 'Private code', amount: 25000, visibility: 'private', maxUses: 1 }
	]
};
