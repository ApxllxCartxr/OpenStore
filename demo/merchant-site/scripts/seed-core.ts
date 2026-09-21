/**
 * The part of seeding that is not one shop's data.
 *
 * A second shop is the test of whether this site is a template or one shop
 * with a schema: everything here is what any Merchant needs written, and
 * everything a Merchant chooses lives in a profile beside it. No SKU, no rate
 * and no brand string belongs in this file.
 *
 * The validation is deliberately strict and loud. A catalogue that seeds with
 * a missing HSN produces a shop whose first agent order cannot be invoiced,
 * and the failure surfaces hours later as a quote refusal nobody can trace
 * back to the seed.
 */
import { randomBytes, scryptSync } from 'node:crypto';
import { migrate, sql } from '../src/lib/db.ts';

export type Row = {
	sku: string;
	group: string;
	name: string;
	price: number;
	hsn: string;
	/** Basis points. **0 is legal and means nil-rated** — printed books are,
	 *  and a validator that refuses it cannot seed a bookshop. It is spelled
	 *  out per row rather than inferred, so a forgotten rate cannot pass as a
	 *  nil rating. */
	gst: number;
	nilRated?: boolean;
	stock: number;
	low: number;
	options: Record<string, string>;
	tags: string[];
};

export type Zone = { id: string; label: string; states: string[]; cost: number; eta: number };

export type Profile = {
	/** Shown in seeded copy — group descriptions, the admin account. */
	shop: string;
	/** `operator@<this>` is the admin account, and the tax identity's domain. */
	domain: string;
	groups: [string, string, Record<string, string[]>][];
	items: Row[];
	media: Record<string, string[]>;
	tax: {
		legalName: string;
		gstin: string;
		state: string;
		taxInclusive: boolean;
		invoicePrefix: string;
	};
	zones: Zone[];
	/** Postal prefixes the shop does not ship to. */
	unserviceable: string[];
	codes: { code: string; label: string; amount: number; visibility: 'public' | 'private'; maxUses: number }[];
	/** Checks that belong to this shop alone — a pinned item count, a
	 *  combination that must stay absent. Run before anything is written. */
	check?: (rows: Row[]) => void;
};

export class SeedError extends Error {}

/** Every row check that is true of any shop's catalogue. */
export function validateRows(rows: Row[]): void {
	const seen = new Set<string>();
	for (const row of rows) {
		if (seen.has(row.sku)) throw new SeedError(`${row.sku}: seeded twice`);
		seen.add(row.sku);
		if (!Number.isInteger(row.stock) || row.stock < 0) {
			throw new SeedError(`${row.sku}: stock is int >= 0, never ${row.stock}`);
		}
		if (!Number.isInteger(row.price) || row.price < 0) {
			throw new SeedError(`${row.sku}: price_minor is paise, an int >= 0, never ${row.price}`);
		}
		const isAddon = row.tags.includes('addon');
		if (isAddon) continue;
		if (!row.hsn) {
			throw new SeedError(`${row.sku}: no HSN/SAC. A line with no HSN cannot be invoiced.`);
		}
		// Nil-rated is a rate, and it is not the same thing as a rate nobody
		// filled in. Books are 0% and must be sellable; a forgotten rate is a
		// catalogue bug and must not be.
		if (row.gst === 0 && !row.nilRated) {
			throw new SeedError(
				`${row.sku}: 0% with no nilRated flag. Say nilRated: true for genuinely ` +
					`nil-rated goods (HSN 4901 printed books), or give the real rate.`
			);
		}
		if (row.gst < 0) throw new SeedError(`${row.sku}: gst_rate_bp is basis points >= 0`);
		if (row.nilRated && row.gst !== 0) {
			throw new SeedError(`${row.sku}: nilRated but the rate is ${row.gst}`);
		}
	}
}

/** A private code is high-entropy on purpose: seeding is not a licence to seed
 *  `TEST1`, and the console's entropy rule applies here too. */
