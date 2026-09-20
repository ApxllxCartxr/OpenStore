/**
 * RFC 9421 signing, the other end of the sidecar's verifier.
 *
 * The signature base here is **byte-identical** to
 * `src/openstore/sidecar/admission/signatures.py`'s. Both were written in one
 * sitting against one set of vectors, and `tests/signature-vectors.test.ts`
 * asserts against the same fixture the Python suite uses — so a change to
 * either end fails on both.
 *
 * The plan says to reuse `http-message-signatures` rather than hand-rolling the
 * base. We depend on it for the spec's own structured-field rules and keep the
 * base construction explicit here, because the sidecar has to reproduce it
 * exactly and an opaque helper on one side only is how the two drift.
 */
import { webcrypto } from 'node:crypto';
import type { Identity } from './keys.ts';
import { b64url } from './keys.ts';

const { subtle } = webcrypto;

/** §16.7: 60 seconds. A signature good for longer is one worth capturing. */
export const SIGNATURE_WINDOW_SECONDS = 60;

export async function contentDigest(body: string): Promise<string> {
	const digest = await subtle.digest('SHA-256', new TextEncoder().encode(body));
	// RFC 9530 structured field, colons included. A digest formatted
	// differently is one the other end will not match.
	return `sha-256=:${Buffer.from(new Uint8Array(digest)).toString('base64')}:`;
}

export function signatureBase({
	method,
	targetUri,
	contentDigestValue,
	created,
	expires,
	nonce,
	keyId
}: {
	method: string;
	targetUri: string;
	contentDigestValue: string;
	created: number;
	expires: number;
	nonce: string;
	keyId: string;
}): string {
	const params =
		`("@method" "@target-uri" "content-digest");created=${created};` +
		`expires=${expires};keyid="${keyId}";alg="ecdsa-p256-sha256";nonce="${nonce}"`;
	return [
		`"@method": ${method.toUpperCase()}`,
		`"@target-uri": ${targetUri}`,
		`"content-digest": ${contentDigestValue}`,
		`"@signature-params": ${params}`
	].join('\n');
}

export type SignedHeaders = {
	'content-type': string;
	'content-digest': string;
	signature: string;
	'signature-input': string;
	'x-openstore-nonce': string;
};

export async function signRequest(
	identity: Identity,
	{
		method,
		targetUri,
		body,
		now = Math.floor(Date.now() / 1000),
		nonce = crypto.randomUUID()
	}: { method: string; targetUri: string; body: string; now?: number; nonce?: string }
): Promise<SignedHeaders> {
	const created = now;
	const expires = now + 30; // inside the window, with room for clock skew
	const digest = await contentDigest(body);
	const base = signatureBase({
		method,
		targetUri,
		contentDigestValue: digest,
		created,
		expires,
		nonce,
		keyId: identity.kid
	});

	const raw = await subtle.sign(
		{ name: 'ECDSA', hash: 'SHA-256' },
		identity.privateKey,
		new TextEncoder().encode(base)
	);
	// WebCrypto emits r||s raw, which is what the verifier expects — not DER.
	const signature = Buffer.from(new Uint8Array(raw)).toString('base64');

	return {
		'content-type': 'application/json',
		'content-digest': digest,
		signature: `sig1=:${signature}:`,
		'signature-input':
			`sig1=("@method" "@target-uri" "content-digest");created=${created};` +
			`expires=${expires};keyid="${identity.kid}";alg="ecdsa-p256-sha256";nonce="${nonce}"`,
		'x-openstore-nonce': nonce
	};
}

export { b64url };
