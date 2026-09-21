/**
 * Which shop this deploy is.
 *
 * One image, many shops: the name, the line under it and the accent are read
 * at runtime from the environment, because a second shop that needs a second
 * build of this site is a fork, not an install — and a fork is what this
 * whole project exists to argue against.
 *
 * Runtime, not build-time (`$env/dynamic/public`, not `static`): the image is
 * built once in CI and the same layers run as SpoiledDuckie and as Dog-Eared.
 * Defaults are SpoiledDuckie's, so an existing deploy that sets nothing keeps
 * the shop it already had.
 */
import { env } from '$env/dynamic/public';

export const BRAND = {
	name: env.PUBLIC_SHOP_NAME || 'SpoiledDuckie',
	tagline: env.PUBLIC_SHOP_TAGLINE || 'Small accessories, made in Bengaluru.',
	/** Beside the shop name in the browser tab. Kept short: a title is a label,
	 *  not the tagline a second time. */
	blurb: env.PUBLIC_SHOP_BLURB || 'accessories made in Bengaluru',
	/** One token, so a second shop is recognisably not the first at a glance.
	 *  Everything else — type, spacing, the whole token layer — is shared.
	 *
	 *  A six-digit hex value or nothing: this string reaches a `<style>` block,
	 *  and deploy configuration is not a reason to let arbitrary text in there.
	 *  An unparseable value keeps the default rather than failing the boot —
	 *  the wrong accent is a blemish, a shop that will not start is an outage. */
	accent: /^#[0-9a-f]{6}$/i.test(env.PUBLIC_SHOP_ACCENT ?? '') ? env.PUBLIC_SHOP_ACCENT! : ''
};
