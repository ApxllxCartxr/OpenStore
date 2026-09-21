<?php
/**
 * Same key, same result — including a refusal.
 *
 * A retry that succeeds where the first attempt refused is a second hold with
 * extra steps. So the stored answer is the whole response: status and body,
 * written **before** the response goes out, so a crash on the wire replays what
 * the caller would have seen rather than re-running the mutation.
 *
 * Its own table rather than options or transients: an option is autoloaded into
 * every page render, and a transient is allowed to vanish early — which would
 * turn "already reserved" into a second reserve.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Idempotency {

	public const TABLE_VERSION_OPTION = 'openstore_idempotency_schema';
	private const SCHEMA_VERSION      = 1;

	/** How long a key is honoured. Longer than any retry a client will make. */
	private const RETENTION_DAYS = 7;

	public static function table(): string {
		global $wpdb;
		return $wpdb->prefix . 'openstore_idempotency';
	}

	public static function install_if_needed(): void {
		if ( (int) get_option( self::TABLE_VERSION_OPTION, 0 ) !== self::SCHEMA_VERSION ) {
			self::install();
		}
	}

	public static function install(): void {
		global $wpdb;
		require_once ABSPATH . 'wp-admin/includes/upgrade.php';

		$table   = self::table();
		$collate = $wpdb->get_charset_collate();

		// `idem_key` is UNIQUE and that is the race-closer, not the SELECT above
		// the INSERT: two concurrent retries both read "not seen yet", and only
		// one survives the write.
		dbDelta(
			"CREATE TABLE {$table} (
				id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
				idem_key VARCHAR(191) NOT NULL,
				door VARCHAR(64) NOT NULL,
				status SMALLINT UNSIGNED NOT NULL,
				body LONGTEXT NOT NULL,
				created_at DATETIME NOT NULL,
				PRIMARY KEY (id),
				UNIQUE KEY idem_key (idem_key),
				KEY created_at (created_at)
			) {$collate};"
		);

		update_option( self::TABLE_VERSION_OPTION, self::SCHEMA_VERSION, false );
	}

	/**
	 * The stored answer for this door and key, or null.
	 *
	 * @return array{status:int, body:mixed}|null
	 */
	public static function replay( string $door, string $key ): ?array {
		global $wpdb;
		$row = $wpdb->get_row(
			$wpdb->prepare(
				'SELECT status, body FROM ' . self::table() . ' WHERE idem_key = %s',
				self::compose( $door, $key )
			),
			ARRAY_A
		);
		if ( ! $row ) {
			return null;
		}
		return array(
			'status' => (int) $row['status'],
			'body'   => json_decode( (string) $row['body'], true ),
		);
	}

	/**
	 * Store this answer, unless one is already stored.
	 *
	 * `INSERT IGNORE`-shaped on purpose: the first writer wins, and a retry that
	 * arrives while the first is still in flight replays the first one's answer
	 * rather than overwriting it with its own.
	 */
	public static function remember( string $door, string $key, int $status, mixed $body ): void {
		global $wpdb;
		$wpdb->query(
			$wpdb->prepare(
				'INSERT IGNORE INTO ' . self::table() . ' (idem_key, door, status, body, created_at) VALUES (%s, %s, %d, %s, %s)',
				self::compose( $door, $key ),
				$door,
				$status,
				(string) wp_json_encode( $body ),
				current_time( 'mysql', true )
			)
		);
	}

	/** Old keys, swept by the store's own cron. Nothing retries after a week. */
	public static function forget_old(): void {
		global $wpdb;
		$wpdb->query(
			$wpdb->prepare(
				'DELETE FROM ' . self::table() . ' WHERE created_at < %s',
				gmdate( 'Y-m-d H:i:s', time() - ( self::RETENTION_DAYS * DAY_IN_SECONDS ) )
			)
		);
	}

	/**
	 * The key is scoped by door.
	 *
	 * `order_id:attempt` is unique per transition, not per door, so an
	 * unscoped key would let a `commit` replay answer a `release`.
	 */
	private static function compose( string $door, string $key ): string {
		return substr( $door . ':' . $key, 0, 191 );
	}
}
