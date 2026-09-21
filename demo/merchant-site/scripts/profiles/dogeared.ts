/**
 * Dog-Eared: the second shop, and the reason the first one's seed was split.
 *
 * It sells printed books, which is not a cosmetic change of commodity:
 *
 * - **Printed books are nil-rated.** HSN 4901 at 0%, and every line of it
 *   carries a tax line of zero rather than no tax line at all. The seed
 *   validator used to refuse any non-add-on at 0%, so this catalogue could not
 *   have been seeded at all.
 * - **Its home state is West Bengal.** SpoiledDuckie is Karnataka, so the same
 *   Consumer address that produces CGST/SGST at one shop produces IGST at the
 *   other — the split is a property of the pair, not of the buyer.
 * - **Two rates and a service in one basket.** Books at 0%, stationery at 12%,
 *   bookmarks at 18%, and an inscription performed on the premises whose Place
 *   of Supply is the shop's own state. One order exercises every branch of
 *   §16.11's tax step.
 *
 * Nothing here is a real business. The GSTIN is fabricated and correct only in
 * shape.
 */
import type { Profile, Row } from '../seed-core.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['tidepool', 'The Tidepool Almanac', { edition: ['paperback', 'hardback'] }],
	['saltroad', 'Salt Road', { condition: ['new', 'used'] }],
	['fieldnotes', 'Field Notes on Nothing', {}],
	['kitchenyear', 'A Kitchen Year', {}],
	['pocketpoets', 'Pocket Poets box', {}],
	['notebook', 'Sewn notebook', {}],
	['bookmarks', 'Brass bookmark', {}],
	['inscription', 'Hand inscription', {}],
	['wrap', 'Paper wrap', {}],
	['withdrawn', 'Withdrawn title', {}]
];

/**
 * Eleven items across ten groups.
 *
 * `DE-TIDEPOOL-HB` is the low-stock one and `DE-SALTROAD-USED` the scarce one:
 * a shop where everything is plentiful never renders a low-stock badge, and a
 * demo that cannot show one is not showing the catalogue honestly.
 */
const ITEMS: Row[] = [
	{ sku: 'DE-TIDEPOOL-PB', group: 'tidepool', name: 'The Tidepool Almanac — paperback', price: 49900, hsn: '4901', gst: 0, nilRated: true, stock: 14, low: 3, options: { edition: 'paperback' }, tags: [] },
	{ sku: 'DE-TIDEPOOL-HB', group: 'tidepool', name: 'The Tidepool Almanac — hardback', price: 89900, hsn: '4901', gst: 0, nilRated: true, stock: 2, low: 3, options: { edition: 'hardback' }, tags: [] },
	{ sku: 'DE-SALTROAD-NEW', group: 'saltroad', name: 'Salt Road — new', price: 59900, hsn: '4901', gst: 0, nilRated: true, stock: 8, low: 3, options: { condition: 'new' }, tags: [] },
	{ sku: 'DE-SALTROAD-USED', group: 'saltroad', name: 'Salt Road — used, good', price: 29900, hsn: '4901', gst: 0, nilRated: true, stock: 1, low: 2, options: { condition: 'used' }, tags: [] },
	{ sku: 'DE-FIELDNOTES', group: 'fieldnotes', name: 'Field Notes on Nothing', price: 39900, hsn: '4901', gst: 0, nilRated: true, stock: 20, low: 4, options: {}, tags: [] },
	{ sku: 'DE-KITCHENYEAR', group: 'kitchenyear', name: 'A Kitchen Year', price: 129900, hsn: '4901', gst: 0, nilRated: true, stock: 6, low: 2, options: {}, tags: ['limited'] },
	{ sku: 'DE-POCKETPOETS', group: 'pocketpoets', name: 'Pocket Poets box', price: 149900, hsn: '4901', gst: 0, nilRated: true, stock: 5, low: 2, options: {}, tags: [] },
	// Stationery is not a book: 12% on notebooks, 18% on the brass bookmark.
	{ sku: 'DE-NOTEBOOK', group: 'notebook', name: 'Sewn notebook', price: 34900, hsn: '4820', gst: 1200, stock: 30, low: 5, options: {}, tags: [] },
	{ sku: 'DE-BOOKMARK', group: 'bookmarks', name: 'Brass bookmark', price: 24900, hsn: '8305', gst: 1800, stock: 22, low: 5, options: {}, tags: [] },
	// Performed at the counter: its Place of Supply is the shop's own state
	// even when the books ship elsewhere.
	{ sku: 'DE-INSCRIPTION', group: 'inscription', name: 'Hand inscription', price: 19900, hsn: '998391', gst: 1800, stock: 25, low: 5, options: {}, tags: ['service'] },
	{ sku: 'DE-WRAP', group: 'wrap', name: 'Paper wrap', price: 6900, hsn: '', gst: 0, stock: 100, low: 10, options: {}, tags: ['addon'] },
	{ sku: 'DE-WITHDRAWN', group: 'withdrawn', name: 'Withdrawn title', price: 44900, hsn: '4901', gst: 0, nilRated: true, stock: 3, low: 2, options: {}, tags: ['recalled'] }
];

/** Placeholder tiles, not photographs — `pnpm placeholders` writes them and
 *  says so on the tile itself. A shop that renders empty frames beside one
 *  with photographs reads as broken rather than as unphotographed. */
const MEDIA: Record<string, string[]> = Object.fromEntries(
	ITEMS.map((item) => [item.sku, [`/media/${item.sku.toLowerCase()}.svg`]])
);

export const DOGEARED: Profile = {
	shop: 'Dog-Eared',
	domain: 'dogeared.localhost',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		legalName: 'Dog-Eared Books & Paper',
		// Fabricated. 19 is West Bengal's state code, which is what makes the
		// CGST/SGST split land differently here than at a Karnataka shop.
		gstin: '19AAFCD5862R1ZP',
		state: 'WB',
		taxInclusive: true,
		invoicePrefix: 'DE'
	},
	zones: [
		{ id: 'west-bengal', label: 'West Bengal', states: ['WB'], cost: 3900, eta: 2 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 11900, eta: 6 }
	],
	// Books go by surface post; the shop does not serve the islands.
	unserviceable: ['74'],
	codes: [
		{ code: 'SHELF15', label: 'Shelf sale', amount: 15000, visibility: 'public', maxUses: 40 },
		{ code: 'FOXED-8QM2-5KTW', label: 'Private code', amount: 30000, visibility: 'private', maxUses: 1 }
	],
	check(rows) {
		const nil = rows.filter((row) => row.nilRated);
		if (nil.length < 5) {
			throw new Error(
				`a bookshop whose catalogue is not mostly nil-rated is not exercising the ` +
					`0% path this shop exists to cover; got ${nil.length}`
			);
		}
	}
};
