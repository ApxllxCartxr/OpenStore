/**
 * The Merchant's own database. The sidecar has its own and there is no
 * cross-grant: `sc_app` cannot SELECT any table in here, and
 * `docker/postgres-init.sql` is what makes that structural rather than a
 * promise.
 */
import postgres from 'postgres';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const url =
	process.env.MERCHANT_DATABASE_URL ??
	'postgresql://sd_app:sd_app_dev@localhost:5432/spoiledduckie';

export const sql = postgres(url, {
	// Money is paise and every amount is a BIGINT. Without this, postgres.js
	// hands back strings for bigint columns and every comparison silently
	// becomes a string comparison.
	types: {
		bigint: postgres.BigInt
	},
	transform: { undefined: null },
	// `migrate()` is idempotent by design, so every re-run raises one
	// `42P07 relation ... already exists, skipping` per object. postgres.js
	// prints those by default, which buried the line that actually matters —
	// what the seed wrote — under sixteen stack-shaped objects per shop.
	//
	// Only that one code is dropped, and anything else postgres has to say is
	// still printed. A blanket `onnotice: () => {}` would also swallow the
	// notices worth reading: a truncated identifier, a deprecated cast, a
	// constraint quietly not created.
	onnotice: (notice) => {
		if (notice.code === '42P07') return;
		console.warn(notice);
	}
});

export async function migrate(): Promise<void> {
	// No literal BEGIN/COMMIT in the file: postgres.js owns transaction
	// boundaries and refuses them inside `unsafe`. Every statement is
	// `IF NOT EXISTS`, so re-running is a no-op rather than an error.
	const path = fileURLToPath(new URL('./schema.sql', import.meta.url));
	await sql.unsafe(readFileSync(path, 'utf8'));
}

export type Db = typeof sql;
