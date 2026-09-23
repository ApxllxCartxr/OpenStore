/**
 * C2's gates. The SSRF sink pointed the other way, and TOFU key pinning that
 * survives a legitimate rotation.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
	assessTrust,
	checkUrl,
	confirmRotation,
	connectMcp,
	ContactError,
	fetchCard,
	forgetContact,
	getContact,
	isPublicAddress,
	parseCard,
	parseJwks,
	saveContact,
	saveGenericContact
} from '../src/lib/contacts.ts';
import { db } from '../src/lib/session.ts';

const resolver = (map: Record<string, string[]>) => async (host: string) =>
	map[host] ?? ['93.184.216.34'];

describe('a Consumer-pasted URL is the same SSRF sink, pointed the other way', () => {
	it.each([
		['loopback', '127.0.0.1'],
		['rfc1918 10', '10.0.0.5'],
		['rfc1918 192', '192.168.1.9'],
		['rfc1918 172', '172.16.4.4'],
		['link-local', '169.254.1.1']
	])('refuses %s before any fetch', async (_label, address) => {
		await expect(
			checkUrl('https://internal.example/.well-known/agent-commerce.json', resolver({ 'internal.example': [address] }))
		).rejects.toThrow(ContactError);
	});

	it('refuses the metadata address unconditionally, even when named in the allowlist', async () => {
		process.env.OPENSTORE_DEV_PROFILE_HOSTS = '169.254.169.254';
		await expect(checkUrl('https://169.254.169.254/x')).rejects.toMatchObject({
			code: 'metadata-refused'
		});
		delete process.env.OPENSTORE_DEV_PROFILE_HOSTS;
	});

	it('refuses a host whose NAME is innocent and whose ANSWER is not', async () => {
		await expect(
			checkUrl('https://evil.example/x', resolver({ 'evil.example': ['169.254.169.254'] }))
		).rejects.toMatchObject({ code: 'metadata-refused' });
	});

	it('refuses plain http unless the host is named', async () => {
		await expect(checkUrl('http://shop.example/x')).rejects.toMatchObject({ code: 'not-https' });
	});

	it('admits exactly the named dev host and nothing else in its range', async () => {
		process.env.OPENSTORE_DEV_PROFILE_HOSTS = 'spoiledduckie.localhost:80';
		const resolve = resolver({ 'spoiledduckie.localhost': ['172.18.0.3'], other: ['172.18.0.4'] });
		await expect(
			checkUrl('http://spoiledduckie.localhost:80/.well-known/agent-commerce.json', resolve)
		).resolves.toMatchObject({ usedDevException: true });
		await expect(checkUrl('https://other/x', resolve)).rejects.toMatchObject({ code: 'not-public' });
		delete process.env.OPENSTORE_DEV_PROFILE_HOSTS;
	});

	it('passes a real public host with the allowlist unset', async () => {
		// The one that proves the mechanism rather than the exception.
		const checked = await checkUrl('https://shop.example/card', resolver({ 'shop.example': ['93.184.216.34'] }));
		expect(checked.usedDevException).toBe(false);
	});

	it('classifies addresses without guessing', () => {
		expect(isPublicAddress('93.184.216.34')).toBe(true);
		expect(isPublicAddress('::1')).toBe(false);
		expect(isPublicAddress('fd00::1')).toBe(false);
		expect(isPublicAddress('not-an-ip')).toBe(false);
	});
});

describe('fetching a card', () => {
	const publicResolver = resolver({ 'shop.example': ['93.184.216.34'] });

	it('refuses a redirect rather than following it', async () => {
		const fetcher = (async () => new Response(null, { status: 302 })) as unknown as typeof fetch;
		await expect(
			fetchCard('https://shop.example/card', fetcher, publicResolver)
		).rejects.toMatchObject({ code: 'redirected' });
	});

	it('refuses a dead sidecar with a named reason', async () => {
		const fetcher = (async () => {
			throw new Error('ECONNREFUSED');
		}) as unknown as typeof fetch;
		await expect(
			fetchCard('https://shop.example/card', fetcher, publicResolver)
		).rejects.toMatchObject({ code: 'unreachable' });
	});

	it('refuses a card that does not say where its keys live', () => {
		// The card names its key source; it never carries keys inline. See
		// `real-card.test.ts` for the same fetcher against the sidecar's own card.
		expect(() => parseCard({ merchant: { name: 'Shop' } }, 'shop.example')).toThrow(
			/where its keys live/
		);
	});

	it('refuses a key set with no keys in it', () => {
		expect(() => parseJwks({ keys: [] })).toThrow(/carries no keys/);
	});

	it('refuses something that is not a card at all', async () => {
		const fetcher = (async () => new Response('<html>hello</html>', { status: 200 })) as unknown as typeof fetch;
		await expect(
			fetchCard('https://shop.example/card', fetcher, publicResolver)
		).rejects.toMatchObject({ code: 'not-a-card' });
	});
});

describe('TOFU key trust', () => {
	const pinned = { keys: [{ kid: 'k1' }] };

	it('accepts a first sight', () => {
		expect(assessTrust({ keys: [] }, pinned)).toEqual({ state: 'new' });
	});

	it('accepts the same key', () => {
		expect(assessTrust(pinned, { keys: [{ kid: 'k1' }] })).toEqual({ state: 'known' });
	});

	it('treats an unknown kid as a rotation to be confirmed, not an immediate hijack', () => {
		// ADR-0014 makes rotation additive and normal. Calling every new key a
		// hijack would break every Merchant who rotates after an incident.
		expect(assessTrust(pinned, { keys: [{ kid: 'k2' }] })).toEqual({ state: 'rotated', kid: 'k2' });
	});

	it('confirms a rotation when the pinned domain really does publish the key', () => {
		expect(confirmRotation({ keys: [{ kid: 'k1' }, { kid: 'k2' }] }, 'k2')).toEqual({
			state: 'rotated'
		});
	});

	it('calls it a hijack when the key is nowhere on the pinned domain', () => {
		// Silent key acceptance is how a hijacked domain gets paid.
		expect(confirmRotation({ keys: [{ kid: 'k1' }] }, 'k9')).toEqual({
			state: 'hijacked',
			kid: 'k9'
		});
	});
});

describe('the contact book', () => {
	const card = {
		name: 'SpoiledDuckie',
		description: 'Bags and small leather accessories.',
		category: 'accessories',
		domain: 'shop.test',
		protocols: ['mcp', 'ucp'],
		jwksUrl: 'https://shop.test/.well-known/jwks.json',
		jwks: { keys: [{ kid: 'k1' }] }
	};

	beforeEach(() => saveContact(card, 'https://shop.test/card'));
	afterEach(() => forgetContact('shop.test'));

	it('stores the JWKS at add time so it can be pinned later', () => {
		expect(getContact('shop.test')?.jwks.keys[0]?.kid).toBe('k1');
	});

	it('pins where the keys came from, because rotation refetches that URL', () => {
		expect(getContact('shop.test')?.jwksUrl).toBe('https://shop.test/.well-known/jwks.json');
	});

	it('forgetting deletes the local bookmark only', () => {
		forgetContact('shop.test');
		expect(getContact('shop.test')).toBeNull();
		// Server revocation is separate and authoritative. A chat that claimed
		// to revoke would be telling the Consumer something it cannot do.
	});
});

describe('connecting to a bare MCP server — not one of this repo\'s own shops', () => {
	const publicResolver = resolver({ 'toybox.example': ['93.184.216.34'] });

	function stub(handler: (body: any) => unknown) {
		return (async (_url: string, init: RequestInit) => {
			const body = JSON.parse(String(init.body));
			return new Response(JSON.stringify(handler(body)), { status: 200 });
		}) as unknown as typeof fetch;
	}

	it('goes through the same SSRF check any pasted URL does', async () => {
		await expect(
			connectMcp('https://internal.example/mcp', undefined, resolver({ 'internal.example': ['10.0.0.5'] }))
		).rejects.toThrow(ContactError);
	});

	it('names the server from its own handshake and caches its tools', async () => {
		const fetcher = stub((body) =>
			body.method === 'initialize'
				? { jsonrpc: '2.0', id: body.id, result: { serverInfo: { name: 'Toybox' } } }
				: { jsonrpc: '2.0', id: body.id, result: { tools: [{ name: 'roll_dice' }] } }
		);
		const server = await connectMcp('https://toybox.example/mcp', fetcher, publicResolver);
		expect(server).toEqual({
			domain: 'toybox.example',
			name: 'Toybox',
			endpoint: 'https://toybox.example/mcp',
			tools: [{ name: 'roll_dice', description: '', inputSchema: { type: 'object' }, annotations: {} }]
		});
	});

	it('refuses a server with nothing to call — a contact this chat could never use', async () => {
		const fetcher = stub((body) =>
			body.method === 'initialize'
				? { jsonrpc: '2.0', id: body.id, result: {} }
				: { jsonrpc: '2.0', id: body.id, result: { tools: [] } }
		);
		await expect(connectMcp('https://toybox.example/mcp', fetcher, publicResolver)).rejects.toMatchObject({
			code: 'no-tools'
		});
	});

	it('round-trips through saveGenericContact and getContact-adjacent storage', async () => {
		const fetcher = stub((body) =>
			body.method === 'initialize'
				? { jsonrpc: '2.0', id: body.id, result: {} }
				: { jsonrpc: '2.0', id: body.id, result: { tools: [{ name: 'roll_dice' }] } }
		);
		const server = await connectMcp('https://toybox.example/mcp', fetcher, publicResolver);
		saveGenericContact(server);
		const row = db.prepare(`SELECT * FROM contacts WHERE domain = ?`).get('toybox.example') as any;
		expect(row.kind).toBe('generic');
		expect(row.mcp_endpoint).toBe('https://toybox.example/mcp');
		expect(JSON.parse(row.tools)).toEqual(server.tools);
		forgetContact('toybox.example');
	});
});
