import adapter from '@sveltejs/adapter-node';

// The storefront is public; /admin is session-authenticated and bound to the
// private network (B3). CSRF stays on for every mutation — the admin holds
// the Refund button, which moves real money through the sidecar.
//
// The edge 404s `/admin`, so the only way to reach it is the loopback port
// directly — where there is no `x-forwarded-proto` and adapter-node therefore
// computes an `https://` origin the browser never sent. Every admin form was
// refused as cross-site. These are the admin's own origins, and the browser
// is what sets `Origin`, so naming them costs nothing cross-site and is what
// makes the page usable at all.
//
// One build, ten shops (ADR-0007: a shop is an env var, not a rebuild) — so
// this cannot be one port. Fixed to 3000 alone, it silently worked for
// SpoiledDuckie and silently refused every other shop's admin login,
// Dog-Eared's included, since before there were eight more to notice it
// with. Every `127.0.0.1:<port>:3000` this repo's own docker-compose.yml
// publishes is named here instead — still a fixed list, because a
// self-hosted demo's own admin ports are a fact about the deploy, not
// something to discover from an incoming request the way a real webapp's
// origin sometimes has to be.
const ADMIN_PORTS = [3000, 3010, 3020, 3030, 3040, 3050, 3060, 3070, 3080, 3090];
const trustedOrigins = ADMIN_PORTS.flatMap((port) => [
	`http://127.0.0.1:${port}`,
	`http://localhost:${port}`
]);

/** @type {import('@sveltejs/kit').Config} */
export default {
	kit: {
		adapter: adapter(),
		csrf: { trustedOrigins }
	}
};