export function assertCodeEntropy(code: string): void {
	const distinct = new Set(code.replace(/-/g, '')).size;
	if (code.length < 12 || distinct < 8) {
		throw new SeedError(
			`private code ${code} is guessable. Seeding is not a licence to seed TEST1.`
		);
	}
}

export async function writeProfile(profile: Profile): Promise<void> {
	profile.check?.(profile.items);
	validateRows(profile.items);
	for (const code of profile.codes) {
		if (code.visibility === 'private') assertCodeEntropy(code.code);
	}
	await migrate();

	await sql`TRUNCATE code_reservations, stock_moves, stock, catalogue_items, product_groups,
	          shipping_zones, unserviceable_postal_ranges, discount_codes, merchant_tax_identity,
	          notifications, admin_users, idempotency RESTART IDENTITY CASCADE`;

	for (const [id, name, axes] of profile.groups) {
		// A group's photograph is its first item's — the group is presentation
		// only and never a cart line, so it has no photograph of its own.
		const cover = profile.items
			.filter((i) => i.group === id)
			.flatMap((i) => profile.media[i.sku] ?? [])[0];
		await sql`INSERT INTO product_groups (id, slug, name, description, media, option_axes)
		          VALUES (${id}, ${id}, ${name}, ${`${name} by ${profile.shop}.`},
		                  ${sql.json(cover ? [cover] : [])}, ${sql.json(axes)})`;
	}

	for (const row of profile.items) {
		await sql`INSERT INTO catalogue_items
		          (sku, group_id, options, name, price_minor, tags, media, low_stock_threshold, hsn_sac, gst_rate_bp)
		          VALUES (${row.sku}, ${row.group}, ${sql.json(row.options)}, ${row.name},
		                  ${row.price}, ${sql.json(row.tags)}, ${sql.json(profile.media[row.sku] ?? [])},
		                  ${row.low}, ${row.hsn}, ${row.gst})`;
		await sql`INSERT INTO stock (sku, available) VALUES (${row.sku}, ${row.stock})`;
	}

	// Fabricated, correct in shape, belonging to nobody — never presented as real.
	await sql`INSERT INTO merchant_tax_identity (id, legal_name, gstin, registered_state, tax_inclusive, invoice_prefix)
	          VALUES (1, ${profile.tax.legalName}, ${profile.tax.gstin}, ${profile.tax.state},
	                  ${profile.tax.taxInclusive}, ${profile.tax.invoicePrefix})`;

	for (const zone of profile.zones) {
		await sql`INSERT INTO shipping_zones (id, label, states, cost_minor, eta_days)
		          VALUES (${zone.id}, ${zone.label}, ${sql.json(zone.states)}, ${zone.cost}, ${zone.eta})`;
	}
	for (const prefix of profile.unserviceable) {
		await sql`INSERT INTO unserviceable_postal_ranges (prefix) VALUES (${prefix})`;
	}
	for (const code of profile.codes) {
		await sql`INSERT INTO discount_codes (code, label, amount_minor, visibility, max_uses)
		          VALUES (${code.code}, ${code.label}, ${code.amount}, ${code.visibility}, ${code.maxUses})`;
	}

	// scrypt from the standard library rather than a native argon2 build: it is
	// memory-hard, it needs no build script, and one fewer native dependency is
	// one fewer thing that fails on somebody else's machine.
	const password = process.env.ADMIN_SEED_PASSWORD ?? randomBytes(12).toString('base64url');
	const salt = randomBytes(16);
	const hash = scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 });
	const email = `operator@${profile.domain}`;
	await sql`INSERT INTO admin_users (email, password_hash)
	          VALUES (${email}, ${`scrypt$${salt.toString('hex')}$${hash.toString('hex')}`})`;

	console.log(
		`Seeded ${profile.shop}: ${profile.groups.length} groups, ${profile.items.length} items, ` +
			`${profile.zones.length} zones, ${profile.codes.length} codes.`
	);
	console.log(`Admin: ${email} / ${password}`);
}
