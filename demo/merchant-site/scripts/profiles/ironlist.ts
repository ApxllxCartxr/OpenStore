/**
 * IronList: hardware and DIY tools, home state Tamil Nadu.
 *
 * `IL-BATT-AA4` mirrors CircuitYard's `CY-BATT-AA4` — same product, two
 * merchants, two SKUs, two prices and two stock levels. A search for
 * batteries with no shop named should surface both.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['hammer', 'Claw hammer', {}],
	['screwdriver-set', 'Screwdriver set', {}],
	['tape-measure', 'Measuring tape', { length: ['3m', '5m'] }],
	['aa-batteries', 'AA batteries, 4-pack', {}],
	['cable-ties', 'Cable ties, 100-pack', {}],
	['work-gloves', 'Work gloves', { size: ['M', 'L'] }],
	['sandpaper', 'Sandpaper pack', {}],
	['wood-screws', 'Wood screws, 50-pack', {}]
];

const ITEMS: Row[] = [
	{ sku: 'IL-HAMMER-16OZ', group: 'hammer', name: 'Claw hammer, 16oz', price: 44900, hsn: '8205', gst: 1800, stock: 20, low: 4, options: {}, tags: [] },
	{ sku: 'IL-SCREWDRIVER-SET', group: 'screwdriver-set', name: 'Screwdriver set, 6-piece', price: 69900, hsn: '8205', gst: 1800, stock: 15, low: 4, options: {}, tags: [] },
	{ sku: 'IL-TAPE-3M', group: 'tape-measure', name: 'Measuring tape — 3m', price: 24900, hsn: '9017', gst: 1800, stock: 30, low: 6, options: { length: '3m' }, tags: [] },
	{ sku: 'IL-TAPE-5M', group: 'tape-measure', name: 'Measuring tape — 5m', price: 34900, hsn: '9017', gst: 1800, stock: 1, low: 4, options: { length: '5m' }, tags: [] },
	// The overlap: see file header.
	{ sku: 'IL-BATT-AA4', group: 'aa-batteries', name: 'AA batteries, 4-pack', price: 22900, hsn: '8506', gst: 2800, stock: 45, low: 10, options: {}, tags: [] },
	{ sku: 'IL-CABLETIES-100', group: 'cable-ties', name: 'Cable ties, 100-pack', price: 14900, hsn: '3926', gst: 1800, stock: 50, low: 8, options: {}, tags: [] },
	{ sku: 'IL-GLOVES-M', group: 'work-gloves', name: 'Work gloves — M', price: 19900, hsn: '4015', gst: 1200, stock: 25, low: 5, options: { size: 'M' }, tags: [] },
	{ sku: 'IL-GLOVES-L', group: 'work-gloves', name: 'Work gloves — L', price: 19900, hsn: '4015', gst: 1200, stock: 25, low: 5, options: { size: 'L' }, tags: [] },
	{ sku: 'IL-SANDPAPER', group: 'sandpaper', name: 'Sandpaper pack, assorted grit', price: 12900, hsn: '6805', gst: 1800, stock: 40, low: 8, options: {}, tags: [] },
	{ sku: 'IL-WOODSCREWS-50', group: 'wood-screws', name: 'Wood screws, 50-pack', price: 9900, hsn: '', gst: 0, stock: 90, low: 10, options: {}, tags: ['addon'] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const IRONLIST: Profile = {
	shop: 'IronList',
	domain: 'ironlist.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'IronList Hardware',
		gstin: '33AABCI5678N1ZR',
		state: 'TN',
		taxInclusive: true,
		invoicePrefix: 'IL'
	},
	zones: [
		{ id: 'tamil-nadu', label: 'Tamil Nadu', states: ['TN'], cost: 4900, eta: 2 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 13900, eta: 6 }
	],
	unserviceable: ['79'],
	codes: [
		{ code: 'IRONFIRST10', label: 'First-order discount', amount: 10000, visibility: 'public', maxUses: 40 },
		{ code: 'ILPRIVATE-K2W8-4TNH', label: 'Private code', amount: 20000, visibility: 'private', maxUses: 1 }
	]
};
