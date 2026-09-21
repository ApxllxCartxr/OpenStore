<?php
/**
 * WooCommerce's model, in the trait's vocabulary.
 *
 * The mapping is the whole plugin, and it is one sentence long:
 *
 * - A **Product Group** is a WooCommerce *variable* product. Presentation only:
 *   never sellable, never reserved, never a cart line.
 * - A **Catalogue Item** is a WooCommerce *variation*, or a *simple* product,
 *   which is its own group of one. It owns the SKU, the price, the stock, the
 *   HSN/SAC and the GST rate.
 *
 * **Two WooCommerce habits are refused rather than accommodated:**
 *
 * 1. *An item with no SKU is not published.* The SKU is what a cart line, a
 *    reservation and an Attestation all refer to; an item without one cannot be
 *    referred to at all, and inventing an id from the post ID would produce a
 *    reference that changes when the shop is migrated.
 * 2. *An item with stock management off is not published.* Stock is the present
 *    integer count, always set and never empty. WooCommerce's "in stock" with
 *    no number is exactly the null this spec refuses, and treating it as
 *    infinite is how an agent sells something that does not exist.
 *
 * Both are reported in WooCommerce → OpenStore, so a Merchant sees what is
 * missing rather than wondering why an agent cannot find their bestseller.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Catalogue {

	/** WooCommerce statuses that are agent-visible at all. */
	private const PUBLISHED = array( 'publish' );

	/**
	 * Door 1. Groups and items, both sorted, both presentation-complete.
	 *
	 * @return array{groups: array<int, array<string, mixed>>, items: array<int, array<string, mixed>>}
	 */
	public function catalog_read(): array {
		$groups = array();
		$items  = array();

		foreach ( $this->published_products() as $product ) {
			if ( $product->is_type( 'variable' ) ) {
				$groups[] = $this->group_from( $product );
				foreach ( $product->get_children() as $variation_id ) {
					$variation = wc_get_product( $variation_id );
					if ( $variation && $this->is_sellable( $variation ) ) {
						$items[] = $this->item_from( $variation, (string) $product->get_id() );
					}
				}
				continue;
			}

			if ( $this->is_sellable( $product ) ) {
				// A simple product is its own group of one. The group still
				// exists, because an agent reads a page before it reads a SKU.
				$groups[] = $this->group_from( $product );
				$items[]  = $this->item_from( $product, (string) $product->get_id() );
			}
		}

		usort( $groups, static fn( array $a, array $b ): int => strcmp( $a['id'], $b['id'] ) );
		usort( $items, static fn( array $a, array $b ): int => strcmp( $a['sku'], $b['sku'] ) );

		return array(
			'groups' => $groups,
			'items'  => $items,
		);
	}

	/**
	 * Door 2 — **exact integers, private network only.**
	 *
	 * @param string[] $skus
	 * @return array{stock: array<string, int>}
	 */
	public function stock_read( array $skus ): array {
		$stock = array();
		foreach ( $skus as $sku ) {
			$product = $this->resolve( (string) $sku );
			$count   = $product->get_stock_quantity();
			if ( null === $count ) {
				// Missing fails loud: a silent 0 reads as "sold out" for an item
				// nobody has counted, and a silent large number sells air.
				throw new OpenStore_Door_Error(
					'not-found',
					sprintf( '%s has no stock count. Turn on "Manage stock" for it in WooCommerce.', $sku ),
					array( 'sku' => (string) $sku )
				);
			}
			$stock[ (string) $sku ] = max( 0, (int) $count );
		}
		return array( 'stock' => $stock );
	}

	/**
	 * The Catalogue Item for a SKU, or the refusal that says why not.
	 *
	 * A Product Group id refuses `variant-required` rather than having a size
	 * guessed for it — a guess wearing a helpful expression.
	 *
	 * @throws OpenStore_Door_Error
	 */
	public function resolve( string $sku ): WC_Product {
		$product_id = wc_get_product_id_by_sku( $sku );

		if ( ! $product_id ) {
			// A variable product's own id, offered where a SKU belongs.
			$as_group = is_numeric( $sku ) ? wc_get_product( (int) $sku ) : null;
			if ( $as_group instanceof WC_Product && $as_group->is_type( 'variable' ) ) {
				throw new OpenStore_Door_Error(
					'variant-required',
					sprintf( '%s is a Product Group; choose one of its Catalogue Items', $sku ),
					array(
						'group_id' => $sku,
						'axes'     => $this->option_axes( $as_group ),
					)
				);
			}
			throw new OpenStore_Door_Error(
				'not-found',
				sprintf( 'no Catalogue Item %s', $sku ),
				array( 'sku' => $sku )
			);
		}

		$product = wc_get_product( $product_id );
		if ( ! $product instanceof WC_Product ) {
			throw new OpenStore_Door_Error( 'not-found', sprintf( 'no Catalogue Item %s', $sku ), array( 'sku' => $sku ) );
		}
		if ( $product->is_type( 'variable' ) ) {
			throw new OpenStore_Door_Error(
				'variant-required',
				sprintf( '%s is a Product Group; choose one of its Catalogue Items', $sku ),
				array(
					'group_id' => $sku,
					'axes'     => $this->option_axes( $product ),
				)
			);
		}
		return $product;
	}

	/**
	 * Price in paise, as an integer.
	 *
	 * WooCommerce keeps prices as decimal strings. The conversion is the one
	 * place a float could sneak into a money path, so it goes through a string
	 * and `round`, never through `floatval * 100` alone — `19.99 * 100` is
	 * `1998.9999999999998`, which truncates to a rupee short.
	 */
	public static function price_minor( WC_Product $product ): int {
		$price = $product->get_price( 'edit' );
		if ( '' === $price || null === $price ) {
			throw new OpenStore_Door_Error(
				'quote-inconsistent',
				sprintf( '%s has no price set', (string) $product->get_sku() ),
				array( 'sku' => (string) $product->get_sku() )
			);
		}
		return (int) round( ( (float) $price ) * 100 );
	}

	/** The GST rate for one item, in basis points. */
	public static function gst_rate_bp( WC_Product $product ): int {
		$own = $product->get_meta( OpenStore_Settings::META_RATE, true );
		if ( '' === $own || null === $own ) {
			$parent = $product->get_parent_id() ? wc_get_product( $product->get_parent_id() ) : null;
			$own    = $parent ? $parent->get_meta( OpenStore_Settings::META_RATE, true ) : '';
		}
		if ( '' === $own || null === $own ) {
			$own = OpenStore_Settings::default_rate_bp();
		}
		return (int) $own;
	}

	/** The HSN/SAC for one item. Refused rather than blanked: it is on the invoice. */
	public static function hsn_sac( WC_Product $product ): string {
		$own = (string) $product->get_meta( OpenStore_Settings::META_HSN, true );
		if ( '' === $own && $product->get_parent_id() ) {
			$parent = wc_get_product( $product->get_parent_id() );
			$own    = $parent ? (string) $parent->get_meta( OpenStore_Settings::META_HSN, true ) : '';
		}
		if ( '' === $own ) {
			$own = OpenStore_Settings::default_hsn();
		}
		if ( '' === $own ) {
			throw new OpenStore_Door_Error(
				'quote-inconsistent',
				sprintf(
					'%s has no HSN/SAC, and this store has no default. Set one on the product or under WooCommerce → OpenStore.',
					(string) $product->get_sku()
				),
				array( 'sku' => (string) $product->get_sku() )
			);
		}
		return $own;
	}

	/**
	 * The trait's tags for one item.
	 *
	 * WooCommerce product tags, plus the two the money core actually reads —
	 * `addon` and `service` — which are kept in their own meta so a shop's
	 * marketing tags cannot accidentally fold a product into another line or
	 * move its Place of Supply.
	 *
	 * @return string[]
	 */
	public static function tags( WC_Product $product ): array {
		$tags = array();
		foreach ( wc_get_product_terms( $product->get_parent_id() ?: $product->get_id(), 'product_tag', array( 'fields' => 'slugs' ) ) as $slug ) {
			$tags[] = (string) $slug;
		}
		$structural = $product->get_meta( OpenStore_Settings::META_TAGS, true );
		if ( ! $structural && $product->get_parent_id() ) {
			$parent     = wc_get_product( $product->get_parent_id() );
			$structural = $parent ? $parent->get_meta( OpenStore_Settings::META_TAGS, true ) : '';
		}
		foreach ( is_array( $structural ) ? $structural : array_filter( array_map( 'trim', explode( ',', (string) $structural ) ) ) as $tag ) {
			$tags[] = (string) $tag;
		}
		$tags = array_values( array_unique( $tags ) );
		sort( $tags );
		return $tags;
	}

	/** An item is sellable when it has a SKU and a real stock count. */
	private function is_sellable( WC_Product $product ): bool {
		return '' !== (string) $product->get_sku()
			&& $product->managing_stock()
			&& null !== $product->get_stock_quantity();
	}

	/** @return WC_Product[] */
	private function published_products(): array {
		$products = wc_get_products(
			array(
				'status'  => self::PUBLISHED,
				'limit'   => -1,
				'orderby' => 'ID',
				'order'   => 'ASC',
			)
		);
		return is_array( $products ) ? $products : array();
	}

	/** @return array<string, mixed> */
	private function group_from( WC_Product $product ): array {
		return array(
			'id'          => (string) $product->get_id(),
			'slug'        => (string) $product->get_slug(),
			'name'        => (string) $product->get_name(),
			'description' => wp_strip_all_tags( (string) $product->get_short_description() ),
			'media'       => $this->media_for( $product ),
			'option_axes' => $this->option_axes( $product ),
			'tags'        => self::tags( $product ),
			'status'      => 'active',
		);
	}

	/** @return array<string, mixed> */
	private function item_from( WC_Product $product, string $group_id ): array {
		return array(
			'sku'                 => (string) $product->get_sku(),
			'group_id'            => $group_id,
			'options'             => $this->options_of( $product ),
			'name'                => (string) $product->get_name(),
			'price_minor'         => self::price_minor( $product ),
			'tags'                => self::tags( $product ),
			'media'               => $this->media_for( $product ),
			'status'              => 'active',
			'low_stock_threshold' => (int) ( $product->get_low_stock_amount() ?: get_option( 'woocommerce_notify_low_stock_amount', 2 ) ),
			'hsn_sac'             => self::hsn_sac( $product ),
			'gst_rate_bp'         => self::gst_rate_bp( $product ),
		);
	}

	/** @return array<string, string[]> */
	private function option_axes( WC_Product $product ): array {
		$axes = array();
		foreach ( $product->get_attributes() as $name => $attribute ) {
			$values = $attribute instanceof WC_Product_Attribute ? $attribute->get_options() : (array) $attribute;
			if ( $attribute instanceof WC_Product_Attribute && $attribute->is_taxonomy() ) {
				$terms  = wc_get_product_terms( $product->get_id(), $attribute->get_name(), array( 'fields' => 'names' ) );
				$values = is_array( $terms ) ? $terms : array();
			}
			$axes[ wc_attribute_label( (string) $name ) ] = array_values( array_map( 'strval', $values ) );
		}
		return $axes;
	}

	/** @return array<string, string> */
	private function options_of( WC_Product $product ): array {
		if ( ! $product instanceof WC_Product_Variation ) {
			return array();
		}
		$options = array();
		foreach ( $product->get_variation_attributes() as $name => $value ) {
			$options[ wc_attribute_label( str_replace( 'attribute_', '', (string) $name ) ) ] = (string) $value;
		}
		return $options;
	}

	/** @return string[] */
	private function media_for( WC_Product $product ): array {
		$urls  = array();
		$image = $product->get_image_id() ? wp_get_attachment_url( (int) $product->get_image_id() ) : '';
		if ( $image ) {
			$urls[] = (string) $image;
		}
		foreach ( $product->get_gallery_image_ids() as $id ) {
			$url = wp_get_attachment_url( (int) $id );
			if ( $url ) {
				$urls[] = (string) $url;
			}
		}
		return $urls;
	}
}
