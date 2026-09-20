/**
 * Cut 5, asserted rather than remembered.
 *
 * **No public storefront route creates an order.** Door 7 `orders.create` — on
 * the private network, called by the sidecar — is the only path that does, and
 * a second money path is forbidden (SPEC §9). This test walks the route tree so
 * that path cannot reappear by accident in six weeks.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROUTES = new URL('../src/routes', import.meta.url).pathname;

function walk(dir: string): string[] {
	return readdirSync(dir).flatMap((entry) => {
		const path = join(dir, entry);
		return statSync(path).isDirectory() ? walk(path) : [path];
	});
}

const files = walk(ROUTES);

/** Everything under /trait is the private-network door surface. */
const isPrivate = (path: string) => path.includes(`${'/'}trait${'/'}`);

describe('cut 5: no second money path', () => {
	it('only the private trait surface writes an order row', () => {
		const offenders = files
			.filter((path) => !isPrivate(path))
			.filter((path) => /INSERT\s+INTO\s+orders/i.test(readFileSync(path, 'utf8')));
		expect(offenders).toEqual([]);
	});

	it('no PUBLIC route moves stock', () => {
		// The Merchant's own admin adjusts inventory — that is Merchant truth
		// being edited by the person who owns it. What must never happen is a
		// public route holding stock, because that is a checkout by another name.
		const offenders = files
			.filter((path) => !isPrivate(path) && !path.includes(`${'/'}admin${'/'}`))
			.filter((path) => /UPDATE\s+stock\s+SET/i.test(readFileSync(path, 'utf8')));
		expect(offenders).toEqual([]);
	});

	it('every admin stock write is audited in stock_moves', () => {
		// An adjustment nobody can trace is an adjustment nobody can dispute.
		for (const path of files.filter((p) => p.includes(`${'/'}admin${'/'}`))) {
			const source = readFileSync(path, 'utf8');
			if (!/UPDATE\s+stock\s+SET/i.test(source)) continue;
			expect(source, `${path} moves stock without auditing it`).toMatch(
				/INSERT INTO stock_moves/i
			);
		}
	});

	it('there is no direct cart or checkout route', () => {
		// site_carts existed only to hold stock for the direct cart, and the
		// direct cart is not built.
		expect(files.some((p) => /routes\/(cart|checkout)\b/.test(p))).toBe(false);
		expect(readFileSync(new URL('../src/lib/schema.sql', import.meta.url), 'utf8')).not.toMatch(
			/CREATE TABLE.*site_carts/i
		);
	});

	it('the group page offers the agent card instead of an Add to cart', () => {
		const page = readFileSync(new URL('../src/routes/p/[slug]/+page.svelte', import.meta.url), 'utf8');
		const loader = readFileSync(new URL('../src/routes/p/[slug]/+page.server.ts', import.meta.url), 'utf8');
		expect(page).toMatch(/Buy via your agent/);
		expect(page).toMatch(/data\.cardUrl/);
		// The URL itself is built server-side, from the deployment's own origin.
		expect(loader).toMatch(/agent-commerce\.json/);
		expect(page).not.toMatch(/Add to cart/i);
	});
});

describe('the exposure boundary', () => {
	/**
	 * The rule is about **agents and the public**, not about the Merchant.
	 *
	 * `/admin` is session-authenticated shop ops and shows exact counts on
	 * purpose — it is the Merchant's own inventory and they are the ones who
	 * have to reorder it. What must never carry a count is a public page or an
	 * agent-facing payload, because a count handed to anyone who asks is an
	 * inventory-probing oracle.
	 */
	const isMerchantOwnConsole = (path: string) => path.includes(`${'/'}admin${'/'}`);

	it('no PUBLIC storefront loader returns a raw stock count', () => {
		const leaks = files
			.filter((p) => p.endsWith('+page.server.ts'))
			.filter((p) => !isMerchantOwnConsole(p))
			.filter((p) => readFileSync(p, 'utf8').includes('available'))
			.filter((p) => !/bucketFor|groupBucket/.test(readFileSync(p, 'utf8')));
		expect(leaks).toEqual([]);
	});

	it('the admin is allowed its counts, and is behind a session', () => {
		const guard = readFileSync(new URL('../src/routes/admin/+layout.server.ts', import.meta.url), 'utf8');
		expect(guard).toMatch(/redirect\(303, '\/admin\/login'\)/);
	});
});
