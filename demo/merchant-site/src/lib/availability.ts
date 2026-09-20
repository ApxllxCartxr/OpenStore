/**
 * The exposure boundary, Merchant-side.
 *
 * Exact counts cross door 2 on the private network and nowhere else. Everything
 * the storefront renders — and everything an agent can ever see — is a bucket,
 * because a count handed to anyone who asks is both competitive intelligence
 * and an inventory-probing oracle: ask for 50, get refused, ask for 25, binary
 * search the shop's stock in eight requests.
 */
export type Bucket = 'in-stock' | 'low-stock' | 'sold-out';

export function bucketFor(available: number, lowStockThreshold: number): Bucket {
	if (!Number.isInteger(available) || available < 0) {
		throw new Error(`stock is int >= 0, never ${available}; this should have failed at load`);
	}
	if (available === 0) return 'sold-out';
	if (available <= lowStockThreshold) return 'low-stock';
	return 'in-stock';
}

/** A Product Group reads in-stock when **any** of its items is. */
export function groupBucket(buckets: Bucket[]): Bucket {
	if (buckets.includes('in-stock')) return 'in-stock';
	if (buckets.includes('low-stock')) return 'low-stock';
	return 'sold-out';
}

export const BUCKET_LABEL: Record<Bucket, string> = {
	'in-stock': 'In stock',
	'low-stock': 'Low stock',
	'sold-out': 'Sold out'
};
