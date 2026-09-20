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

	it('no public route reserves stock', () => {
		const offenders = files
			.filter((path) => !isPrivate(path))
			.filter((path) => /UPDATE\s+stock\s+SET/i.test(readFileSync(path, 'utf8')));
		expect(offenders).toEqual([]);
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
	it('no storefront load function returns a raw stock count', () => {
		// `available` crosses door 2 and the loaders' internals; what reaches the
		// browser is a bucket. A count in a page payload is an oracle any visitor
		// can read.
		for (const path of files.filter((p) => p.endsWith('+page.server.ts'))) {
			const source = readFileSync(path, 'utf8');
			if (!source.includes('available')) continue;
			expect(source, `${path} returns a count to the browser`).toMatch(/bucketFor|groupBucket/);
		}
	});
});
