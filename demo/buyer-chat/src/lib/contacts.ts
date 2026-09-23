/**
 * C2 — paste a Merchant URL, fetch the card, save it as a contact.
 *
 * **A Consumer-pasted URL fetched server-side is the same SSRF sink, pointed
 * the other way.** The same rules as the sidecar's profile fetcher apply here,
 * including the same narrow dev-mode allowlist — because an exception that
 * exists on one side of the demo and not the other just moves the failure.
 *
 * **Merchant key trust is TOFU and pinned.** The card's JWKS is stored with the
 * contact at add time; a later fetch presenting a *different* key for a known
 * contact warns loudly and **blocks spend**. Silent key acceptance is how a
 * hijacked domain gets paid.
 *
 * Rotation is normal though (ADR-0014), so verification is `kid`-aware: an
 * unknown `kid` triggers **exactly one** JWKS refetch against the pinned
 * domain, and only a key that fails to appear there is treated as a hijack.
 */
import { isIP } from 'node:net';
import { lookup } from 'node:dns/promises';
import { db } from './session.ts';
import { ShopError, initializeGeneric, listToolsGeneric, type ToolDescriptor } from './mcp/client.ts';

export const METADATA_ADDRESSES = new Set(['169.254.169.254', 'fd00:ec2::254']);
export const MAX_CARD_BYTES = 64 * 1024;
export const FETCH_TIMEOUT_MS = 5000;

export class ContactError extends Error {
	constructor(
		readonly code:
			| 'not-https'
			| 'not-public'
			| 'metadata-refused'
			| 'unreachable'
			| 'not-a-card'
			| 'too-large'
			| 'redirected'
			| 'key-changed'
			| 'blocked'
			| 'no-tools',
		message: string
	) {
		super(message);
	}
}

function devHosts(): string[] {
	return (process.env.OPENSTORE_DEV_PROFILE_HOSTS ?? '')
		.split(',')
		.map((h) => h.trim())
		.filter(Boolean);
}

/** RFC 1918, loopback, link-local, and anything else not routable. */
export function isPublicAddress(address: string): boolean {
	if (METADATA_ADDRESSES.has(address)) return false;
	const version = isIP(address);
	if (version === 4) {
		const parts = address.split('.').map(Number) as [number, number, number, number];
		const [a, b] = parts;
		if (a === 10 || a === 127 || a === 0) return false;
		if (a === 172 && b >= 16 && b <= 31) return false;
		if (a === 192 && b === 168) return false;
		if (a === 169 && b === 254) return false;
		if (a >= 224) return false; // multicast and reserved
		return true;
	}
	if (version === 6) {
		const lower = address.toLowerCase();
		if (lower === '::1' || lower === '::') return false;
		if (lower.startsWith('fe80') || lower.startsWith('fc') || lower.startsWith('fd')) return false;
		return true;
	}
	return false;
}

export type CheckedUrl = { host: string; addresses: string[]; usedDevException: boolean };

/**
 * Validate **before any fetch**. Every refusal here happens without a packet
 * leaving the box, which is the point: a check performed after the request has
 * been made has already lost.
 */
