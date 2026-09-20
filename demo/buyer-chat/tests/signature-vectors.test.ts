/**
 * A4c's gate, from the chat's end.
 *
 * The **same fixture** the sidecar's Python suite asserts against. If the
 * signature base drifts on either side, both suites fail — which is the whole
 * point of writing the signer and the verifier in one sitting.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { contentDigest, signatureBase, signRequest } from '../src/lib/identity/sign.ts';
import type { Identity, Jwk } from '../src/lib/identity/keys.ts';
import { thumbprint } from '../src/lib/identity/keys.ts';

const VECTORS = JSON.parse(
	readFileSync(new URL('../../../tests/GOLDEN/rfc9421/vectors.json', import.meta.url), 'utf8')
) as {
	private_jwk: Jwk;
	public_jwk: Jwk;
	cases: {
		name: string;
		method: string;
		target_uri: string;
		body: string;
		created: number;
		expires: number;
		nonce: string;
		key_id: string;
		content_digest: string;
		signature_base: string;
		signature: string;
	}[];
};

async function identity(): Promise<Identity> {
	const privateKey = (await webcrypto.subtle.importKey(
		'jwk',
		VECTORS.private_jwk as JsonWebKey,
		{ name: 'ECDSA', namedCurve: 'P-256' },
		true,
		['sign']
	)) as Identity['privateKey'];
	return { kid: 'chat-k1', privateKey, publicJwk: VECTORS.public_jwk };
}

describe('the signature base is identical on both ends', () => {
	for (const testCase of VECTORS.cases) {
		it(`reproduces the sidecar's base for ${testCase.name}`, async () => {
			expect(await contentDigest(testCase.body)).toBe(testCase.content_digest);
			expect(
				signatureBase({
					method: testCase.method,
					targetUri: testCase.target_uri,
					contentDigestValue: testCase.content_digest,
					created: testCase.created,
					expires: testCase.expires,
					nonce: testCase.nonce,
					keyId: testCase.key_id
				})
			).toBe(testCase.signature_base);
		});
	}

	it('digests a unicode body as its own bytes, not as escapes', async () => {
		const unicode = VECTORS.cases.find((c) => c.name === 'unicode')!;
		expect(await contentDigest(unicode.body)).toBe(unicode.content_digest);
	});
});

describe('signatures this chat produces verify against the pinned key', () => {
	it('round-trips through WebCrypto', async () => {
		const me = await identity();
		const body = JSON.stringify({ query: 'black tote' });
		const headers = await signRequest(me, {
			method: 'POST',
			targetUri: 'https://spoiledduckie.localhost/agent/search',
			body,
			now: 1789900000,
			nonce: 'nonce-roundtrip'
		});

		const raw = Buffer.from(headers.signature.replace(/^sig1=:|:$/g, ''), 'base64');
		// ES256 is 64 raw bytes (r||s), never DER — the verifier expects raw.
		expect(raw.length).toBe(64);

		const publicKey = await webcrypto.subtle.importKey(
			'jwk',
			VECTORS.public_jwk as JsonWebKey,
			{ name: 'ECDSA', namedCurve: 'P-256' },
			true,
			['verify']
		);
		const base = signatureBase({
			method: 'POST',
			targetUri: 'https://spoiledduckie.localhost/agent/search',
			contentDigestValue: headers['content-digest'],
			created: 1789900000,
			expires: 1789900030,
			nonce: 'nonce-roundtrip',
			keyId: 'chat-k1'
		});
		expect(
			await webcrypto.subtle.verify(
				{ name: 'ECDSA', hash: 'SHA-256' },
				publicKey,
				raw,
				new TextEncoder().encode(base)
			)
		).toBe(true);
	});

	it('a corrupted base does not verify — the A4c gate, from this end', async () => {
		const me = await identity();
		const headers = await signRequest(me, {
			method: 'POST',
			targetUri: 'https://spoiledduckie.localhost/agent/search',
			body: '{}',
			now: 1789900000,
			nonce: 'nonce-corrupt'
		});
		const raw = Buffer.from(headers.signature.replace(/^sig1=:|:$/g, ''), 'base64');
		const publicKey = await webcrypto.subtle.importKey(
			'jwk',
			VECTORS.public_jwk as JsonWebKey,
			{ name: 'ECDSA', namedCurve: 'P-256' },
			true,
			['verify']
		);
		// Same signature, different target: a redirected request must not verify.
		const tampered = signatureBase({
			method: 'POST',
			targetUri: 'https://spoiledduckie.localhost/agent/place-order',
			contentDigestValue: headers['content-digest'],
			created: 1789900000,
			expires: 1789900030,
			nonce: 'nonce-corrupt',
			keyId: 'chat-k1'
		});
		expect(
			await webcrypto.subtle.verify(
				{ name: 'ECDSA', hash: 'SHA-256' },
				publicKey,
				raw,
				new TextEncoder().encode(tampered)
			)
		).toBe(false);
	});

	it('keeps the acceptance window short', async () => {
		const me = await identity();
		const headers = await signRequest(me, {
			method: 'POST',
			targetUri: 'https://x/y',
			body: '{}',
			now: 1000
		});
		const created = Number(/created=(\d+)/.exec(headers['signature-input'])![1]);
		const expires = Number(/expires=(\d+)/.exec(headers['signature-input'])![1]);
		expect(expires - created).toBeLessThanOrEqual(60);
	});
});

describe('agent_id', () => {
	it('is the RFC 7638 thumbprint, derived from the key rather than claimed', async () => {
		const id = await thumbprint(VECTORS.public_jwk);
		expect(id).toHaveLength(43); // base64url of a sha256, unpadded
		// Same key, same id, every time — two agents cannot claim to be one.
		expect(await thumbprint(VECTORS.public_jwk)).toBe(id);
	});
});
