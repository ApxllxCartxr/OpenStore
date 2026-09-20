import adapter from '@sveltejs/adapter-node';

/** @type {import('@sveltejs/kit').Config} */
export default {
	kit: {
		adapter: adapter(),
		// The storefront is public; /admin is session-authenticated and bound to
		// the private network (B3). CSRF stays on for every mutation — the admin
		// holds the Refund button, which moves real money through the sidecar.
		csrf: { trustedOrigins: [] }
	}
};