export async function checkUrl(
	raw: string,
	resolver: (host: string) => Promise<string[]> = defaultResolver
): Promise<CheckedUrl> {
	let url: URL;
	try {
		url = new URL(raw);
	} catch {
		throw new ContactError('not-a-card', `${raw} is not a URL.`);
	}

	// `new URL()` strips a default port, so `http://host:80` has an empty
	// `url.port`. An allowlist entry written as `host:80` would then never
	// match — a silent miss that reads as "the allowlist is broken". Both sides
	// are normalised the same way.
	const hostPort = url.port ? `${url.hostname}:${url.port}` : url.hostname;
	const normalised = devHosts().map((entry) => {
		try {
			const parsed = new URL(`http://${entry}`);
			return parsed.port ? `${parsed.hostname}:${parsed.port}` : parsed.hostname;
		} catch {
			return entry;
		}
	});
	const allowed = normalised.includes(hostPort) || normalised.includes(url.hostname);

	// Refused before the allowlist is consulted, so no configuration reaches it.
	if (METADATA_ADDRESSES.has(url.hostname)) {
		throw new ContactError('metadata-refused', 'That address is refused unconditionally.');
	}
	if (url.protocol === 'http:' && !allowed) {
		throw new ContactError(
			'not-https',
			'Shops are fetched over HTTPS. (The demo chat itself is the one exception, and it has to be named.)'
		);
	}
	if (url.protocol !== 'https:' && url.protocol !== 'http:') {
		throw new ContactError('not-a-card', `${url.protocol} is not a scheme we fetch.`);
	}

	const addresses = await resolver(url.hostname);
	if (!addresses.length) throw new ContactError('unreachable', `${url.hostname} resolves to nothing.`);

	for (const address of addresses) {
		if (METADATA_ADDRESSES.has(address)) {
			throw new ContactError('metadata-refused', 'That address is refused unconditionally.');
		}
		if (!isPublicAddress(address) && !allowed) {
			throw new ContactError(
				'not-public',
				`${url.hostname} resolves to ${address}, which is not reachable from the internet.`
			);
		}
	}
	return { host: url.hostname, addresses, usedDevException: allowed };
}

async function defaultResolver(host: string): Promise<string[]> {
	if (isIP(host)) return [host];
	try {
		const records = await lookup(host, { all: true });
		return records.map((r) => r.address);
	} catch {
		throw new ContactError('unreachable', `Cannot resolve ${host}.`);
	}
}

export type Card = {
	name: string;
	/** What the shop says it sells, from its own card. A hint for deciding
	 *  which shops to ask first and **never** grounds for concluding one has
	 *  nothing — the card says as much itself, and overlapping stock across
	 *  shops is the ordinary case. */
	description: string;
	category: string;
	domain: string;
	protocols: string[];
	jwksUrl: string;
	jwks: { keys: { kid?: string }[] };
};

/** A card as read, before its keys have been fetched. */
export type CardMeta = Omit<Card, 'jwks'>;

async function fetchJson(
	url: string,
	what: 'card' | 'keys',
	fetcher: typeof fetch,
	resolver?: (host: string) => Promise<string[]>
): Promise<unknown> {
	await checkUrl(url, resolver);

	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
	let response: Response;
	try {
		response = await fetcher(url, { redirect: 'manual', signal: controller.signal });
	} catch {
		throw new ContactError(
			'unreachable',
			what === 'card' ? 'That shop did not answer.' : 'That shop did not serve its keys.'
		);
	} finally {
		clearTimeout(timer);
	}

	if ([301, 302, 303, 307, 308].includes(response.status)) {
		// A redirect is how a public host becomes a private one after the check.
		throw new ContactError('redirected', 'That shop redirected us, and we do not follow redirects.');
	}
	if (!response.ok) throw new ContactError('unreachable', `That shop answered ${response.status}.`);

	const text = await response.text();
	if (text.length > MAX_CARD_BYTES) throw new ContactError('too-large', 'That card is too large.');

	try {
		return JSON.parse(text);
	} catch {
		throw new ContactError(
			'not-a-card',
			what === 'card'
				? 'That URL did not return a shop card.'
				: 'That shop served something that is not a key set.'
		);
	}
}

/**
 * **The card and the keys are two documents** (SPEC §V1). The card names where
 * its keys live; it does not carry them. A fetcher that expected them inline
 * would refuse every card this sidecar has ever served — which is exactly what
 * it did, because the only cards it was ever tested against were hand-built
 * fixtures of a shape nothing emits.
 *
 * Both fetches go through the same SSRF check, and the keys must live on the
 * host the card came from: a card that sources its keys elsewhere is handing
 * key custody to a third party, which defeats the pinning underneath it.
 */
export async function fetchCard(
	cardUrl: string,
	fetcher: typeof fetch = fetch,
	resolver?: (host: string) => Promise<string[]>
): Promise<Card> {
	const document = await fetchJson(cardUrl, 'card', fetcher, resolver);
	const meta = parseCard(document, new URL(cardUrl).hostname);
	const keys = await fetchJson(meta.jwksUrl, 'keys', fetcher, resolver);
	return { ...meta, jwks: parseJwks(keys) };
}

