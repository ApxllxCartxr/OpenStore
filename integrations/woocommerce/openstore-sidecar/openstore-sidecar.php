<?php
/**
 * Plugin Name:       OpenStore Sidecar
 * Plugin URI:        https://openstore.dev
 * Description:       Serves the OpenStore nine-door Merchant-truth trait from this WooCommerce store, so an OpenStore sidecar can make it transactable by any Buyer Agent.
 * Version:           0.1.0
 * Requires at least: 6.4
 * Requires PHP:      8.1
 * WC requires at least: 8.0
 * License:           MIT
 *
 * ---------------------------------------------------------------------------
 *
 * **Why this exists.** The trait (SPECS/PLAN.md §6.1) is ten HTTP doors with
 * HMAC signing and per-door idempotency. Implementing it by hand is a
 * multi-week integration, which means no shop owner ever will — so "makes any
 * Merchant site transactable" stayed a claim with a sample size of one. This
 * makes it an install.
 *
 * **What it is not.** It is not a sidecar. It serves Merchant truth and nothing
 * else: no Gate, no Ledger, no Authority, no receipt. The sidecar runs beside
 * this store and calls in. WooCommerce stays the book of record for stock,
 * orders and money, which is the whole posture (ADR-0001) — this plugin never
 * decides anything, it answers.
 *
 * **The private network is load-bearing.** These routes are HMAC-signed, but
 * they are also meant to be unreachable from the public internet. Door 2 hands
 * back exact stock integers, and door 7 hands back the `order_salt` once. Put
 * the sidecar and the store on the same private network, or firewall
 * `/wp-json/openstore/v1/trait/*` to the sidecar's address.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

define( 'OPENSTORE_SIDECAR_VERSION', '0.1.0' );
define( 'OPENSTORE_SIDECAR_FILE', __FILE__ );
define( 'OPENSTORE_SIDECAR_PATH', plugin_dir_path( __FILE__ ) );

require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-error.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-settings.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-hmac.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-idempotency.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-catalogue.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-pricing.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-quote.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-stock.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-orders.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-rest.php';
require_once OPENSTORE_SIDECAR_PATH . 'includes/class-openstore-admin.php';

/**
 * Refuse to load without WooCommerce rather than fataling on first request.
 *
 * Every door reads WooCommerce state; without it this plugin has nothing to
 * serve and a half-answered door is worse than an absent one.
 */
function openstore_sidecar_boot(): void {
	if ( ! class_exists( 'WooCommerce' ) ) {
		add_action(
			'admin_notices',
			static function (): void {
				echo '<div class="notice notice-error"><p><strong>OpenStore Sidecar</strong> needs WooCommerce, which is not active. Every door reads WooCommerce state, so nothing is served until it is.</p></div>';
			}
		);
		return;
	}

	OpenStore_Idempotency::install_if_needed();
	OpenStore_Stock::install_if_needed();
	( new OpenStore_REST() )->register();
	( new OpenStore_Admin() )->register();
}
add_action( 'plugins_loaded', 'openstore_sidecar_boot' );

/**
 * Create the idempotency table on activation.
 *
 * The table is the whole of "same key, same result", and a mutating door
 * without it would happily take a second hold on a retry.
 */
register_activation_hook(
	__FILE__,
	static function (): void {
		OpenStore_Idempotency::install();
		OpenStore_Stock::install();
	}
);
