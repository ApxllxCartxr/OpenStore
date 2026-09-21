<?php
/**
 * What the Merchant has to tell this plugin, and what it refuses to guess.
 *
 * Three of these have no safe default and are therefore never defaulted: the
 * HMAC secret, the GST registration state, and whether prices include tax.
 * Guessing the last one double-charges every order, which is why
 * `tax_inclusive()` reads WooCommerce's own setting rather than inventing one.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Settings {

	public const OPTION_SECRET       = 'openstore_trait_hmac_secret';
	public const OPTION_HOME_STATE   = 'openstore_registered_state';
	public const OPTION_DEFAULT_HSN  = 'openstore_default_hsn_sac';
	public const OPTION_DEFAULT_RATE = 'openstore_default_gst_rate_bp';
	public const OPTION_UNSERVICEABLE = 'openstore_unserviceable_prefixes';

	/** Product meta a Merchant sets per item, because tax detail is per item. */
	public const META_HSN  = '_openstore_hsn_sac';
	public const META_RATE = '_openstore_gst_rate_bp';
	public const META_TAGS = '_openstore_tags';

	public static function secret(): string {
		$secret = (string) get_option( self::OPTION_SECRET, '' );
		if ( '' === $secret ) {
			// No secret means no signature can verify, which refuses every door.
			// That is the conservative half of a choice with one safe side: a
			// door that accepted an unsigned body is a door anyone on the
			// network can take stock through.
			throw new OpenStore_Door_Error(
				'signature-invalid',
				'This store has no OpenStore trait secret set, so no request can be verified. Set it under WooCommerce → OpenStore.'
			);
		}
		return $secret;
	}

	public static function has_secret(): bool {
		return '' !== (string) get_option( self::OPTION_SECRET, '' );
	}

	/**
	 * The Merchant's own state, which decides CGST/SGST versus IGST.
	 *
	 * Refused rather than defaulted: a wrong home state puts the wrong tax
	 * split on every order in the country.
	 */
	public static function home_state(): string {
		$state = strtoupper( trim( (string) get_option( self::OPTION_HOME_STATE, '' ) ) );
		if ( ! preg_match( '/^[A-Z]{2}$/', $state ) ) {
			throw new OpenStore_Door_Error(
				'quote-inconsistent',
				'This store has no GST registration state set, so CGST/SGST cannot be told apart from IGST. Set it under WooCommerce → OpenStore.'
			);
		}
		return $state;
	}

	/**
	 * Whether prices already include tax.
	 *
	 * Read from WooCommerce rather than stored again here. Two settings for one
	 * fact drift, and this is the fact that double-charges every order when it
	 * is wrong.
	 */
	public static function tax_inclusive(): bool {
		return wc_prices_include_tax();
	}

	public static function default_hsn(): string {
		return (string) get_option( self::OPTION_DEFAULT_HSN, '' );
	}

	public static function default_rate_bp(): int {
		return (int) get_option( self::OPTION_DEFAULT_RATE, 0 );
	}

	/** @return string[] Postal-code prefixes this store does not deliver to. */
	public static function unserviceable_prefixes(): array {
		$raw = (string) get_option( self::OPTION_UNSERVICEABLE, '' );
		$out = array();
		foreach ( preg_split( '/[\s,]+/', $raw ) ?: array() as $prefix ) {
			$prefix = trim( $prefix );
			if ( '' !== $prefix ) {
				$out[] = $prefix;
			}
		}
		return $out;
	}
}
