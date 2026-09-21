import adapter from '@sveltejs/adapter-node';

/** @type {import('@sveltejs/kit').Config} */
export default {
	kit: {
		adapter: adapter(),
		// The storefront is public; /admin is session-authenticated and bound to
		// the private network (B3). CSRF stays on for every mutation — the admin
		// holds the Refund button, which moves real money through the sidecar.
		//
		// The edge 404s `/admin`, so the only way to reach it is the loopback
		// port directly — where there is no `x-forwarded-proto` and adapter-node
		// therefore computes an `https://` origin the browser never sent. Every
		// admin form was refused as cross-site. These are the admin's OWN
		// origins, and the browser is what sets `Origin`, so naming them costs
		// nothing cross-site and is what makes the page usable at all.
		csrf: { trustedOrigins: ['http://127.0.0.1:3000', 'http://localhost:3000'] }
	}
};
