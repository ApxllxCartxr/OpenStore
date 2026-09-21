/**
 * PantryLine: packaged groceries and pantry staples, home state Gujarat.
 *
 * `PL-COFFEE-250` mirrors Kettle & Grain's `KG-COFFEE-250` — the same filter
 * coffee powder, sold by a grocer and by a kitchenware shop, at two prices.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['coffee', 'Filter coffee powder, 250g', {}],
	['chai', 'Masala chai blend, 200g', {}],
	['almonds', 'Roasted almonds, 250g', {}],
	['rice', 'Basmati rice, 1kg', {}],
	['oil', 'Cold-pressed groundnut oil, 1L', {}],
	['papad', 'Papad pack', {}],
	['pickle', 'Mango pickle, 400g', {}],
	['honey', 'Raw honey, 500g', {}]
];

const ITEMS: Row[] = [
	// The overlap: see file header.
	{ sku: 'PL-COFFEE-250', group: 'coffee', name: 'Filter coffee powder, 250g', price: 24900, hsn: '0901', gst: 500, stock: 40, low: 8, options: {}, tags: [] },
	{ sku: 'PL-CHAI-200', group: 'chai', name: 'Masala chai blend, 200g', price: 17900, hsn: '0902', gst: 500, stock: 35, low: 8, options: {}, tags: [] },
	{ sku: 'PL-ALMONDS-250', group: 'almonds', name: 'Roasted almonds, 250g', price: 34900, hsn: '2008', gst: 1200, stock: 3, low: 5, options: {}, tags: [] },
	{ sku: 'PL-RICE-1KG', group: 'rice', name: 'Basmati rice, 1kg', price: 22900, hsn: '1006', gst: 500, stock: 50, low: 10, options: {}, tags: [] },
	{ sku: 'PL-OIL-1L', group: 'oil', name: 'Cold-pressed groundnut oil, 1L', price: 29900, hsn: '1508', gst: 500, stock: 28, low: 6, options: {}, tags: [] },
	{ sku: 'PL-PAPAD', group: 'papad', name: 'Papad pack', price: 9900, hsn: '1905', gst: 1200, stock: 60, low: 10, options: {}, tags: [] },
	{ sku: 'PL-PICKLE-400', group: 'pickle', name: 'Mango pickle, 400g', price: 19900, hsn: '2001', gst: 1200, stock: 22, low: 5, options: {}, tags: [] },
	{ sku: 'PL-HONEY-500', group: 'honey', name: 'Raw honey, 500g', price: 39900, hsn: '0409', gst: 500, stock: 0, low: 4, options: {}, tags: [] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const PANTRYLINE: Profile = {
	shop: 'PantryLine',
	domain: 'pantryline.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'PantryLine Foods',
		gstin: '24AABCP4321L1ZS',
		state: 'GJ',
		taxInclusive: true,
		invoicePrefix: 'PL'
	},
	zones: [
		{ id: 'gujarat', label: 'Gujarat', states: ['GJ'], cost: 3900, eta: 2 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 11900, eta: 5 }
	],
	unserviceable: ['79'],
	codes: [
		{ code: 'PANTRY10', label: 'Pantry restock discount', amount: 10000, visibility: 'public', maxUses: 60 },
		{ code: 'PLPRIVATE-7QJ3-XV2M', label: 'Private code', amount: 20000, visibility: 'private', maxUses: 1 }
	]
};