export function parseCard(document: unknown, domain: string): CardMeta {
	const card = document as {
		merchant?: {
			name?: unknown;
			domain?: unknown;
			description?: unknown;
			categories?: unknown;
		};
		category?: unknown;
		protocols?: unknown;
		endpoints?: { jwks?: unknown };
	};
	if (!card || typeof card !== 'object') {
		throw new ContactError('not-a-card', 'That URL did not return a shop card.');
	}

	// The host we fetched from is the identity; the card's own claim is just a
	// claim. A card copied from another shop says that shop's domain, and
	// believing it would pin the wrong keys under the wrong name.
	const claimed = card.merchant?.domain;
	if (typeof claimed === 'string' && claimed && claimed !== domain) {
		throw new ContactError(
			'not-a-card',
			`That card claims to be ${claimed}, but we fetched it from ${domain}.`
		);
	}

	const jwksUrl = card.endpoints?.jwks;
	if (typeof jwksUrl !== 'string' || !jwksUrl) {
		throw new ContactError(
			'not-a-card',
			'That card does not say where its keys live, so nothing it says could be checked.'
		);
	}
	let keyHost: string;
	try {
		keyHost = new URL(jwksUrl).hostname;
	} catch {
		throw new ContactError('not-a-card', `${jwksUrl} is not a URL we can fetch keys from.`);
	}
	if (keyHost !== domain) {
		throw new ContactError(
			'not-a-card',
			`That card sources its keys from ${keyHost}, which is not ${domain}.`
		);
	}

	// `merchant.categories` is the card's own list; `card.category` is the older
	// single-value spelling this chat has always read. Both are accepted so a
	// shop that has not redeployed still lands somewhere sensible.
	const categories = Array.isArray(card.merchant?.categories)
		? card.merchant.categories.map(String).filter(Boolean)
		: [];

	return {
		name: String(card.merchant?.name ?? domain),
		description: String(card.merchant?.description ?? ''),
		category: categories.length ? categories.join(', ') : String(card.category ?? ''),
		domain,
		protocols: Array.isArray(card.protocols) ? card.protocols.map(String) : [],
		jwksUrl
	};
}

export function parseJwks(document: unknown): Card['jwks'] {
	const jwks = document as { keys?: unknown };
	if (!jwks || typeof jwks !== 'object' || !Array.isArray(jwks.keys) || !jwks.keys.length) {
		throw new ContactError('not-a-card', 'That card carries no keys, so nothing it says can be checked.');
	}
	return jwks as Card['jwks'];
}

export type TrustResult =
	| { state: 'new' }
	| { state: 'known' }
	| { state: 'rotated'; kid: string }
	| { state: 'hijacked'; kid: string };

/**
 * TOFU, `kid`-aware.
 *
 * A key we have never seen under a `kid` we have never seen is **rotation**
 * when the pinned domain's current JWKS contains it, and a **hijack** when it
 * does not. Treating every new key as a hijack would break ADR-0014's additive
 * rotation; treating every new key as rotation would be no pinning at all.
 */
export function assessTrust(pinned: Card['jwks'], presented: Card['jwks']): TrustResult {
	const pinnedKids = new Set(pinned.keys.map((k) => k.kid).filter(Boolean));
	const presentedKids = presented.keys.map((k) => k.kid).filter(Boolean) as string[];

	if (!pinnedKids.size) return { state: 'new' };
	if (presentedKids.every((kid) => pinnedKids.has(kid))) return { state: 'known' };

	const unknown = presentedKids.find((kid) => !pinnedKids.has(kid))!;
	// The caller refetches the pinned domain's JWKS exactly once and calls
	// `confirmRotation`. Only a key absent from *that* is a hijack.
	return { state: 'rotated', kid: unknown };
}

export function confirmRotation(
	refetched: Card['jwks'],
	kid: string
): { state: 'rotated' } | { state: 'hijacked'; kid: string } {
	const present = refetched.keys.some((k) => k.kid === kid);
	return present ? { state: 'rotated' } : { state: 'hijacked', kid };
}

