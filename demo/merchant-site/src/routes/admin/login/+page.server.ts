import { fail, redirect } from '@sveltejs/kit';
import { CSRF_COOKIE, login, SESSION_COOKIE } from '$lib/auth.ts';

export const actions = {
	default: async ({ request, cookies }) => {
		const form = await request.formData();
		const result = await login(
			String(form.get('email') ?? ''),
			String(form.get('password') ?? '')
		);
		// One message for both wrong-password and no-such-user: the difference
		// between them enumerates operators.
		if (!result) return fail(401, { message: 'Those credentials are not valid.' });

		cookies.set(SESSION_COOKIE, result.session, {
			path: '/admin',
			httpOnly: true,
			sameSite: 'lax',
			secure: process.env.NODE_ENV === 'production'
		});
		cookies.set(CSRF_COOKIE, result.csrf, {
			path: '/admin',
			httpOnly: false, // the form has to read it
			sameSite: 'lax',
			secure: process.env.NODE_ENV === 'production'
		});
		redirect(303, '/admin');
	}
};
