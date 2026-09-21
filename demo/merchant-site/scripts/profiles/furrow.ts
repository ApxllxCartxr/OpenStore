/**
 * Furrow: pet supplies, home state Uttar Pradesh — the tenth shop.
 *
 * `FW-TENNISBALL3` mirrors Playspool's `PS-TENNISBALL3` — see that file's
 * header.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['dogfood', 'Dog food, 3kg bag', {}],
	['catlitter', 'Cat litter, 5kg bag', {}],
	['leash', 'Pet leash', {}],
	['tennisball', 'Tennis ball, 3-pack', {}],
	['petbed', 'Pet bed', { size: ['S', 'M'] }],
	['brush', 'Grooming brush', {}],
	['collar', 'Collar', {}],
	['treatpouch', 'Treat pouch', {}]
];

const ITEMS: Row[] = [
	{ sku: 'FW-DOGFOOD-3KG', group: 'dogfood', name: 'Dog food, 3kg bag', price: 79900, hsn: '2309', gst: 1800, stock: 22, low: 5, options: {}, tags: [] },
	{ sku: 'FW-CATLITTER-5KG', group: 'catlitter', name: 'Cat litter, 5kg bag', price: 49900, hsn: '2530', gst: 1200, stock: 18, low: 4, options: {}, tags: [] },
	{ sku: 'FW-LEASH', group: 'leash', name: 'Pet leash', price: 34900, hsn: '4201', gst: 1200, stock: 26, low: 5, options: {}, tags: [] },
	// The overlap: see file header.
	{ sku: 'FW-TENNISBALL3', group: 'tennisball', name: 'Tennis ball, 3-pack', price: 17900, hsn: '9506', gst: 1200, stock: 35, low: 8, options: {}, tags: [] },
	{ sku: 'FW-PETBED-S', group: 'petbed', name: 'Pet bed — S', price: 89900, hsn: '4201', gst: 1200, stock: 10, low: 3, options: { size: 'S' }, tags: [] },
	{ sku: 'FW-PETBED-M', group: 'petbed', name: 'Pet bed — M', price: 119900, hsn: '4201', gst: 1200, stock: 1, low: 3, options: { size: 'M' }, tags: [] },
	{ sku: 'FW-BRUSH', group: 'brush', name: 'Grooming brush', price: 24900, hsn: '9603', gst: 1800, stock: 30, low: 6, options: {}, tags: [] },
	{ sku: 'FW-COLLAR', group: 'collar', name: 'Collar', price: 29900, hsn: '4201', gst: 1200, stock: 24, low: 5, options: {}, tags: [] },
	{ sku: 'FW-TREATPOUCH', group: 'treatpouch', name: 'Treat pouch', price: 9900, hsn: '', gst: 0, stock: 70, low: 10, options: {}, tags: ['addon'] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const FURROW: Profile = {
	shop: 'Furrow',
	domain: 'furrow.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'Furrow Pet Supplies',
		gstin: '09AABCF8642U1ZX',
		state: 'UP',
		taxInclusive: true,
		invoicePrefix: 'FW'
	},
	zones: [
		{ id: 'uttar-pradesh', label: 'Uttar Pradesh', states: ['UP'], cost: 4900, eta: 3 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 13900, eta: 6 }
	],
	unserviceable: ['79'],
	codes: [
		{ code: 'PAWS10', label: 'New-pet discount', amount: 10000, visibility: 'public', maxUses: 50 },
		{ code: 'FWPRIVATE-3JXM-9CTP', label: 'Private code', amount: 20000, visibility: 'private', maxUses: 1 }
	]
};
