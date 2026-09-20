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
import { randomBytes, scryptSync } from 'node:crypto';
import { migrate, sql } from '../src/lib/db.ts';

type Row = {
	sku: string;
	group: string;
	name: string;
	price: number;
	hsn: string;
	gst: number;
	stock: number;
	low: number;
	options: Record<string, string>;
	tags: string[];
};

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

export class SeedError extends Error {}

/**
 * Catalogue validation, run before anything is written.
 *
 * Fails **loud and named**: a seeder that accepts a missing GST rate produces a
 * catalogue whose first agent order cannot be invoiced, and the error surfaces
 * hours later as a quote refusal nobody can trace back here.
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
	for (const row of rows) {
		if (!Number.isInteger(row.stock) || row.stock < 0) {
			throw new SeedError(`${row.sku}: stock is int >= 0, never ${row.stock}`);
		}
		if (!Number.isInteger(row.price) || row.price < 0) {
			throw new SeedError(`${row.sku}: price_minor is paise, an int >= 0, never ${row.price}`);
		}
		const isAddon = row.tags.includes('addon');
		if (!isAddon && !row.hsn) {
			throw new SeedError(`${row.sku}: no HSN/SAC. A line with no HSN cannot be invoiced.`);
		}
		if (!isAddon && row.gst <= 0) {
			throw new SeedError(`${row.sku}: no GST rate. An Add-on inherits its parent's; this is not one.`);
		}
	}
}

/** §16.6's private code is high-entropy on purpose: seeding is not a licence
 * to seed `TEST1`, and B3's entropy rule applies to the seed as much as to the
 * console. */
function assertCodeEntropy(code: string): void {
	const distinct = new Set(code.replace(/-/g, '')).size;
	if (code.length < 12 || distinct < 8) {
		throw new SeedError(
			`private code ${code} is guessable. Seeding is not a licence to seed TEST1.`
		);
	}
}

export async function seed(): Promise<void> {
	validate(ITEMS);
	assertCodeEntropy('DUCK-7F3K-9QWX');
	await migrate();

	await sql`TRUNCATE code_reservations, stock_moves, stock, catalogue_items, product_groups,
	          shipping_zones, unserviceable_postal_ranges, discount_codes, merchant_tax_identity,
	          notifications, admin_users, idempotency RESTART IDENTITY CASCADE`;

	for (const [id, name, axes] of GROUPS) {
		await sql`INSERT INTO product_groups (id, slug, name, description, option_axes)
		          VALUES (${id}, ${id}, ${name}, ${`${name} by SpoiledDuckie.`}, ${sql.json(axes)})`;
	}

	for (const row of ITEMS) {
		await sql`INSERT INTO catalogue_items
		          (sku, group_id, options, name, price_minor, tags, low_stock_threshold, hsn_sac, gst_rate_bp)
		          VALUES (${row.sku}, ${row.group}, ${sql.json(row.options)}, ${row.name},
		                  ${row.price}, ${sql.json(row.tags)}, ${row.low}, ${row.hsn}, ${row.gst})`;
		await sql`INSERT INTO stock (sku, available) VALUES (${row.sku}, ${row.stock})`;
	}

	// §16.2. Fabricated, correct in shape, belonging to nobody — never presented
	// as real.
	await sql`INSERT INTO merchant_tax_identity (id, legal_name, gstin, registered_state, tax_inclusive, invoice_prefix)
	          VALUES (1, 'SpoiledDuckie Accessories', '29AABCS1429B1ZQ', 'KA', true, 'SD')`;

	// §16.5. Both GST splits are exercised because one zone is intra-state and
	// the other is not.
	await sql`INSERT INTO shipping_zones (id, label, states, cost_minor, eta_days) VALUES
	          ('karnataka', 'Karnataka', ${sql.json(['KA'])}, 4900, 2),
	          ('rest-of-india', 'Rest of India', ${sql.json([])}, 9900, 5)`;
	await sql`INSERT INTO unserviceable_postal_ranges (prefix) VALUES ('19')`;

	// §16.6.
	await sql`INSERT INTO discount_codes (code, label, amount_minor, visibility, max_uses) VALUES
	          ('SPOILED10', 'Launch code', 10000, 'public', 50),
	          ('DUCK-7F3K-9QWX', 'Private code', 25000, 'private', 1)`;

	// scrypt from the standard library rather than a native argon2 build: it is
	// memory-hard, it needs no build script, and one fewer native dependency is
	// one fewer thing that fails on somebody else's machine.
	const password = process.env.ADMIN_SEED_PASSWORD ?? randomBytes(12).toString('base64url');
	const salt = randomBytes(16);
	const hash = scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 });
	await sql`INSERT INTO admin_users (email, password_hash)
	          VALUES ('operator@spoiledduckie.test', ${`scrypt$${salt.toString('hex')}$${hash.toString('hex')}`})`;

	console.log(`Seeded 12 groups, ${ITEMS.length} items, 2 zones, 2 codes.`);
	console.log(`Admin: operator@spoiledduckie.test / ${password}`);
	console.log('SD-TOTE-BLK-L is absent on purpose — the picker renders it unavailable.');
}

if (import.meta.url === `file://${process.argv[1]}`) {
	await seed();
	await sql.end();
}

export { ITEMS, GROUPS };
