import { redirect } from '@sveltejs/kit';
import { resolve, SESSION_COOKIE } from '$lib/auth.ts';

/**
 * Everything under /admin is session-authenticated. This runs before any admin
 * page loads, so a new route cannot be added without inheriting the check —
 * which is the difference between access control and a habit.
 */
export async function load({ cookies, url }) {
	const session = resolve(cookies.get(SESSION_COOKIE));
	if (!session) {
		if (url.pathname !== '/admin/login') redirect(303, '/admin/login');
		return { operator: null, csrf: '' };
	}
	return { operator: session.email, csrf: session.csrf };
}
