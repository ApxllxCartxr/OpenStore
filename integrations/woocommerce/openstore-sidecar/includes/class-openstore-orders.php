<?php
/**
 * Doors 7 and 8: the order, which is a real WooCommerce order.
 *
 * **Not a parallel book.** An agent's order appears in WooCommerce → Orders
 * like any other, is packed and shipped by the same people, and shows up in the
 * shop's own reports. A plugin that kept its own order table would be asking a
 * Merchant to run two businesses.
 *
 * **Status is the interesting part.** The trait has eight statuses; WooCommerce
 * has its own set, and two of the trait's — `cancelled` and `expired` — land on
 * the same WooCommerce status. So the WooCommerce status is authoritative and
 * the trait status is *derived* from it, with one meta value used only to tell
 * those two apart. That ordering matters: a shopkeeper who marks an order
 * complete in the admin has changed the truth, and a plugin that answered from
 * its own meta would tell the sidecar something the shop had stopped believing.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Orders {

	private const META_CART_ID  = '_openstore_cart_id';
	private const META_SALT     = '_openstore_order_salt';
	private const META_AGENT    = '_openstore_agent_id';
	private const META_CONSUMER = '_openstore_consumer_id';
	private const META_STATUS   = '_openstore_status';
	private const META_LINES    = '_openstore_lines';
	private const META_REASON   = '_openstore_cancel_reason';

	/** Trait status → WooCommerce status. */
	private const TO_WC = array(
		'pending'   => 'pending',
		'confirmed' => 'on-hold',
		'paid'      => 'processing',
		'completed' => 'completed',
		'cancelled' => 'cancelled',
		'expired'   => 'cancelled',
		'failed'    => 'failed',
		'refunded'  => 'refunded',
	);

	/** WooCommerce status → trait status. */
	private const FROM_WC = array(
		'pending'    => 'pending',
		'on-hold'    => 'confirmed',
		'processing' => 'paid',
		'completed'  => 'completed',
		'cancelled'  => 'cancelled',
		'failed'     => 'failed',
		'refunded'   => 'refunded',
		'checkout-draft' => 'pending',
	);

	public function __construct( private OpenStore_Catalogue $catalogue, private OpenStore_Quote $quote ) {}

	/**
	 * Door 7 (create) — the door that produces the `order_id`, and the only
	 * response that ever carries the `order_salt` (§6.3a).
	 *
	 * The salt is 128 bits from `random_bytes`, stored once on the order and
	 * returned exactly here. Erasing the order deletes the salt with it, and the
	 * sidecar's evidence commitments become permanently unopenable — which is
	 * the erasure guarantee, implemented by deletion rather than promised.
	 *
	 * @param array<string, mixed> $payload
	 * @return array{order_id:string, order_salt_hex:string, status:string}
	 */
	public function create( array $payload ): array {
		$lines       = (array) ( $payload['lines'] ?? array() );
		$destination = (array) ( $payload['destination'] ?? array() );
		$contact     = (array) ( $payload['contact'] ?? array() );

		// Priced before it is written, so an order is never created for a basket
		// the shop would refuse to quote.
		$priced = $this->quote->quote(
			$lines,
			$destination,
			(string) ( $payload['fulfillment_option_id'] ?? '' ) ?: null,
			null
		);

		$order = new WC_Order();
		$order->set_created_via( 'openstore-agent' );
		$order->set_currency( 'INR' );
		$order->set_prices_include_tax( OpenStore_Settings::tax_inclusive() );

		foreach ( $priced['lines'] as $quote_line ) {
			$product = $this->catalogue->resolve( (string) $quote_line['sku'] );
			$item    = new WC_Order_Item_Product();
			$item->set_product( $product );
			$item->set_quantity( (int) $quote_line['qty'] );
			// The Quote's own figures, not WooCommerce's recomputation. The
			// Consumer authorizes this total; an order that re-prices itself on
			// save would be a different agreement.
			$item->set_subtotal( (string) ( $quote_line['line_total_minor'] / 100 ) );
			$item->set_total( (string) ( $quote_line['line_total_minor'] / 100 ) );
			$order->add_item( $item );
		}

		$shipping = new WC_Order_Item_Shipping();
		$shipping->set_method_title( (string) ( $priced['fulfillment_options'][0]['label'] ?? 'Shipping' ) );
		$shipping->set_method_id( (string) $priced['fulfillment_chosen']['id'] );
		$shipping->set_total( (string) ( ( (int) $priced['fulfillment_chosen']['cost_minor'] ) / 100 ) );
		$order->add_item( $shipping );

		$order->set_address(
			array(
				'address_1' => (string) ( $destination['line1'] ?? '' ),
				'address_2' => (string) ( $destination['line2'] ?? '' ),
				'city'      => (string) ( $destination['city'] ?? '' ),
				'state'     => strtoupper( (string) ( $destination['state'] ?? '' ) ),
				'postcode'  => (string) ( $destination['postal_code'] ?? '' ),
				'country'   => 'IN',
				'email'     => (string) ( $contact['email'] ?? '' ),
				'phone'     => (string) ( $contact['phone'] ?? '' ),
			),
			'shipping'
		);
		$order->set_billing_email( (string) ( $contact['email'] ?? '' ) );
		$order->set_billing_phone( (string) ( $contact['phone'] ?? '' ) );

		$salt = bin2hex( random_bytes( 16 ) );
		$order->update_meta_data( self::META_CART_ID, (string) ( $payload['cart_id'] ?? '' ) );
		$order->update_meta_data( self::META_SALT, $salt );
		$order->update_meta_data( self::META_AGENT, (string) ( $payload['agent_id'] ?? '' ) );
		$order->update_meta_data( self::META_CONSUMER, (string) ( $payload['consumer_id'] ?? '' ) );
		$order->update_meta_data( self::META_STATUS, 'pending' );
		// The lines as the agent sent them, Add-on parents included. The order's
		// own items fold Add-ons into their parent's total, so the shape the
		// sidecar hashed would otherwise not survive the round trip.
		$order->update_meta_data( self::META_LINES, wp_json_encode( array_values( $lines ) ) );

		$order->set_total( (string) ( ( (int) $priced['total_minor'] ) / 100 ) );
		$order->set_status( 'pending' );
		$order->save();

		return array(
			'order_id'       => $this->order_id( $order ),
			'order_salt_hex' => $salt,
			'status'         => 'pending',
		);
	}

	/**
	 * Door 7 (read). The salt is never here.
	 *
	 * @return array<string, mixed>
	 */
	public function read( string $order_id ): array {
		$order = $this->find( $order_id );

		$lines = json_decode( (string) $order->get_meta( self::META_LINES, true ), true );
		if ( ! is_array( $lines ) || ! $lines ) {
			$lines = array();
			foreach ( $order->get_items() as $item ) {
				if ( $item instanceof WC_Order_Item_Product ) {
					$product = $item->get_product();
					if ( $product instanceof WC_Product && '' !== (string) $product->get_sku() ) {
						$lines[] = array(
							'sku' => (string) $product->get_sku(),
							'qty' => (int) $item->get_quantity(),
						);
					}
				}
			}
		}

		$refunded = (int) round( ( (float) $order->get_total_refunded() ) * 100 );

		return array(
			'order_id'        => $order_id,
			'status'          => $this->trait_status( $order ),
			'lines'           => array_values( $lines ),
			'total_minor'     => (int) round( ( (float) $order->get_total() ) * 100 ),
			'refunded_minor'  => $refunded,
			// Merchant-set pass-throughs. The sidecar computes none of them
			// (ADR-0020) and this plugin invents none either: an empty tracking
			// number means the shop has not entered one.
			'tracking_number' => (string) $order->get_meta( '_tracking_number', true ),
			'carrier'         => (string) $order->get_meta( '_tracking_provider', true ),
			'dispatched_at'   => $this->iso( (string) $order->get_meta( '_date_shipped', true ) ),
			'invoice_number'  => (string) $order->get_meta( '_invoice_number', true ),
			'expires_at'      => null,
		);
	}

	/**
	 * Door 8 — the serialization point for tap vs expiry vs shop-reject.
	 *
	 * The sidecar owns both expiry clocks and this store never self-expires: a
	 * WooCommerce cron that cancelled a `pending` order on its own would race
	 * the tap, and the loser would be whichever one the Consumer was doing.
	 *
	 * @return array<string, mixed>
	 */
	public function set_status( string $order_id, string $status, string $reason ): array {
		$order = $this->find( $order_id );

		if ( ! isset( self::TO_WC[ $status ] ) ) {
			throw new OpenStore_Door_Error(
				'not-found',
				sprintf( '%s is not one of the eight order statuses', $status ),
				array( 'status' => $status )
			);
		}

		$order->update_meta_data( self::META_STATUS, $status );
		if ( '' !== $reason ) {
			$order->update_meta_data( self::META_REASON, $reason );
			$order->add_order_note( sprintf( 'OpenStore: %s (%s)', $status, $reason ) );
		} else {
			$order->add_order_note( sprintf( 'OpenStore: %s', $status ) );
		}
		$order->set_status( self::TO_WC[ $status ] );
		$order->save();

		return $this->read( $order_id );
	}

	/**
	 * The trait status for an order, derived from WooCommerce's own.
	 *
	 * The meta is consulted for exactly one thing: telling `expired` from
	 * `cancelled`, which WooCommerce cannot distinguish. Everywhere else the
	 * shop's status wins, because the shop is where a human changes it.
	 */
	private function trait_status( WC_Order $order ): string {
		$wc     = $order->get_status();
		$stored = (string) $order->get_meta( self::META_STATUS, true );

		if ( 'cancelled' === $wc && 'expired' === $stored ) {
			return 'expired';
		}
		return self::FROM_WC[ $wc ] ?? 'pending';
	}

	/**
	 * The order this id names, or `not-found`.
	 *
	 * The id is WooCommerce's own order id with an `ord_` prefix, so an operator
	 * reading a Transcript can find the order in their admin by eye — an opaque
	 * id would make every support conversation a lookup.
	 */
	private function find( string $order_id ): WC_Order {
		$numeric = (int) preg_replace( '/^ord_/', '', $order_id );
		$order   = $numeric ? wc_get_order( $numeric ) : false;
		if ( ! $order instanceof WC_Order ) {
			throw new OpenStore_Door_Error( 'not-found', 'no such order', array( 'order_id' => $order_id ) );
		}
		return $order;
	}

	private function order_id( WC_Order $order ): string {
		return 'ord_' . $order->get_id();
	}

	private function iso( string $value ): ?string {
		if ( '' === $value ) {
			return null;
		}
		$timestamp = is_numeric( $value ) ? (int) $value : strtotime( $value );
		return $timestamp ? gmdate( 'Y-m-d\TH:i:s\Z', $timestamp ) : null;
	}
}
