<?php
/**
 * Door 9 — read-only, side-effect-free, and carrying no clock.
 *
 * Byte-identical for identical inputs against unchanged state. Not
 * time-invariant, deliberately: a price edit or an exhausted code changes the
 * answer, which is what the Gate's `quote-fresh` check exists to catch.
 *
 * **Shipping comes from WooCommerce's own zones**, so a Merchant configures
 * delivery once, in the place they already know, and agents see what customers
 * see. A cost is read from the flat-rate or free-shipping method on the zone
 * that matches the Destination — anything that needs a cart to price itself
 * (table rate, live carrier rates) is refused rather than guessed at.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Quote {

	public function __construct( private OpenStore_Catalogue $catalogue ) {}

	/**
	 * @param array<int, array{sku:string, qty:int, parent?:string|null}> $lines
	 * @param array<string, string> $destination
	 * @return array<string, mixed>
	 */
	public function quote( array $lines, array $destination, ?string $fulfillment_option_id, ?string $discount_code ): array {
		$postal_code = (string) ( $destination['postal_code'] ?? '' );
		foreach ( OpenStore_Settings::unserviceable_prefixes() as $prefix ) {
			if ( str_starts_with( $postal_code, $prefix ) ) {
				throw new OpenStore_Door_Error(
					'destination-unserviceable',
					sprintf( 'We do not deliver to %s yet.', $postal_code ),
					array( 'postal_code' => $postal_code )
				);
			}
		}

		$items = array();
		foreach ( $lines as $line ) {
			$product              = $this->catalogue->resolve( (string) $line['sku'] );
			$items[ (string) $line['sku'] ] = array(
				'sku'         => (string) $product->get_sku(),
				'price_minor' => OpenStore_Catalogue::price_minor( $product ),
				'hsn_sac'     => OpenStore_Catalogue::hsn_sac( $product ),
				'gst_rate_bp' => OpenStore_Catalogue::gst_rate_bp( $product ),
				'tags'        => OpenStore_Catalogue::tags( $product ),
			);
		}

		$zone = $this->zone_for( (string) ( $destination['state'] ?? '' ), $postal_code );
		if ( $fulfillment_option_id && $fulfillment_option_id !== $zone['id'] ) {
			throw new OpenStore_Door_Error(
				'destination-unserviceable',
				sprintf(
					'fulfillment option %s does not serve %s',
					$fulfillment_option_id,
					(string) ( $destination['state'] ?? '' )
				),
				array( 'available' => array( $zone['id'] ) )
			);
		}

		$discount = $discount_code ? $this->discount_for( $discount_code, $items ) : null;

		return OpenStore_Pricing::build_quote(
			$lines,
			$items,
			strtoupper( (string) ( $destination['state'] ?? '' ) ),
			OpenStore_Settings::home_state(),
			$zone,
			$discount,
			OpenStore_Settings::tax_inclusive()
		);
	}

	/**
	 * The shipping zone that serves this Destination, as one fulfillment option.
	 *
	 * @return array{id:string, label:string, cost_minor:int, eta_days:int}
	 */
	private function zone_for( string $state, string $postal_code ): array {
		$package = array(
			'destination' => array(
				'country'  => 'IN',
				'state'    => strtoupper( $state ),
				'postcode' => $postal_code,
				'city'     => '',
			),
		);

		$zone = function_exists( 'wc_get_shipping_zone' ) ? wc_get_shipping_zone( $package ) : null;
		if ( ! $zone instanceof WC_Shipping_Zone ) {
			throw new OpenStore_Door_Error(
				'destination-unserviceable',
				sprintf( 'no shipping zone covers %s', $state ),
				array( 'state' => $state )
			);
		}

		foreach ( $zone->get_shipping_methods( true ) as $method ) {
			$cost = $this->cost_of( $method );
			if ( null === $cost ) {
				continue;
			}
			return array(
				'id'         => $this->option_id( $zone, $method ),
				'label'      => (string) $method->get_title(),
				'cost_minor' => $cost,
				'eta_days'   => (int) apply_filters( 'openstore_zone_eta_days', 5, $zone, $method ),
			);
		}

		throw new OpenStore_Door_Error(
			'destination-unserviceable',
			sprintf(
				'the shipping zone for %s has no method this plugin can price. Flat rate and free shipping are supported; a method that needs a cart to price itself is not.',
				$state
			),
			array( 'zone' => $zone->get_zone_name() )
		);
	}

	/**
	 * What one shipping method costs, in paise, or null if it cannot be priced
	 * without a cart.
	 *
	 * A live carrier rate or a table rate depends on a WooCommerce cart session
	 * that does not exist here, and inventing a number for it would put a
	 * shipping cost in front of a Consumer that the shop would not honour.
	 */
	private function cost_of( WC_Shipping_Method $method ): ?int {
		if ( 'free_shipping' === $method->id ) {
			return 0;
		}
		if ( 'flat_rate' === $method->id ) {
			$cost = (string) $method->get_option( 'cost', '' );
			// Flat rate supports formulas like `10 + 2 * [qty]`. A formula needs
			// a cart, so only a plain number is honoured here.
			if ( ! is_numeric( $cost ) ) {
				return null;
			}
			return (int) round( ( (float) $cost ) * 100 );
		}
		if ( 'local_pickup' === $method->id ) {
			$cost = (string) $method->get_option( 'cost', '0' );
			return is_numeric( $cost ) ? (int) round( ( (float) $cost ) * 100 ) : 0;
		}
		return null;
	}

	private function option_id( WC_Shipping_Zone $zone, WC_Shipping_Method $method ): string {
		return sanitize_title( $zone->get_zone_name() ?: 'everywhere' ) . ':' . $method->id;
	}

	/**
	 * The discount a code names, as a negative amount.
	 *
	 * **Every wrong code refuses the same way**, with no message and no timing
	 * tell — otherwise door 9 answers "is this a code?" all day. Percentage
	 * coupons are resolved against the basket here, because the Quote carries
	 * amounts and never rates.
	 *
	 * @param array<string, array{price_minor:int, tags:string[]}> $items
	 * @return array{code:string, label:string, amount_minor:int}
	 */
	private function discount_for( string $code, array $items ): array {
		$coupon = new WC_Coupon( $code );
		if ( ! $coupon->get_id() || $coupon->get_date_expires() && $coupon->get_date_expires()->getTimestamp() < time() ) {
			throw new OpenStore_Door_Error( 'code-invalid', 'That code is not valid.' );
		}
		if ( $coupon->get_usage_limit() && $coupon->get_usage_count() >= $coupon->get_usage_limit() ) {
			throw new OpenStore_Door_Error( 'code-invalid', 'That code is not valid.' );
		}

		$amount = (float) $coupon->get_amount();
		if ( 'percent' === $coupon->get_discount_type() ) {
			$basis = 0;
			foreach ( $items as $item ) {
				$basis += $item['price_minor'];
			}
			$minor = intdiv( (int) round( $amount * 100 ) * $basis, 10000 );
		} else {
			$minor = (int) round( $amount * 100 );
		}

		if ( $minor <= 0 ) {
			throw new OpenStore_Door_Error( 'code-invalid', 'That code is not valid.' );
		}

		return array(
			'code'         => $coupon->get_code(),
			'label'        => $coupon->get_description() ?: $coupon->get_code(),
			'amount_minor' => -abs( $minor ),
		);
	}
}
