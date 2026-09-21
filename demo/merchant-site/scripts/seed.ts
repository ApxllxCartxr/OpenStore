/**
 * The seed, built from §16.3's authoritative table.
 *
 * **12 Product Groups → exactly 15 Catalogue Items.** Counted, not estimated:
 * the table has 16 rows and `SD-TOTE-BLK-L` is marked DO NOT SEED. Black/L was
 * never made, and it is **absent** rather than zero-stocked — seeding it breaks
 * two gates at once ("zero null-stock rows" and "the unavailable combination
 * renders as unavailable").
 *
 * Rows inserted directly are fine only for catalogue, pricing and stock, which
 * are Merchant truth by definition. Any seeded order that must be visible on
 * **both** sides is created by driving the real flow over HTTP
 * (`scripts/seed_demo.py`), because a row inserted here has no Transcript, no
 * Ledger, no receipt and no attribution — it would demo a hollow order whose
 * "empty Ledger" is vacuously true for the wrong reason.
 */
import { sql } from '../src/lib/db.ts';
import { SeedError, validateRows, writeProfile, type Profile, type Row } from './seed-core.ts';
import { DOGEARED } from './profiles/dogeared.ts';

const GROUPS: [string, string, Record<string, string[]>][] = [
	['tote', 'Tote', { colour: ['black', 'red'], size: ['M', 'L'] }],
	['cap', 'Cap', { size: ['S', 'M'] }],
	['stickers', 'Sticker pack', {}],
	['keychain', 'Keychain', {}],
	['hairclips', 'Hair clips', {}],
	['phonecharm', 'Phone charm', {}],
	['pinset', 'Pin set', {}],
	['plush', 'Plush mini', {}],
	['charmbar', 'Charm-bar seat', {}],
	['giftwrap', 'Gift-wrap', {}],
	['extracharm', 'Extra charm', {}],
	['recalled', 'Recalled item', {}]
];

/**
 * Product photography, served from `static/media/` (B1).
 *
 * `media` has been a column on both catalogue tables since the schema was
 * written, door 1 carries it, and `core/feed.py` emits it as `image_link` —
 * every layer was ready and the seed put nothing in it, so the shop, the feed
 * and every agent saw a catalogue with no pictures.
 *
 * A group shows its first item's photograph. One row per SKU, because
 * variants are what a buyer is actually choosing between.
 */
const MEDIA: Record<string, string[]> = {
 'SD-TOTE-BLK-M': [
  '/media/sd-tote-blk-m-1.webp',
  '/media/sd-tote-blk-m-2.webp',
  '/media/sd-tote-blk-m-3.webp'
 ],
 'SD-TOTE-RED-M': [
  '/media/sd-tote-red-m-1.webp',
  '/media/sd-tote-red-m-2.webp',
  '/media/sd-tote-red-m-3.webp'
 ],
 'SD-TOTE-RED-L': [
  '/media/sd-tote-red-l-1.webp',
  '/media/sd-tote-red-l-2.webp',
  '/media/sd-tote-red-l-3.webp'
 ],
 'SD-CAP-S': [
  '/media/sd-cap-s-1.webp',
  '/media/sd-cap-s-2.webp',
  '/media/sd-cap-s-3.webp'
 ],
 'SD-CAP-M': [
  '/media/sd-cap-m-1.webp',
  '/media/sd-cap-m-2.webp',
  '/media/sd-cap-m-3.webp'
 ],
 'SD-STICKERS': [
  '/media/sd-stickers-1.webp',
  '/media/sd-stickers-2.webp',
  '/media/sd-stickers-3.webp'
 ],
 'SD-KEYCHAIN': [
  '/media/sd-keychain-1.webp'
 ],
 'SD-HAIRCLIPS': [
  '/media/sd-hairclips-1.webp'
 ],
 'SD-PHONECHARM': [
  '/media/sd-phonecharm-1.webp',
  '/media/sd-phonecharm-2.webp',
  '/media/sd-phonecharm-3.webp'
 ],
 'SD-PINSET': [
  '/media/sd-pinset-1.webp',
  '/media/sd-pinset-2.webp',
  '/media/sd-pinset-3.webp'
 ],
 'SD-PLUSH-MINI': [
  '/media/sd-plush-mini-1.webp'
 ],
 'SD-CHARMBAR-SEAT': [
  '/media/sd-charmbar-seat-1.webp',
  '/media/sd-charmbar-seat-2.webp'
 ],
 'SD-GIFTWRAP': [
  '/media/sd-giftwrap-1.webp',
  '/media/sd-giftwrap-2.webp',
  '/media/sd-giftwrap-3.webp'
 ],
 'SD-EXTRACHARM': [
  '/media/sd-extracharm-1.webp'
 ],
 'SD-RECALLED': [
  '/media/sd-recalled-1.webp',
  '/media/sd-recalled-2.webp'
 ]
};

