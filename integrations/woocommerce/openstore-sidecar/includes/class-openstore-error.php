<?php
/**
 * The refusal envelope, and the one table that decides its HTTP status.
 *
 * Mirrors `core/codes.py`. **The call site never chooses a status**: two
 * implementers picking independently is how one client retries what the other
 * treats as fatal (§6.1). A code with no mapping here is a programming error
 * and says so, rather than defaulting to 500.
 */

declare( strict_types=1 );

defined( 'ABSPATH' ) || exit;

final class OpenStore_Door_Error extends Exception {

	/** Reason code → HTTP status. The same table as `core/codes.py`. */
	private const HTTP_STATUS = array(
		// Business refusals against well-formed requests.
		'sold-out'                  => 409,
		'price-changed'             => 409,
		'code-invalid'              => 409,
		'destination-unserviceable' => 409,
		'amount-mismatch'           => 409,
		'time-limit-reached'        => 409,
		'payment-window-elapsed'    => 409,
		'delivery-window-elapsed'   => 409,
		'cap-exceeded'              => 409,
		'qty-exceeded'              => 409,
		'count-exceeded'            => 409,
		'window-closed'             => 409,
		'blocked-item'              => 409,
		'tag-refused'               => 409,
		'no-hold'                   => 409,
		'dispatch-not-allowed'      => 409,
		'cancel-not-allowed'        => 409,
		// Malformed or wrongly-shaped requests.
		'variant-required'          => 400,
		'addon-without-parent'      => 400,
		'quote-inconsistent'        => 400,
		'currency-mismatch'         => 400,
		'merchant-mismatch'         => 400,
		// Authentication and authorization.
		'signature-invalid'         => 401,
		'untrusted-key'             => 401,
		'authority-missing'         => 403,
		'authority-kind-not-enabled' => 403,
		'authority-stale'           => 403,
		'intent-mechanism-not-enabled' => 403,
		'method-not-supported'      => 403,
		'profile-refused'           => 403,
		'agent-blocked'             => 403,
		'not-found'                 => 404,
		'rate-limited'              => 429,
	);

	/** @var array<string, mixed> */
	private array $fields;

	/**
	 * @param array<string, mixed> $fields Extra machine-readable context.
	 */
	public function __construct( private string $code_value, string $detail, array $fields = array() ) {
		parent::__construct( $detail );
		$this->fields = $fields;
	}

	public function reason_code(): string {
		return $this->code_value;
	}

	public function status(): int {
		if ( ! isset( self::HTTP_STATUS[ $this->code_value ] ) ) {
			// Loud on purpose. A code with no status is a code this plugin and
			// the sidecar disagree about, and guessing 500 would hide it.
			throw new RuntimeException(
				sprintf( 'reason code %s has no HTTP status mapping', $this->code_value )
			);
		}
		return self::HTTP_STATUS[ $this->code_value ];
	}

	/** @return array<string, mixed> */
	public function to_payload(): array {
		$error = array(
			'code'   => $this->code_value,
			'detail' => $this->getMessage(),
		);
		if ( $this->fields ) {
			$error['fields'] = $this->fields;
		}
		return array( 'error' => $error );
	}
}
