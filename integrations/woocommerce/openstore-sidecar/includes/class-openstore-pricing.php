<?php
/**
 * §16.11's arithmetic, implemented a fourth time.
 *
 * There are now four independent implementations of one pinned specification:
 * the sidecar's Gate check, the conformance fake, the demo storefront's
 * TypeScript, and this. **They share no code on purpose.** If they did, the
 * Gate's `quote-consistent` check would prove only that the sidecar agrees with
 * itself.
 *
 * Two implementations that round differently fire `quote-inconsistent` on a
 * *correct* quote, which looks like a bug in the money core and is actually a
 * bug in one of them. §16.11 is the authority; where they disagree, both are
 * wrong until they agree with it.
 *
 * **There is no float in this file.** PHP integers are 64-bit on every platform
 * this plugin supports, and every division below is integer division with an
 * explicit rounding rule. A float in a money path rounds silently and the
 * rounding is invisible until a total is a paise out and the Gate refuses it.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Pricing {

	/**
	 * Step 5: extract the tax already inside an inclusive amount.
	 *
	 * `ROUND_HALF_UP`, not banker's rounding — they disagree on exactly the .5
	 * cases money hits. Done in integers: `(inclusive * rate * 2 + divisor) /
	 * (2 * divisor)` is half-up without ever touching a float.
	 */
	public static function extract_tax( int $inclusive_minor, int $rate_bp ): int {
		if ( 0 === $rate_bp ) {
			return 0;
		}
		$divisor   = 10000 + $rate_bp;
		$numerator = ( $inclusive_minor * $rate_bp * 2 ) + $divisor;
		return intdiv( $numerator, $divisor * 2 );
	}

	/**
	 * Step 4: largest remainder, stated once so it is never re-derived.
	 *
	 * Floor each raw share; the leftover paise go one each to the largest
	 * fractional parts, descending, ties broken by SKU ascending. The shares sum
	 * **exactly** to the amount — an apportionment that loses a paise is a total
	 * that does not add up, which the Gate refuses.
	 *
	 * @param array<int, array{0:string,1:int}> $weights SKU and its weight.
	 * @return array<string, int>
	 */
	public static function apportion( int $amount, array $weights ): array {
		$total  = 0;
		foreach ( $weights as $pair ) {
			$total += $pair[1];
		}

		$shares = array();
		if ( 0 === $total ) {
			foreach ( $weights as $pair ) {
				$shares[ $pair[0] ] = 0;
			}
			return $shares;
		}

		// Remainders compared as integers: `amount * w % total`, so no float
		// ever decides which line gets the spare paise.
		$remainders = array();
		$assigned   = 0;
		foreach ( $weights as $pair ) {
			list( $sku, $weight ) = $pair;
			$exact                = $amount * $weight;
			$floor                = intdiv( $exact, $total );
			$shares[ $sku ]       = $floor;
			$assigned            += $floor;
			$remainders[]         = array( $sku, $exact % $total );
		}

		usort(
			$remainders,
			static function ( array $a, array $b ): int {
				return $b[1] <=> $a[1] ?: strcmp( $a[0], $b[0] );
			}
		);

		$spare = $amount - $assigned;
		for ( $i = 0; $i < $spare; $i++ ) {
			if ( ! isset( $remainders[ $i ] ) ) {
				break;
			}
			$shares[ $remainders[ $i ][0] ] += 1;
		}

		if ( array_sum( $shares ) !== $amount ) {
			throw new OpenStore_Door_Error(
				'quote-inconsistent',
				sprintf( 'apportionment lost %d paise', $amount - array_sum( $shares ) )
			);
		}
		return $shares;
	}

	/**
	 * Step 6, the trap this section exists for. SGST takes the odd paise.
	 *
	 * @return array{0:int,1:int} CGST, then SGST.
	 */
	public static function split_cgst_sgst( int $tax ): array {
		$cgst = intdiv( $tax, 2 );
		return array( $cgst, $tax - $cgst );
	}

	public static function rate_label( int $rate_bp ): string {
		$whole = intdiv( $rate_bp, 100 );
		$frac  = $rate_bp % 100;
		return 0 === $frac ? "{$whole}%" : $whole . '.' . intdiv( $frac, 10 ) . '%';
	}

	/**
	 * The whole Quote, in §16.11's order of operations.
	 *
	 * Carries **no clock**: an ETA is a day count and never a date, or midnight
	 * turns every re-quote into a spurious `price-changed`.
	 *
	 * @param array<int, array{sku:string, qty:int, parent?:string|null}> $lines
	 * @param array<string, array{sku:string, price_minor:int, hsn_sac:string, gst_rate_bp:int, tags:string[]}> $items
	 * @param array{id:string, label:string, cost_minor:int, eta_days:int} $zone
	 * @param array{code:string, label:string, amount_minor:int}|null $discount
	 * @return array<string, mixed>
	 */
	public static function build_quote(
		array $lines,
		array $items,
		string $destination_state,
		string $home_state,
		array $zone,
		?array $discount,
		bool $tax_inclusive
	): array {
		// 1. Fold Add-ons into their parents. An Add-on is a cart line with its
		//    own SKU and Attestation, and never a Quote Line of its own: as a
		//    composite supply its amount folds into the parent's taxable value
		//    and inherits the parent's rate, HSN/SAC and Place of Supply.
		$parents = array();
		$addons  = array();
		foreach ( $lines as $line ) {
			$sku = (string) $line['sku'];
			if ( ! isset( $items[ $sku ] ) ) {
				throw new OpenStore_Door_Error( 'not-found', sprintf( 'no Catalogue Item %s', $sku ), array( 'sku' => $sku ) );
			}
			if ( in_array( 'addon', $items[ $sku ]['tags'], true ) ) {
				$parent = (string) ( $line['parent'] ?? '' );
				if ( '' === $parent ) {
					throw new OpenStore_Door_Error(
						'addon-without-parent',
						sprintf( '%s attaches to a line; it is never sold alone', $sku ),
						array( 'sku' => $sku )
					);
				}
				$addons[ $parent ][] = $line;
				continue;
			}
			$parents[ $sku ] = $line;
		}
		foreach ( array_keys( $addons ) as $parent_sku ) {
			if ( ! isset( $parents[ $parent_sku ] ) ) {
				throw new OpenStore_Door_Error(
					'addon-without-parent',
					sprintf( 'no line %s for its Add-on to attach to', $parent_sku ),
					array( 'sku' => $parent_sku )
				);
			}
		}

		$quote_lines = array();
		foreach ( $parents as $sku => $line ) {
			$item   = $items[ $sku ];
			$folded = array();
			$own    = $addons[ $sku ] ?? array();
			usort( $own, static fn( array $a, array $b ): int => strcmp( (string) $a['sku'], (string) $b['sku'] ) );
			$folded_total = 0;
			foreach ( $own as $addon ) {
				$amount       = $items[ (string) $addon['sku'] ]['price_minor'] * (int) $addon['qty'];
				$folded[]     = array(
					'sku'          => (string) $addon['sku'],
					'amount_minor' => $amount,
				);
				$folded_total += $amount;
			}

			$quote_lines[] = array(
				'sku'               => (string) $sku,
				'qty'               => (int) $line['qty'],
				'unit_price_minor'  => $item['price_minor'],
				'line_total_minor'  => ( $item['price_minor'] * (int) $line['qty'] ) + $folded_total,
				'hsn_sac'           => $item['hsn_sac'],
				'gst_rate_bp'       => $item['gst_rate_bp'],
				// Per line: the Destination state for goods, the place of
				// performance for a service sold at the premises. This is what
				// lets one basket carry IGST on one line and CGST/SGST on
				// another.
				'place_of_supply'   => in_array( 'service', $item['tags'], true ) ? $home_state : $destination_state,
				'addons'            => $folded,
			);
		}
		usort( $quote_lines, static fn( array $a, array $b ): int => strcmp( $a['sku'], $b['sku'] ) );

		$subtotal = 0;
		$weights  = array();
		foreach ( $quote_lines as $line ) {
			$subtotal  += $line['line_total_minor'];
			$weights[]  = array( $line['sku'], $line['line_total_minor'] );
		}

		// 2–3. Discount, then fulfillment. Both apportion on the same basis: the
		//      line inclusive total from step 1.
		$discount_lines   = $discount ? array( $discount ) : array();
		$discount_shares  = $discount ? self::apportion( abs( $discount['amount_minor'] ), $weights ) : array();
		$shipping_shares  = self::apportion( $zone['cost_minor'], $weights );

		// 5–6. Extract tax per line, then split by Place of Supply.
		$taxes = array();
		$add   = static function ( string $kind, int $rate_bp, int $amount ) use ( &$taxes ): void {
			$key           = "{$kind}:{$rate_bp}";
			$taxes[ $key ] = array(
				'kind'    => $kind,
				'rate_bp' => $rate_bp,
				'amount'  => ( $taxes[ $key ]['amount'] ?? 0 ) + $amount,
			);
		};

		foreach ( $quote_lines as $line ) {
			$inclusive = $line['line_total_minor']
				- ( $discount_shares[ $line['sku'] ] ?? 0 )
				+ ( $shipping_shares[ $line['sku'] ] ?? 0 );
			$tax       = self::extract_tax( $inclusive, $line['gst_rate_bp'] );
			if ( $line['place_of_supply'] === $home_state ) {
				list( $cgst, $sgst ) = self::split_cgst_sgst( $tax );
				$add( 'CGST', intdiv( $line['gst_rate_bp'], 2 ), $cgst );
				$add( 'SGST', intdiv( $line['gst_rate_bp'], 2 ), $sgst );
			} else {
				$add( 'IGST', $line['gst_rate_bp'], $tax );
			}
		}

		$tax_values = array_values( $taxes );
		usort(
			$tax_values,
			static function ( array $a, array $b ): int {
				return strcmp( $a['kind'], $b['kind'] ) ?: ( $a['rate_bp'] <=> $b['rate_bp'] );
			}
		);
		$tax_lines = array();
		foreach ( $tax_values as $tax ) {
			$tax_lines[] = array(
				'kind'          => $tax['kind'],
				'label'         => $tax['kind'] . ' ' . self::rate_label( $tax['rate_bp'] ),
				'rate_bp'       => $tax['rate_bp'],
				'amount_minor'  => $tax['amount'],
				// Tax-inclusive prices mean tax is reported and never added.
				// Getting this backwards double-charges every order.
				'informational' => $tax_inclusive,
			);
		}

		$discount_total = 0;
		foreach ( $discount_lines as $line ) {
			$discount_total += $line['amount_minor'];
		}

		return array(
			'currency'            => 'INR',
			'subtotal_minor'      => $subtotal,
			'lines'               => $quote_lines,
			'discount_lines'      => $discount_lines,
			'fulfillment_options' => array(
				array(
					'id'         => $zone['id'],
					'label'      => $zone['label'],
					'cost_minor' => $zone['cost_minor'],
					'eta_days'   => $zone['eta_days'],
				),
			),
			'fulfillment_chosen'  => array(
				'id'         => $zone['id'],
				'cost_minor' => $zone['cost_minor'],
			),
			'tax_lines'           => $tax_lines,
			// 7. Always 0 in v1. A non-zero value is a bug, not a feature.
			'round_off_minor'     => 0,
			// 8. subtotal + fulfillment + discounts (negative) + round_off.
			'total_minor'         => $subtotal + $zone['cost_minor'] + $discount_total,
			'tax_inclusive'       => $tax_inclusive,
		);
	}
}