/** §16.3, verbatim. Fifteen rows; black/L is deliberately not among them. */
const ITEMS: Row[] = [
	{ sku: 'SD-TOTE-BLK-M', group: 'tote', name: 'Tote — black / M', price: 89900, hsn: '4202', gst: 1800, stock: 12, low: 3, options: { colour: 'black', size: 'M' }, tags: [] },
	{ sku: 'SD-TOTE-RED-M', group: 'tote', name: 'Tote — red / M', price: 89900, hsn: '4202', gst: 1800, stock: 7, low: 3, options: { colour: 'red', size: 'M' }, tags: [] },
	{ sku: 'SD-TOTE-RED-L', group: 'tote', name: 'Tote — red / L', price: 99900, hsn: '4202', gst: 1800, stock: 4, low: 3, options: { colour: 'red', size: 'L' }, tags: [] },
	{ sku: 'SD-CAP-S', group: 'cap', name: 'Cap — S', price: 64900, hsn: '6505', gst: 1200, stock: 9, low: 3, options: { size: 'S' }, tags: [] },
	{ sku: 'SD-CAP-M', group: 'cap', name: 'Cap — M', price: 64900, hsn: '6505', gst: 1200, stock: 2, low: 3, options: { size: 'M' }, tags: [] },
	{ sku: 'SD-STICKERS', group: 'stickers', name: 'Sticker pack', price: 19900, hsn: '4911', gst: 1800, stock: 40, low: 5, options: {}, tags: [] },
	{ sku: 'SD-KEYCHAIN', group: 'keychain', name: 'Keychain', price: 29900, hsn: '8308', gst: 1800, stock: 25, low: 5, options: {}, tags: [] },
	{ sku: 'SD-HAIRCLIPS', group: 'hairclips', name: 'Hair clips', price: 34900, hsn: '9615', gst: 1800, stock: 18, low: 3, options: {}, tags: [] },
	{ sku: 'SD-PHONECHARM', group: 'phonecharm', name: 'Phone charm', price: 44900, hsn: '7117', gst: 300, stock: 15, low: 3, options: {}, tags: [] },
	{ sku: 'SD-PINSET', group: 'pinset', name: 'Pin set', price: 39900, hsn: '7117', gst: 300, stock: 11, low: 3, options: {}, tags: [] },
	{ sku: 'SD-PLUSH-MINI', group: 'plush', name: 'Plush mini', price: 129900, hsn: '9503', gst: 1200, stock: 10, low: 2, options: {}, tags: ['limited'] },
	{ sku: 'SD-CHARMBAR-SEAT', group: 'charmbar', name: 'Charm-bar seat', price: 150000, hsn: '999799', gst: 1800, stock: 10, low: 2, options: {}, tags: ['service'] },
	{ sku: 'SD-GIFTWRAP', group: 'giftwrap', name: 'Gift-wrap', price: 9900, hsn: '', gst: 0, stock: 100, low: 10, options: {}, tags: ['addon'] },
	{ sku: 'SD-EXTRACHARM', group: 'extracharm', name: 'Extra charm', price: 14900, hsn: '', gst: 0, stock: 60, low: 10, options: {}, tags: ['addon'] },
	{ sku: 'SD-RECALLED', group: 'recalled', name: 'Recalled item', price: 49900, hsn: '4202', gst: 1800, stock: 5, low: 3, options: {}, tags: ['recalled'] }
];

