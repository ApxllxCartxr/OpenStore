<?php
/**
 * HMAC over the raw body, with a nonce and a 60-second window (§6.1, §16.7).
 *
 * **Over the raw bytes, never over a re-serialization.** WordPress hands
 * handlers a decoded body; re-encoding it to check a signature would cover a
 * round trip and not the request — two JSON encoders disagree about key order
 * and whitespace. `php://input` is read once, before anything parses it.
 *
 * The window plus the nonce is what stops replay. Neither alone does: a window
 * without a nonce lets an attacker resend inside it, and a nonce without a
 * window means remembering every nonce ever seen.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_HMAC {

	public const SIGNATURE_HEADER   = 'x-openstore-signature';
	public const TIMESTAMP_HEADER   = 'x-openstore-timestamp';
	public const NONCE_HEADER       = 'x-openstore-nonce';
	public const IDEMPOTENCY_HEADER = 'idempotency-key';

	public const REPLAY_WINDOW_SECONDS = 60;

	/** Nonces live in the object cache for exactly as long as they are valid. */
	private const NONCE_GROUP = 'openstore_nonce';

	/**
	 * The signature covers the path as well as the body.
	 *
	 * Without the path, a signed `release` body is a signed `commit` body: the
	 * two carry the same `{order_id}` shape, and an attacker who can redirect a
	 * request gets a free close of somebody's hold.
	 */
	public static function sign( string $secret, string $body, int $timestamp, string $nonce, string $path ): string {
		$preimage = implode( "\n", array( $path, (string) $timestamp, $nonce, $body ) );
		return hash_hmac( 'sha256', $preimage, $secret );
	}

	/**
	 * Raise unless this is a valid, fresh, unseen signature.
	 *
	 * @throws OpenStore_Door_Error Always `signature-invalid`, with no detail
	 *                              that distinguishes a wrong secret from a
	 *                              stale timestamp to an unauthenticated caller.
	 */
	public static function verify( string $secret, string $body, string $path, array $headers, ?int $now = null ): void {
		$signature = (string) ( $headers[ self::SIGNATURE_HEADER ] ?? '' );
		$nonce     = (string) ( $headers[ self::NONCE_HEADER ] ?? '' );
		$timestamp = (string) ( $headers[ self::TIMESTAMP_HEADER ] ?? '' );

		if ( '' === $signature || '' === $nonce || ! ctype_digit( $timestamp ) ) {
			throw new OpenStore_Door_Error( 'signature-invalid', 'signature, timestamp and nonce are all required' );
		}

		$expected = self::sign( $secret, $body, (int) $timestamp, $nonce, $path );
		// `hash_equals`, not `===`: a byte-by-byte comparison leaks the correct
		// prefix through timing, and the secret is the Merchant's.
		if ( ! hash_equals( $expected, $signature ) ) {
			throw new OpenStore_Door_Error( 'signature-invalid', 'signature does not verify' );
		}

		$now = $now ?? time();
		if ( abs( $now - (int) $timestamp ) > self::REPLAY_WINDOW_SECONDS ) {
			throw new OpenStore_Door_Error(
				'signature-invalid',
				sprintf( 'timestamp is outside the %ds replay window', self::REPLAY_WINDOW_SECONDS )
			);
		}

		self::remember_nonce( $nonce );
	}

	/**
	 * Remember a nonce for one window, and refuse a repeat.
	 *
	 * `wp_cache_add` is atomic where a real object cache is installed, which is
	 * what makes this a check rather than a suggestion. With the default
	 * non-persistent cache it is per-request only — which is why the signature
	 * window, not this, is the outer bound on a replay, and why the readme says
	 * to run a persistent object cache.
	 */
	private static function remember_nonce( string $nonce ): void {
		$fresh = wp_cache_add( $nonce, 1, self::NONCE_GROUP, self::REPLAY_WINDOW_SECONDS );
		if ( ! $fresh ) {
			throw new OpenStore_Door_Error( 'signature-invalid', 'that nonce has already been used' );
		}
	}

	/**
	 * Headers as a lowercase map, which is the only form worth comparing.
	 *
	 * @param WP_REST_Request $request
	 * @return array<string, string>
	 */
	public static function headers_from( WP_REST_Request $request ): array {
		$out = array();
		foreach ( $request->get_headers() as $name => $values ) {
			$out[ strtolower( str_replace( '_', '-', $name ) ) ] = is_array( $values ) ? (string) reset( $values ) : (string) $values;
		}
		return $out;
	}
}
