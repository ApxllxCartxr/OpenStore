/**
 * A4c — the agent's own ES256 identity.
 *
 * **Authored by Track P alongside the sidecar's verifier, in the same sitting,
 * against the same test vectors.** The signature base is the fiddly part of RFC
 * 9421, and two people debugging it from opposite ends in two languages is the
 * classic way a day disappears.
 *
 * This does not touch the firewall. The firewall forbids *imports across
 * roots*, not authorship — a person is not a root. These files live in
 * `demo/buyer-chat/`, import nothing from the sidecar, and reach it only over
 * HTTP.
 *
 * Node's built-in WebCrypto, no heavy dependency. The key file lives **outside
 * the session DB** and is gitignored: a rebuilt container re-registers with the
 * same key, because a signer with no key story is a demo that cannot survive a
 * `docker compose down`.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { webcrypto } from 'node:crypto';

/** Node's own CryptoKey, not the DOM lib's: Node 26 adds KEM key usages that
 *  the DOM type does not know about, and the two are structurally different. */
export type NodeCryptoKey = webcrypto.CryptoKey;

const { subtle } = webcrypto;

export type Jwk = {
	kty: 'EC';
	crv: 'P-256';
	x: string;
	y: string;
	d?: string;
	kid?: string;
	alg?: 'ES256';
	use?: 'sig';
};

export type Identity = {
	kid: string;
	privateKey: NodeCryptoKey;
	publicJwk: Jwk;
};

const KEY_PATH = process.env.CHAT_KEY_PATH ?? '/data/agent-key.json';

async function generate(kid: string): Promise<{ identity: Identity; stored: Jwk }> {
	const pair = await subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, [
		'sign',
		'verify'
	]);
	const privateJwk = (await subtle.exportKey('jwk', pair.privateKey)) as Jwk;
	const publicJwk = (await subtle.exportKey('jwk', pair.publicKey)) as Jwk;
	const stored: Jwk = { ...privateJwk, kid, alg: 'ES256', use: 'sig' };
	return {
		identity: {
			kid,
			privateKey: pair.privateKey,
			publicJwk: { ...publicJwk, kid, alg: 'ES256', use: 'sig' }
		},
		stored
	};
}

/**
 * Load the key from disk, or make one and persist it.
 *
 * Rotation is **additive by `kid`**, the same rule the sidecar's own keyring
 * follows: a new key is a new `kid` in the published JWKS and the old one stays
 * until it is deliberately dropped.
 */
export async function loadOrCreate(path: string = KEY_PATH, kid = 'chat-k1'): Promise<Identity> {
	try {
		const stored = JSON.parse(readFileSync(path, 'utf8')) as Jwk;
		const privateKey = await subtle.importKey(
			'jwk',
			stored as JsonWebKey,
			{ name: 'ECDSA', namedCurve: 'P-256' },
			true,
			['sign']
		);
		const { d: _discarded, ...publicJwk } = stored;
		return { kid: stored.kid ?? kid, privateKey, publicJwk: publicJwk as Jwk };
	} catch {
		const { identity, stored } = await generate(kid);
		mkdirSync(dirname(path), { recursive: true });
		// 0600: the key is the agent's whole identity, and a world-readable one
		// lets anything on the box sign as this agent.
		writeFileSync(path, JSON.stringify(stored), { mode: 0o600 });
		return identity;
	}
}

/**
 * The Agent Profile this chat publishes. It **admits**; it never authorizes a
 * spend — no scope, no tier, no credential, just a name, a contact and a key
 * the sidecar can verify signatures against.
 */
export function agentProfile(identity: Identity, origin: string) {
	return {
		name: 'OpenStore demo chat',
		contact: 'demo@spoiledduckie.test',
		url: origin,
		jwks: { keys: [identity.publicJwk] }
	};
}

/** RFC 7638 thumbprint — the `agent_id` a stranger is known by.
 *  Derived from the key rather than claimed, so two agents cannot say they are
 *  the same one. */
export async function thumbprint(jwk: Jwk): Promise<string> {
	// The required members for EC, lexicographic, no whitespace. The spec is
	// precise because the whole value of a thumbprint is that two
	// implementations compute the same one.
	const canonical = JSON.stringify({ crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y });
	const digest = await subtle.digest('SHA-256', new TextEncoder().encode(canonical));
	return b64url(new Uint8Array(digest));
}

export function b64url(bytes: Uint8Array): string {
	return Buffer.from(bytes).toString('base64url');
}
