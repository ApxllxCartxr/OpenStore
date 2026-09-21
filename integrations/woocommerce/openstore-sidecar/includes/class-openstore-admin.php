<?php
/**
 * One settings screen, and an honest readiness list.
 *
 * The list is the point. A Merchant installing this wants to know whether their
 * shop can actually be sold from, and the two ways it silently cannot —
 * products with no SKU, products with stock management off — are invisible in
 * WooCommerce's own screens. So they are counted here, by name.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Admin {

	public function register(): void {
		add_action( 'admin_menu', array( $this, 'menu' ) );
		add_action( 'admin_init', array( $this, 'settings' ) );
		add_action( 'woocommerce_product_options_general_product_data', array( $this, 'product_fields' ) );
		add_action( 'woocommerce_process_product_meta', array( $this, 'save_product_fields' ) );
	}

	public function menu(): void {
		add_submenu_page(
			'woocommerce',
			'OpenStore',
			'OpenStore',
			'manage_woocommerce',
			'openstore-sidecar',
			array( $this, 'render' )
		);
	}

	public function settings(): void {
		foreach (
			array(
				OpenStore_Settings::OPTION_SECRET,
				OpenStore_Settings::OPTION_HOME_STATE,
				OpenStore_Settings::OPTION_DEFAULT_HSN,
				OpenStore_Settings::OPTION_DEFAULT_RATE,
				OpenStore_Settings::OPTION_UNSERVICEABLE,
			) as $option
		) {
			register_setting( 'openstore_sidecar', $option );
		}
	}

	public function render(): void {
		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			return;
		}
		$report = $this->readiness();
		?>
		<div class="wrap">
			<h1>OpenStore Sidecar</h1>
			<p>
				This store serves the OpenStore trait at
				<code><?php echo esc_html( rest_url( OpenStore_REST::NAMESPACE . '/trait/' ) ); ?></code>.
				<strong>Keep it on a private network.</strong> Door 2 answers with exact stock
				counts and door 7 answers once with an order's salt; both are meant for a
				sidecar you run, not for the internet.
			</p>

			<h2>Readiness</h2>
			<table class="widefat striped" style="max-width:48rem">
				<tbody>
				<?php foreach ( $report as $row ) : ?>
					<tr>
						<td style="width:12rem"><strong><?php echo esc_html( $row['label'] ); ?></strong></td>
						<td><?php echo esc_html( $row['detail'] ); ?></td>
					</tr>
				<?php endforeach; ?>
				</tbody>
			</table>

			<h2>Settings</h2>
			<form method="post" action="options.php">
				<?php settings_fields( 'openstore_sidecar' ); ?>
				<table class="form-table" role="presentation">
					<tr>
						<th scope="row"><label for="openstore_secret">Trait HMAC secret</label></th>
						<td>
							<input name="<?php echo esc_attr( OpenStore_Settings::OPTION_SECRET ); ?>"
								id="openstore_secret" type="text" class="regular-text"
								value="<?php echo esc_attr( (string) get_option( OpenStore_Settings::OPTION_SECRET, '' ) ); ?>">
							<p class="description">
								The same value as the sidecar's <code>TRAIT_HMAC_SECRET</code>. With no
								secret set, every door is refused — there is no unauthenticated path.
							</p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="openstore_state">GST registration state</label></th>
						<td>
							<input name="<?php echo esc_attr( OpenStore_Settings::OPTION_HOME_STATE ); ?>"
								id="openstore_state" type="text" maxlength="2" size="4"
								value="<?php echo esc_attr( (string) get_option( OpenStore_Settings::OPTION_HOME_STATE, '' ) ); ?>">
							<p class="description">
								Two letters, e.g. <code>KA</code>. This is what decides CGST/SGST from
								IGST, so it is never guessed: quotes refuse until it is set.
							</p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="openstore_hsn">Default HSN/SAC</label></th>
						<td>
							<input name="<?php echo esc_attr( OpenStore_Settings::OPTION_DEFAULT_HSN ); ?>"
								id="openstore_hsn" type="text" class="regular-text"
								value="<?php echo esc_attr( OpenStore_Settings::default_hsn() ); ?>">
							<p class="description">Used where a product has none of its own.</p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="openstore_rate">Default GST rate (basis points)</label></th>
						<td>
							<input name="<?php echo esc_attr( OpenStore_Settings::OPTION_DEFAULT_RATE ); ?>"
								id="openstore_rate" type="number" min="0" max="10000"
								value="<?php echo esc_attr( (string) OpenStore_Settings::default_rate_bp() ); ?>">
							<p class="description">1800 is 18%. Basis points, so no rate is ever a float.</p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="openstore_unserviceable">Unserviceable PIN prefixes</label></th>
						<td>
							<textarea name="<?php echo esc_attr( OpenStore_Settings::OPTION_UNSERVICEABLE ); ?>"
								id="openstore_unserviceable" class="large-text" rows="2"><?php
								echo esc_textarea( (string) get_option( OpenStore_Settings::OPTION_UNSERVICEABLE, '' ) );
							?></textarea>
							<p class="description">
								Space- or comma-separated. A Destination starting with one of these
								refuses <code>destination-unserviceable</code> before it is priced.
							</p>
						</td>
					</tr>
				</table>
				<?php submit_button(); ?>
			</form>
		</div>
		<?php
	}

	/**
	 * What a Merchant needs to know before they believe this works.
	 *
	 * @return array<int, array{label:string, detail:string}>
	 */
	private function readiness(): array {
		global $wpdb;

		$no_sku = (int) $wpdb->get_var(
			"SELECT COUNT(*) FROM {$wpdb->posts} p
			  LEFT JOIN {$wpdb->postmeta} m ON m.post_id = p.ID AND m.meta_key = '_sku'
			 WHERE p.post_type IN ('product','product_variation') AND p.post_status = 'publish'
			   AND (m.meta_value IS NULL OR m.meta_value = '')"
		);
		$unmanaged = (int) $wpdb->get_var(
			"SELECT COUNT(*) FROM {$wpdb->posts} p
			  LEFT JOIN {$wpdb->postmeta} m ON m.post_id = p.ID AND m.meta_key = '_manage_stock'
			 WHERE p.post_type IN ('product','product_variation') AND p.post_status = 'publish'
			   AND (m.meta_value IS NULL OR m.meta_value != 'yes')"
		);

		$state = (string) get_option( OpenStore_Settings::OPTION_HOME_STATE, '' );

		return array(
			array(
				'label'  => 'Secret',
				'detail' => OpenStore_Settings::has_secret()
					? 'Set. Every door verifies against it.'
					: 'NOT SET — every door is refused until it is.',
			),
			array(
				'label'  => 'Registration state',
				'detail' => preg_match( '/^[A-Z]{2}$/', strtoupper( $state ) )
					? strtoupper( $state )
					: 'NOT SET — quotes refuse, because CGST/SGST cannot be told from IGST.',
			),
			array(
				'label'  => 'Prices include tax',
				'detail' => wc_prices_include_tax()
					? 'Yes. Tax lines are reported and never added.'
					: 'No. Tax lines are added to the total.',
			),
			array(
				'label'  => 'Products with no SKU',
				'detail' => $no_sku
					? sprintf( '%d — not published to agents. A SKU is what a cart line refers to.', $no_sku )
					: 'None.',
			),
			array(
				'label'  => 'Products not managing stock',
				'detail' => $unmanaged
					? sprintf( '%d — not published to agents. Stock is a count, never "in stock" with no number.', $unmanaged )
					: 'None.',
			),
		);
	}

	/** Per-product HSN/SAC and GST rate, on the product's own General tab. */
	public function product_fields(): void {
		woocommerce_wp_text_input(
			array(
				'id'          => OpenStore_Settings::META_HSN,
				'label'       => 'HSN/SAC',
				'description' => 'Goes on the invoice for this item. Falls back to the OpenStore default.',
				'desc_tip'    => true,
			)
		);
		woocommerce_wp_text_input(
			array(
				'id'                => OpenStore_Settings::META_RATE,
				'label'             => 'GST rate (basis points)',
				'description'       => '1800 is 18%. Basis points, so no rate is ever a float.',
				'desc_tip'          => true,
				'type'              => 'number',
				'custom_attributes' => array(
					'min' => '0',
					'max' => '10000',
				),
			)
		);
		woocommerce_wp_text_input(
			array(
				'id'          => OpenStore_Settings::META_TAGS,
				'label'       => 'OpenStore tags',
				'description' => 'Comma-separated. `addon` folds this item into a parent line; `service` moves its Place of Supply to your own state.',
				'desc_tip'    => true,
			)
		);
	}

	public function save_product_fields( int $product_id ): void {
		if ( ! current_user_can( 'edit_product', $product_id ) ) {
			return;
		}
		// Nonce-checked by WooCommerce's own product save before this fires.
		$product = wc_get_product( $product_id );
		if ( ! $product instanceof WC_Product ) {
			return;
		}
		foreach (
			array(
				OpenStore_Settings::META_HSN,
				OpenStore_Settings::META_RATE,
				OpenStore_Settings::META_TAGS,
			) as $meta
		) {
			if ( isset( $_POST[ $meta ] ) ) { // phpcs:ignore WordPress.Security.NonceVerification.Missing
				$product->update_meta_data( $meta, sanitize_text_field( wp_unslash( (string) $_POST[ $meta ] ) ) ); // phpcs:ignore WordPress.Security.NonceVerification.Missing
			}
		}
		$product->save();
	}
}