export function saveContact(card: Card, cardUrl: string): void {
	db.prepare(
		`INSERT INTO contacts (domain, name, description, category, card_url, jwks_url, jwks, protocols, added_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
		 -- Re-adding a shop refreshes what it says about itself. The hint is the
		 -- Merchant's to change, and a card re-read that kept the old wording
		 -- would leave this chat routing on a description the shop has retired.
		 ON CONFLICT (domain) DO UPDATE SET
		   name = excluded.name,
		   description = excluded.description,
		   category = excluded.category,
		   card_url = excluded.card_url`
	).run(
		card.domain,
		card.name,
		card.description,
		card.category,
		cardUrl,
		card.jwksUrl,
		JSON.stringify(card.jwks),
		JSON.stringify(card.protocols),
		new Date().toISOString()
	);
}

// ── Generic MCP servers — not one of this repo's own shops ─────────────────
//
// No agent-commerce.json, no JWKS, no self-registration: connecting is the
// same JSON-RPC handshake Claude Desktop's own "add an MCP server" flow
// does, over the same SSRF-hardened fetch every Consumer-pasted URL goes
// through (`checkUrl`, above — a URL is a URL regardless of what answers it).

export type GenericServer = { domain: string; name: string; endpoint: string; tools: ToolDescriptor[] };

/**
 * Tried when a pasted URL did not answer like an agent-commerce.json card.
 * `initialize` names the server (falling back to its hostname); `tools/list`
 * is what actually makes it useful — a server with no tools is refused here
 * rather than saved as a contact this chat can never do anything with.
 */
export async function connectMcp(
	url: string,
	fetcher: typeof fetch = fetch,
	resolver?: (host: string) => Promise<string[]>
): Promise<GenericServer> {
	await checkUrl(url, resolver);
	const domain = new URL(url).hostname;
	let name: string | null;
	let tools: ToolDescriptor[];
	try {
		({ name } = await initializeGeneric(url, fetcher));
		tools = await listToolsGeneric(url, fetcher);
	} catch (error) {
		if (error instanceof ShopError) {
			const code =
				error.code === 'auth-required' ? 'blocked' : error.code === 'too-large' ? 'too-large' : 'unreachable';
			throw new ContactError(code, error.message);
		}
		throw error;
	}
	if (!tools.length) {
		throw new ContactError('no-tools', `${domain} answered, but tools/list named nothing to call.`);
	}
	return { domain, name: name ?? domain, endpoint: url, tools };
}

export function saveGenericContact(server: GenericServer): void {
	db.prepare(
		`INSERT INTO contacts (domain, name, category, card_url, jwks, kind, mcp_endpoint, tools, added_at)
		 VALUES (?, ?, ?, ?, ?, 'generic', ?, ?, ?)
		 ON CONFLICT (domain) DO UPDATE SET
		   name = excluded.name, mcp_endpoint = excluded.mcp_endpoint, tools = excluded.tools`
	).run(
		server.domain,
		server.name,
		'mcp-server',
		server.endpoint,
		'{"keys":[]}',
		server.endpoint,
		JSON.stringify(server.tools),
		new Date().toISOString()
	);
}

export function getContact(domain: string): (Card & { card_url: string }) | null {
	const row = db.prepare(`SELECT * FROM contacts WHERE domain = ?`).get(domain) as
		| {
				domain: string;
				name: string;
				description: string;
				category: string;
				card_url: string;
				jwks_url: string;
				jwks: string;
				protocols: string;
		  }
		| undefined;
	if (!row) return null;
	return {
		domain: row.domain,
		name: row.name,
		description: row.description,
		category: row.category,
		card_url: row.card_url,
		jwksUrl: row.jwks_url,
		jwks: JSON.parse(row.jwks),
		protocols: JSON.parse(row.protocols)
	};
}

/**
 * Forgetting a contact deletes the **local bookmark only** and revokes nothing
 * server-side. Server revocation is separate and authoritative — a chat that
 * claimed to revoke would be telling the Consumer something it cannot do.
 */
export function forgetContact(domain: string): void {
	db.prepare(`DELETE FROM contacts WHERE domain = ?`).run(domain);
}