/**
 * SpoiledDuckie's own checks, on top of the row checks every shop gets.
 *
 * Fails **loud and named**: a seeder that accepts a missing GST rate produces
 * a catalogue whose first agent order cannot be invoiced.
 */
export function validate(rows: Row[]): void {
	if (rows.length !== 15) {
		throw new SeedError(`§16.3 seeds exactly 15 Catalogue Items; got ${rows.length}`);
	}
	if (rows.some((r) => r.sku === 'SD-TOTE-BLK-L')) {
		throw new SeedError(
			'SD-TOTE-BLK-L must not be seeded — not with zero stock, not archived. ' +
				'Black/L was never made, and the picker has to render it as unavailable.'
		);
	}
	// Nothing SpoiledDuckie sells is nil-rated, so a 0% row here is a rate
	// somebody forgot rather than a book.
	for (const row of rows) {
		if (!row.tags.includes('addon') && row.gst <= 0) {
			throw new SeedError(`${row.sku}: no GST rate. An Add-on inherits its parent's; this is not one.`);
		}
	}
	validateRows(rows);
}

/** SpoiledDuckie as a profile: §16.3's table, this shop's tax identity, zones
 *  and codes. Everything generic about writing it lives in `seed-core.ts`, so
 *  a second shop is data rather than a second seeder. */
export const SPOILEDDUCKIE: Profile = {
	shop: 'SpoiledDuckie',
	domain: 'spoiledduckie.test',
	groups: GROUPS,
	items: ITEMS,
	media: MEDIA,
	tax: {
		// §16.2. Fabricated, correct in shape, belonging to nobody.
		legalName: 'SpoiledDuckie Accessories',
		gstin: '29AABCS1429B1ZQ',
		state: 'KA',
		taxInclusive: true,
		invoicePrefix: 'SD'
	},
	// §16.5. Both GST splits are exercised because one zone is intra-state and
	// the other is not.
	zones: [
		{ id: 'karnataka', label: 'Karnataka', states: ['KA'], cost: 4900, eta: 2 },
		{ id: 'rest-of-india', label: 'Rest of India', states: [], cost: 9900, eta: 5 }
	],
	unserviceable: ['19'],
	// §16.6.
	codes: [
		{ code: 'SPOILED10', label: 'Launch code', amount: 10000, visibility: 'public', maxUses: 50 },
		{ code: 'DUCK-7F3K-9QWX', label: 'Private code', amount: 25000, visibility: 'private', maxUses: 1 }
	],
	check: validate
};

/** Which shop this deploy is. One database per shop, so seeding the wrong
 *  profile into the wrong database is a truncate away from a shop selling
 *  somebody else's catalogue — the name is required rather than guessed when
 *  it is anything but the default. */
const PROFILES: Record<string, Profile> = {
	spoiledduckie: SPOILEDDUCKIE,
	dogeared: DOGEARED
};

export async function seed(): Promise<void> {
	const name = process.env.SEED_PROFILE ?? 'spoiledduckie';
	const profile = PROFILES[name];
	if (!profile) {
		throw new SeedError(`no seed profile ${JSON.stringify(name)}; known: ${Object.keys(PROFILES).join(', ')}`);
	}
	await writeProfile(profile);
	if (profile === SPOILEDDUCKIE) {
		console.log('SD-TOTE-BLK-L is absent on purpose — the picker renders it unavailable.');
	}
}

if (import.meta.url === `file://${process.argv[1]}`) {
	await seed();
	await sql.end();
}

export { ITEMS, GROUPS };

