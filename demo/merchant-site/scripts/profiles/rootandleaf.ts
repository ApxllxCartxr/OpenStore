/**
 * Root & Leaf: potted plants and garden supplies, home state Kerala.
 *
 * No cross-shop overlap here — every shop having one would stop meaning
 * anything. Its own story is fragile stock: a live plant's "in-stock" is a
 * narrower promise than a mug's, and the low-stock threshold reflects that.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['money-plant', 'Money plant, potted', {}],
	['snake-plant', 'Snake plant, potted', {}],
	['terracotta-pot', 'Terracotta pot', { size: ['S', 'M', 'L'] }],
	['potting-soil', 'Potting soil, 5kg bag', {}],
	['mister', 'Plant mister', {}],
	['ceramic-pot', 'Ceramic pot', {}],
	['fertilizer-sticks', 'Fertilizer sticks, 20-pack', {}],
	['garden-gloves', 'Garden gloves', {}]
];

const ITEMS: Row[] = [
	{ sku: 'RL-MONEYPLANT', group: 'money-plant', name: 'Money plant, potted', price: 39900, hsn: '0602', gst: 500, stock: 12, low: 4, options: {}, tags: [] },
	{ sku: 'RL-SNAKEPLANT', group: 'snake-plant', name: 'Snake plant, potted', price: 49900, hsn: '0602', gst: 500, stock: 2, low: 4, options: {}, tags: [] },
	{ sku: 'RL-POT-S', group: 'terracotta-pot', name: 'Terracotta pot — S', price: 19900, hsn: '6914', gst: 1200, stock: 30, low: 6, options: { size: 'S' }, tags: [] },
	{ sku: 'RL-POT-M', group: 'terracotta-pot', name: 'Terracotta pot — M', price: 29900, hsn: '6914', gst: 1200, stock: 25, low: 6, options: { size: 'M' }, tags: [] },
	{ sku: 'RL-POT-L', group: 'terracotta-pot', name: 'Terracotta pot — L', price: 44900, hsn: '6914', gst: 1200, stock: 10, low: 4, options: { size: 'L' }, tags: [] },
	{ sku: 'RL-SOIL-5KG', group: 'potting-soil', name: 'Potting soil, 5kg bag', price: 24900, hsn: '3101', gst: 500, stock: 40, low: 8, options: {}, tags: [] },
	{ sku: 'RL-MISTER', group: 'mister', name: 'Plant mister', price: 17900, hsn: '8424', gst: 1800, stock: 22, low: 5, options: {}, tags: [] },
	{ sku: 'RL-CERAMICPOT', group: 'ceramic-pot', name: 'Ceramic pot, glazed', price: 69900, hsn: '6913', gst: 1200, stock: 15, low: 4, options: {}, tags: [] },
	{ sku: 'RL-GLOVES', group: 'garden-gloves', name: 'Garden gloves', price: 14900, hsn: '4015', gst: 1200, stock: 26, low: 5, options: {}, tags: [] },
	{ sku: 'RL-FERTSTICKS-20', group: 'fertilizer-sticks', name: 'Fertilizer sticks, 20-pack', price: 9900, hsn: '', gst: 0, stock: 60, low: 10, options: {}, tags: ['addon'] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const ROOTANDLEAF: Profile = {
	shop: 'Root & Leaf',
	domain: 'rootandleaf.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'Root & Leaf Nursery',
		gstin: '32AABCR1357S1ZV',
		state: 'KL',
		taxInclusive: true,
		invoicePrefix: 'RL'
	},
	zones: [
		{ id: 'kerala', label: 'Kerala', states: ['KL'], cost: 4900, eta: 2 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 14900, eta: 6 }
	],
	// A live plant does not survive the islands or the northeast by surface post.
	unserviceable: ['74', '79'],
	codes: [
		{ code: 'GREEN10', label: 'New-planter discount', amount: 10000, visibility: 'public', maxUses: 40 },
		{ code: 'RLPRIVATE-4KQX-7BDT', label: 'Private code', amount: 20000, visibility: 'private', maxUses: 1 }
	]
};
