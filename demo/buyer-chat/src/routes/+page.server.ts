import { addMessage, createThread, thread } from '$lib/session.ts';
import { driverFromEnv } from '$lib/model/driver.ts';
import { TOOLS } from '$lib/tools/loop.ts';

const THREAD = 'thread_demo';

export async function load() {
	createThread(THREAD, 'SpoiledDuckie');
	const state = thread(THREAD);
	if (!state.messages.length) {
		addMessage(
			THREAD,
			'agent',
			'Paste a shop URL and I will add it. I can browse and build a basket — ' +
				'I cannot spend. Every purchase ends with you approving the exact amount on the shop’s own page.'
		);
	}
	return {
		thread: thread(THREAD),
		driver: driverFromEnv().name,
		tools: [...TOOLS]
	};
}
