/**
 * DeskField: stationery and office supplies, home state Delhi.
 *
 * `DF-NOTEBOOK` is the same idea as Dog-Eared's `DE-NOTEBOOK` — a sewn
 * notebook, sold by a bookshop and by a stationer, at two prices. Nothing in
 * Dog-Eared's own file changes; the overlap lives entirely on this side.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['notebook', 'Sewn notebook', {}],
	['fountain-pen', 'Fountain pen', {}],
	['organizer', 'Desk organizer', {}],
	['sticky-notes', 'Sticky notes pack', {}],
	['planner', 'Weekly planner', {}],
	['letter-opener', 'Letter opener', {}],
	['pencil-case', 'Pencil case', {}],
	['washi-tape', 'Washi tape', {}]
];

const ITEMS: Row[] = [
	// The overlap: see file header.
	{ sku: 'DF-NOTEBOOK', group: 'notebook', name: 'Sewn notebook', price: 39900, hsn: '4820', gst: 1200, stock: 35, low: 6, options: {}, tags: [] },
	{ sku: 'DF-FOUNTAINPEN', group: 'fountain-pen', name: 'Fountain pen', price: 149900, hsn: '9608', gst: 1800, stock: 8, low: 3, options: {}, tags: [] },
	{ sku: 'DF-ORGANIZER', group: 'organizer', name: 'Desk organizer', price: 89900, hsn: '3926', gst: 1800, stock: 16, low: 4, options: {}, tags: [] },
	{ sku: 'DF-STICKYNOTES', group: 'sticky-notes', name: 'Sticky notes pack', price: 14900, hsn: '4820', gst: 1200, stock: 50, low: 8, options: {}, tags: [] },
	{ sku: 'DF-PLANNER', group: 'planner', name: 'Weekly planner', price: 59900, hsn: '4820', gst: 1200, stock: 1, low: 4, options: {}, tags: [] },
	{ sku: 'DF-LETTEROPENER', group: 'letter-opener', name: 'Letter opener', price: 34900, hsn: '8214', gst: 1800, stock: 20, low: 4, options: {}, tags: [] },
	{ sku: 'DF-PENCILCASE', group: 'pencil-case', name: 'Pencil case', price: 24900, hsn: '4202', gst: 1800, stock: 28, low: 5, options: {}, tags: [] },
	{ sku: 'DF-WASHITAPE', group: 'washi-tape', name: 'Washi tape', price: 9900, hsn: '', gst: 0, stock: 90, low: 10, options: {}, tags: ['addon'] }
];

const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const DESKFIELD: Profile = {
	shop: 'DeskField',
	domain: 'deskfield.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'DeskField Stationery',
		gstin: '07AABCD2468Q1ZU',
		state: 'DL',
		taxInclusive: true,
		invoicePrefix: 'DF'
	},
	zones: [
		{ id: 'delhi', label: 'Delhi NCR', states: ['DL'], cost: 3900, eta: 1 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 11900, eta: 5 }
	],
	unserviceable: ['79'],
	codes: [
		{ code: 'DESK10', label: 'Back-to-desk discount', amount: 10000, visibility: 'public', maxUses: 50 },
		{ code: 'DFPRIVATE-9NLC-3RMK', label: 'Private code', amount: 20000, visibility: 'private', maxUses: 1 }
	]
};
