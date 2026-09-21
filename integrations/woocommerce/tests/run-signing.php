<?php
/**
 * Does the PHP HMAC produce the same bytes as the sidecar's?
 *
 * The preimage is `path \n timestamp \n nonce \n body`. Getting any part of that
 * wrong produces a signature that is stable, plausible and wrong on every
 * request — so the failure looks like "the secret is misconfigured" and a
 * Merchant spends an afternoon on it. These cases move one element each.
 *
 *     docker run --rm -v "$PWD:/src" php:8.2-cli \
 *         php /src/integrations/woocommerce/tests/run-signing.php
 */

declare( strict_types=1 );

define( 'ABSPATH', __DIR__ );

require_once __DIR__ . '/../openstore-sidecar/includes/class-openstore-error.php';
require_once __DIR__ . '/../openstore-sidecar/includes/class-openstore-hmac.php';

$path = __DIR__ . '/signing-vectors.json';
$doc  = json_decode( (string) file_get_contents( $path ), true );
if ( ! is_array( $doc ) || ! isset( $doc['cases'] ) ) {
	fwrite( STDERR, "could not read {$path}\n" );
	exit( 2 );
}

$failures = 0;
$seen     = array();
foreach ( $doc['cases'] as $case ) {
	$got = OpenStore_HMAC::sign(
		(string) $case['secret'],
		(string) $case['body'],
		(int) $case['timestamp'],
		(string) $case['nonce'],
		(string) $case['path']
	);

	if ( hash_equals( (string) $case['expected'], $got ) ) {
		printf( "ok    %s\n", $case['name'] );
	} else {
		$failures++;
		printf(
			"FAIL  %s\n      expected %s\n      got      %s\n",
			$case['name'],
			$case['expected'],
			$got
		);
	}
	$seen[] = $got;
}

// The two cases that differ only by door must not produce the same signature.
// If they did, every check above could still pass while the path contributed
// nothing — which is the bug that makes a signed `release` a signed `commit`.
if ( count( $seen ) !== count( array_unique( $seen ) ) ) {
	$failures++;
	print( "FAIL  two cases signed identically; something in the preimage is being ignored\n" );
}

printf( "\n%d case(s), %d failure(s)\n", count( $doc['cases'] ), $failures );
exit( $failures ? 1 : 0 );
