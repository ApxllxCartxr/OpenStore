<?php
/**
 * Doors 3–6: the hold, and the three ways it ends.
 *
 * **The compare-and-set is the whole mechanism.** `UPDATE ... WHERE stock >=
 * qty` inside one transaction is what makes two agents racing for the last item
 * produce one sale and one `sold-out`, rather than two sales and an oversell
 * the shop finds out about at packing time. WooCommerce's own
 * `wc_update_product_stock` does a bare decrement with no floor, so it is not
 * used for the reserve: it would happily take stock to -1.
 *
 * Every hold is recorded in its own table rather than inferred from order meta,
 * because door 5 has to release exactly what door 3 took — including when the
 * order was edited in WooCommerce admin in between.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Stock {

	public const HOLDS_TABLE_VERSION_OPTION = 'openstore_holds_schema';
	private const SCHEMA_VERSION            = 1;

	public function __construct( private OpenStore_Catalogue $catalogue ) {}

	public static function table(): string {
		global $wpdb;
		return $wpdb->prefix . 'openstore_holds';
	}

	public static function install(): void {
		global $wpdb;
		require_once ABSPATH . 'wp-admin/includes/upgrade.php';

		$table   = self::table();
		$collate = $wpdb->get_charset_collate();

		dbDelta(
			"CREATE TABLE {$table} (
				id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
				order_id VARCHAR(64) NOT NULL,
				product_id BIGINT UNSIGNED NOT NULL,
				sku VARCHAR(191) NOT NULL,
				qty INT NOT NULL,
				state VARCHAR(16) NOT NULL,
				created_at DATETIME NOT NULL,
				PRIMARY KEY (id),
				KEY order_state (order_id, state)
			) {$collate};"
		);

		update_option( self::HOLDS_TABLE_VERSION_OPTION, self::SCHEMA_VERSION, false );
	}

	public static function install_if_needed(): void {
		if ( (int) get_option( self::HOLDS_TABLE_VERSION_OPTION, 0 ) !== self::SCHEMA_VERSION ) {
			self::install();
		}
	}

	/**
	 * Door 3 — atomic compare-and-set, all or nothing.
	 *
	 * @param array<int, array{sku:string, qty:int}> $lines
	 * @return array{reserved:true}
	 */
	public function reserve( string $order_id, array $lines, ?string $discount_code ): array {
		global $wpdb;

		// Quantities accumulate per SKU before anything is taken: two lines of
		// one SKU are one hold of the sum, or the second `WHERE stock >= qty`
		// checks against a count the first already moved.
		$wanted = array();
		foreach ( $lines as $line ) {
			$product                               = $this->catalogue->resolve( (string) $line['sku'] );
			$sku                                   = (string) $product->get_sku();
			$wanted[ $sku ]                        = ( $wanted[ $sku ] ?? array( 'qty' => 0 ) );
			$wanted[ $sku ]['qty']                += (int) $line['qty'];
			$wanted[ $sku ]['product_id']          = $product->get_id();
		}
		// Sorted, so two concurrent baskets touching the same two SKUs take them
		// in the same order and deadlock rather than interleave into an oversell.
		ksort( $wanted );

		$wpdb->query( 'START TRANSACTION' );
		try {
			foreach ( $wanted as $sku => $want ) {
				$this->take( (string) $sku, (int) $want['product_id'], (int) $want['qty'], $order_id );
			}
			if ( $discount_code ) {
				$this->hold_code( $discount_code, $order_id );
			}
			$wpdb->query( 'COMMIT' );
		} catch ( Throwable $error ) {
			$wpdb->query( 'ROLLBACK' );
			throw $error;
		}

		foreach ( $wanted as $want ) {
			$this->refresh( (int) $want['product_id'] );
		}
		return array( 'reserved' => true );
	}

	/**
	 * Take one SKU's stock, or refuse with the count that is actually there.
	 *
	 * The `WHERE` clause is the lock. Nothing here reads the count and then
	 * decides — a read-then-write is exactly the race two agents win together.
	 */
	private function take( string $sku, int $product_id, int $qty, string $order_id ): void {
		global $wpdb;

		$updated = $wpdb->query(
			$wpdb->prepare(
				"UPDATE {$wpdb->postmeta} SET meta_value = CAST(meta_value AS SIGNED) - %d
				  WHERE post_id = %d AND meta_key = '_stock' AND CAST(meta_value AS SIGNED) >= %d",
				$qty,
				$product_id,
				$qty
			)
		);

		if ( ! $updated ) {
			$available = (int) $wpdb->get_var(
				$wpdb->prepare(
					"SELECT CAST(meta_value AS SIGNED) FROM {$wpdb->postmeta} WHERE post_id = %d AND meta_key = '_stock'",
					$product_id
				)
			);
			// The one place a count is named to an agent, because "try fewer"
			// without a number is not a fix (SPEC §5).
			throw new OpenStore_Door_Error(
				'sold-out',
				$available > 0 ? sprintf( 'Only %d left; try %d or fewer.', $available, $available ) : 'Sold out.',
				array(
					'sku'       => $sku,
					'requested' => $qty,
					'available' => max( 0, $available ),
				)
			);
		}

		$wpdb->insert(
			self::table(),
			array(
				'order_id'   => $order_id,
				'product_id' => $product_id,
				'sku'        => $sku,
				'qty'        => $qty,
				'state'      => 'held',
				'created_at' => current_time( 'mysql', true ),
			),
			array( '%s', '%d', '%s', '%d', '%s', '%s' )
		);
	}

	/**
	 * Door 4 — the hold becomes a sale.
	 *
	 * The stock is already gone; committing only closes the hold, so there is
	 * nothing to move and everything to record.
	 *
	 * @return array{committed:true}
	 */
	public function commit( string $order_id ): array {
		global $wpdb;

		$held = (int) $wpdb->get_var(
			$wpdb->prepare( 'SELECT COUNT(*) FROM ' . self::table() . ' WHERE order_id = %s AND state = %s', $order_id, 'held' )
		);
		$committed = (int) $wpdb->get_var(
			$wpdb->prepare( 'SELECT COUNT(*) FROM ' . self::table() . ' WHERE order_id = %s AND state = %s', $order_id, 'committed' )
		);
		if ( 0 === $held ) {
			// Already committed is not an error: door 4 is idempotent by key and
			// by state, and a retry must see the first attempt's answer.
			if ( $committed > 0 ) {
				return array( 'committed' => true );
			}
			throw new OpenStore_Door_Error( 'no-hold', sprintf( 'no hold to commit for %s', $order_id ) );
		}

		$wpdb->update(
			self::table(),
			array( 'state' => 'committed' ),
			array(
				'order_id' => $order_id,
				'state'    => 'held',
			),
			array( '%s' ),
			array( '%s', '%s' )
		);
		$this->consume_code( $order_id );
		return array( 'committed' => true );
	}

	/**
	 * Door 5 — the hold goes back.
	 *
	 * Releasing a hold that was never taken refuses `no-hold`. A `sold-out`
	 * failure took no hold, and accepting a release for it would make the
	 * sidecar's escrow-zero invariant close on an entry that balances nothing.
	 *
	 * @return array{released:true}
	 */
	public function release( string $order_id ): array {
		global $wpdb;

		$rows = $wpdb->get_results(
			$wpdb->prepare(
				'SELECT product_id, sku, qty FROM ' . self::table() . ' WHERE order_id = %s AND state = %s',
				$order_id,
				'held'
			),
			ARRAY_A
		);

		if ( ! $rows ) {
			$released = (int) $wpdb->get_var(
				$wpdb->prepare( 'SELECT COUNT(*) FROM ' . self::table() . ' WHERE order_id = %s AND state = %s', $order_id, 'released' )
			);
			if ( $released > 0 ) {
				return array( 'released' => true );
			}
			throw new OpenStore_Door_Error( 'no-hold', sprintf( 'no hold to release for %s', $order_id ) );
		}

		$wpdb->query( 'START TRANSACTION' );
		try {
			foreach ( $rows as $row ) {
				$wpdb->query(
					$wpdb->prepare(
						"UPDATE {$wpdb->postmeta} SET meta_value = CAST(meta_value AS SIGNED) + %d
						  WHERE post_id = %d AND meta_key = '_stock'",
						(int) $row['qty'],
						(int) $row['product_id']
					)
				);
			}
			$wpdb->update(
				self::table(),
				array( 'state' => 'released' ),
				array(
					'order_id' => $order_id,
					'state'    => 'held',
				),
				array( '%s' ),
				array( '%s', '%s' )
			);
			$this->release_code( $order_id );
			$wpdb->query( 'COMMIT' );
		} catch ( Throwable $error ) {
			$wpdb->query( 'ROLLBACK' );
			throw $error;
		}

		foreach ( $rows as $row ) {
			$this->refresh( (int) $row['product_id'] );
		}
		return array( 'released' => true );
	}

	/**
	 * Door 6 — stock returned after the fact, which is a different act from a
	 * release: nothing was held, and the count goes up because a parcel came
	 * back.
	 *
	 * @param array<int, array{sku:string, qty:int}> $lines
	 * @return array{restocked:true}
	 */
	public function restock( string $order_id, array $lines ): array {
		global $wpdb;

		$wpdb->query( 'START TRANSACTION' );
		try {
			foreach ( $lines as $line ) {
				$product = $this->catalogue->resolve( (string) $line['sku'] );
				$wpdb->query(
					$wpdb->prepare(
						"UPDATE {$wpdb->postmeta} SET meta_value = CAST(meta_value AS SIGNED) + %d
						  WHERE post_id = %d AND meta_key = '_stock'",
						(int) $line['qty'],
						$product->get_id()
					)
				);
				$wpdb->insert(
					self::table(),
					array(
						'order_id'   => $order_id,
						'product_id' => $product->get_id(),
						'sku'        => (string) $product->get_sku(),
						'qty'        => (int) $line['qty'],
						'state'      => 'restocked',
						'created_at' => current_time( 'mysql', true ),
					),
					array( '%s', '%d', '%s', '%d', '%s', '%s' )
				);
				$this->refresh( $product->get_id() );
			}
			$wpdb->query( 'COMMIT' );
		} catch ( Throwable $error ) {
			$wpdb->query( 'ROLLBACK' );
			throw $error;
		}

		return array( 'restocked' => true );
	}

	/**
	 * Tell WooCommerce the number moved underneath it.
	 *
	 * The UPDATE above goes straight to `postmeta`, which is the only way to get
	 * a conditional decrement. WooCommerce keeps a lookup table and several
	 * caches beside it, and a shop whose admin shows a stale count is a shop
	 * whose owner does not trust the integration.
	 */
	private function refresh( int $product_id ): void {
		wp_cache_delete( $product_id, 'post_meta' );
		$product = wc_get_product( $product_id );
		if ( $product instanceof WC_Product ) {
			wc_update_product_stock_status(
				$product_id,
				( (int) $product->get_stock_quantity() ) > 0 ? 'instock' : 'outofstock'
			);
			wc_delete_product_transients( $product_id );
			if ( function_exists( 'wc_update_product_lookup_tables_column' ) ) {
				$GLOBALS['wpdb']->update(
					$GLOBALS['wpdb']->prefix . 'wc_product_meta_lookup',
					array( 'stock_quantity' => (int) $product->get_stock_quantity() ),
					array( 'product_id' => $product_id ),
					array( '%d' ),
					array( '%d' )
				);
			}
			do_action( 'woocommerce_updated_product_stock', $product_id );
		}
	}

	/**
	 * Hold a Discount Code for this order, once.
	 *
	 * Under the same idempotency key as the stock, so a single-use code cannot
	 * be spent twice by a retry — and the `held` row is what door 5 gives back.
	 */
	private function hold_code( string $code, string $order_id ): void {
		global $wpdb;

		$coupon = new WC_Coupon( $code );
		if ( ! $coupon->get_id() ) {
			throw new OpenStore_Door_Error( 'code-invalid', 'That code is not valid.' );
		}
		if ( $coupon->get_usage_limit() && $coupon->get_usage_count() >= $coupon->get_usage_limit() ) {
			throw new OpenStore_Door_Error( 'code-invalid', 'That code is not valid.' );
		}

		$wpdb->insert(
			self::table(),
			array(
				'order_id'   => $order_id,
				'product_id' => 0,
				'sku'        => 'coupon:' . $coupon->get_code(),
				'qty'        => 0,
				'state'      => 'held',
				'created_at' => current_time( 'mysql', true ),
			),
			array( '%s', '%d', '%s', '%d', '%s', '%s' )
		);
		$coupon->set_usage_count( $coupon->get_usage_count() + 1 );
		$coupon->save();
	}

	private function consume_code( string $order_id ): void {
		// The usage count was already taken at reserve; committing only means it
		// is not coming back.
	}

	private function release_code( string $order_id ): void {
		global $wpdb;
		$rows = $wpdb->get_results(
			$wpdb->prepare(
				'SELECT sku FROM ' . self::table() . " WHERE order_id = %s AND sku LIKE 'coupon:%%'",
				$order_id
			),
			ARRAY_A
		);
		foreach ( $rows as $row ) {
			$coupon = new WC_Coupon( substr( (string) $row['sku'], strlen( 'coupon:' ) ) );
			if ( $coupon->get_id() && $coupon->get_usage_count() > 0 ) {
				$coupon->set_usage_count( $coupon->get_usage_count() - 1 );
				$coupon->save();
			}
		}
	}
}
