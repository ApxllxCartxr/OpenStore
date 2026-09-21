<script lang="ts">
	import { enhance } from '$app/forms';
	import type { PageProps } from './$types';
	import type { Widget } from '$lib/widgets.ts';
	import { renderMarkdown } from '$lib/markdown.ts';
	import { ALWAYS_ALLOWABLE } from '$lib/tools/loop.ts';
	let { data }: PageProps = $props();

	/** Results worth showing as something other than JSON: what the shop
	 *  returned, rendered as the shop returned it. No total is computed here. */
	type Parsed = { shop: string; name: string; request: any; response: any; at: string };
	const calls = $derived(
		data.thread.toolCalls.map((c) => ({
			shop: c.shop,
			name: c.name,
			at: c.at,
			request: JSON.parse(c.request),
			response: c.response ? JSON.parse(c.response) : null
		})) as Parsed[]
	);

	function shopName(domain: string): string {
		return data.shops.find((shop) => shop.domain === domain)?.name ?? domain;
	}

	/** One transcript in the order it happened: messages and tool calls
	 *  interleaved by timestamp. Rendering them in two separate loops is what
	 *  pinned every tool card to the bottom of the page, after the pending
	 *  approval, no matter when the call actually ran. */
	type TimelineMessage = {
		kind: 'message';
		key: string;
		/** The row id, which is how a widget submission names the widget it came
		 *  from — the definition is then read from the database, not the post. */
		id: number;
		role: string;
		text: string;
		widget: Widget | null;
		/** The model's own reasoning for this turn, verbatim, when the driver
		 *  captured any. Never invented, never summarised — the same rule as
		 *  every other value on this page. */
		thinking: string | null;
		at: string;
	};
	type TimelineTool = {
		kind: 'tool';
		key: string;
		shop: string;
		name: string;
		request: any;
		response: any;
		at: string;
	};
	type TimelineItem = TimelineMessage | TimelineTool;
	const timeline = $derived.by(() => {
		const items = [
			...data.thread.messages.map(
				(m, n): TimelineMessage => ({
					kind: 'message',
					key: `m${n}`,
					id: m.id,
					role: m.role,
					text: m.text,
					// Stored validated; parsed here only to render. A row without one
					// is an ordinary message, which is most of them.
					widget: m.widget ? (JSON.parse(m.widget) as Widget) : null,
					thinking: m.thinking,
					at: m.at
				})
			),
			...calls.map((c, n): TimelineTool => ({ kind: 'tool', key: `c${n}`, ...c }))
		].sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0)) as TimelineItem[];
		// Optimistic echo: the consumer's own words appear instantly, ahead of
		// the round trip. Keyed `optimistic` so it never collides with server
		// rows, and dropped the moment `update()` brings the real transcript.
		if (optimisticText) {
			items.push({
				kind: 'message',
				key: 'optimistic',
				id: 0,
				role: 'consumer',
				text: optimisticText,
				widget: null,
				thinking: null,
				at: ''
			});
		}
		return items;
	});

	/** Every shop's basket, exactly as that shop last reported it — one basket
	 *  per shop, because one sidecar per shop is what the protocol says
	 *  (ADR-0007), and merging them here would show a basket no shop can
	 *  quote. Nothing here is local state: a panel the page maintained itself
	 *  would be the second basket this whole design exists to avoid. */
	const baskets = $derived.by(() => {
		const byShop = new Map<string, { sku: string; qty: number; parent?: string }[]>();
		for (const call of calls) {
			const lines = call.response?.lines;
			if (Array.isArray(lines)) byShop.set(call.shop, lines);
		}
		return [...byShop.entries()]
			.map(([shop, lines]) => ({ shop, lines }))
			.filter((b) => b.lines.length);
	});

	/** Delivery costs, by option id, from the shop's own last answer. A widget
	 *  never carries an amount; this is where the amount beside one comes from. */
	const deliveryCosts = $derived.by(() => {
		const costs = new Map<string, { cost_minor: number; eta_days: number }>();
		for (const call of calls) {
			for (const option of (call.response?.options ?? []) as any[]) {
				costs.set(String(option.id), {
					cost_minor: Number(option.cost_minor),
					eta_days: Number(option.eta_days)
				});
			}
		}
		return costs;
	});

	function rupees(minor: number): string {
		return `₹${(minor / 100).toFixed(2)}`;
	}
	/** Shop images are root-relative on the shop's own origin, and this page is
	 *  a different origin — so they are resolved against whichever shop's
	 *  result they came from, not a single "the" shop that may not be the
	 *  one that answered. */
	function onShop(src: string, domain: string): string {
		return src.startsWith('http') ? src : `http://${domain}${src}`;
	}
	let sending = $state(false);
	/** The verdict in flight on the consent prompt, if any. While set, the
	 *  Allow/Decline buttons are dead and a working state shows: the decide
	 *  round trip runs the tool *and* the agent's next turns, which is slow
	 *  enough to need acknowledging. */
	let deciding = $state<string | null>(null);
	/** The consumer's words, shown instantly while the round trip runs. */
	let optimisticText = $state<string | null>(null);
	let draft = $state('');
	let composerBox = $state<HTMLTextAreaElement | undefined>(undefined);

	/** The answer lands above a sticky composer, so the typing bubble pulls the
	 *  viewport down to where the reply will appear. Runs once, on mount. */
	function follow(node: HTMLElement) {
		node.scrollIntoView({ block: 'end', behavior: 'smooth' });
	}

	/** Every widget posts the same way: the typing bubble holds Miro's place
	 *  while the call and the turn that follows it run. */
	function onWidgetSubmit() {
		sending = true;
		return async ({ update }: { update: () => Promise<void> }) => {
			try {
				await update();
			} finally {
				sending = false;
			}
		};
	}

	/** Clear the field the moment the message is taken, not when the reply lands. */
	function resetComposer() {
		draft = '';
		if (composerBox) composerBox.style.height = 'auto';
	}

	/** Enter sends, Shift+Enter breaks the line: the convention every assistant
	 *  UI has already taught the Consumer. `requestSubmit` rather than `submit`
	 *  so the progressive-enhancement handler still runs. */
	function onKeydown(event: KeyboardEvent) {
		if (event.key !== 'Enter' || event.shiftKey) return;
		event.preventDefault();
		(event.currentTarget as HTMLTextAreaElement).form?.requestSubmit();
	}

	/** The field grows with the message instead of scrolling a one-line box. */
	function autogrow(event: Event) {
		const field = event.currentTarget as HTMLTextAreaElement;
		field.style.height = 'auto';
		field.style.height = `${field.scrollHeight}px`;
	}
</script>

<svelte:head><title>Miro, a buyer agent</title></svelte:head>

<div class="thread">
	{#if !data.shops.length}
		<p class="meta" style="margin-bottom:var(--s-4)">
			No shop yet. <a href="/contacts">Add one</a> to start.
		</p>
	{/if}

	{#each timeline as item, i (item.key)}
		{#if item.kind === 'message'}
			<div class="row" data-who={item.role} style="--i:{i}">
				{#if item.role === 'agent'}
					<span class="who">Miro</span>
				{/if}
				{#if item.thinking}
					<!-- The model's own reasoning, verbatim and collapsed by default —
					     the same disclosure pattern as a tool call's request JSON: shown
					     on request, never paraphrased into something that could be
					     wrong with no way to tell. -->
					<details class="thinking-block">
						<summary>Thinking</summary>
						<p class="mono">{item.thinking}</p>
					</details>
				{/if}
				{#if item.text}
					{#if item.role === 'agent'}
						<!-- Models format, and rendering their asterisks literally made
						     every list of recommendations read as `- **Tote**`. The
						     renderer escapes every character before it adds a tag, so
						     nothing a model writes can become HTML here. The Consumer's
						     own words below are shown exactly as typed. -->
						<div class="bubble md">{@html renderMarkdown(item.text)}</div>
					{:else}
						<div class="bubble">{item.text}</div>
					{/if}
				{/if}
				{#if item.widget}
					<!-- What the agent offered to do next, as something to tap or fill
					     in. Validated on the way in; the shop still refuses anything it
					     does not like, and a spend step can never be one of these. -->
					<div class="widget">
						{#if item.widget.kind === 'chips'}
							<div class="chips">
								{#each item.widget.options as chip, c (item.key + c)}
									<form method="POST" action="?/send" use:enhance={onWidgetSubmit}>
										<input type="hidden" name="text" value={chip} />
										<button class="chip" type="submit" disabled={sending}>{chip}</button>
									</form>
								{/each}
							</div>
						{:else if item.widget.kind === 'choices'}
							<form method="POST" action="?/widget" use:enhance={onWidgetSubmit}>
								<input type="hidden" name="message" value={item.id} />
								{#if item.widget.title}<p class="widget-title">{item.widget.title}</p>{/if}
								<div class="chips">
									{#each item.widget.options as option, o (item.key + o)}
										<button
											class="chip"
											type="submit"
											name={item.widget.arg}
											value={option.value}
											disabled={sending}
										>
											{option.label}
											{#if deliveryCosts.has(option.value)}
												<span class="meta mono">
													{rupees(deliveryCosts.get(option.value)?.cost_minor ?? 0)} ·
													{deliveryCosts.get(option.value)?.eta_days} days
												</span>
											{/if}
										</button>
									{/each}
								</div>
							</form>
						{:else}
							<form method="POST" action="?/widget" use:enhance={onWidgetSubmit}>
								<input type="hidden" name="message" value={item.id} />
								<p class="widget-title">{item.widget.title}</p>
								<div class="fields">
									{#each item.widget.fields as field, f (item.key + f)}
										<label>
											<span class="meta">{field.label}</span>
											<input
												name={field.name}
												type={field.kind}
												placeholder={field.placeholder}
												required={field.required}
												autocomplete="off"
											/>
										</label>
									{/each}
								</div>
								<p class="meta">
									Sent to {data.shops.length === 1 ? data.shops[0]?.name : 'the shop'} as you typed it — if
									more than one could answer, you'll be asked which. Nothing is charged.
								</p>
								<button class="tap" type="submit" disabled={sending}>{item.widget.submit}</button>
							</form>
						{/if}
					</div>
				{/if}
			</div>
		{:else}
			<details class="tool" style="--i:{i}">
				<summary class="mono">{item.shop ? `${shopName(item.shop)} · ` : ''}{item.name}</summary>
				<!-- The exact request JSON. A card that summarised could be wrong, and
				     the Consumer would have no way to tell. -->
				<pre class="mono">{JSON.stringify(item.request, null, 2)}</pre>
				{#if item.response}
					<pre class="mono" style="color:var(--muted)">{JSON.stringify(item.response, null, 2)}</pre>
				{/if}
			</details>
			{#if item.response?.results?.length}
				<div class="results">
					{#each item.response.results as result, j (item.key + j)}
						<div class="result" style="--i:{i}">
							{#if result.image}
								<img src={onShop(result.image, item.shop)} alt={result.name} loading="lazy" />
							{/if}
							<div class="result-body">
								<div style="font-weight:600">{result.name}</div>
								<div class="mono meta">from {rupees(result.from_minor)}</div>
								<span class="pill" data-b={result.availability}>{result.availability}</span>
							</div>
						</div>
					{/each}
				</div>
			{/if}
			{#if item.response?.quote}
				<!-- The shop's own Quote, line for line. Nothing on this page adds up. -->
				<table class="quote mono">
					<tbody>
						{#each item.response.quote.lines as line, k (item.key + k)}
							<tr>
								<td>{line.sku} × {line.qty}</td>
								<td>{rupees(line.line_total_minor)}</td>
							</tr>
						{/each}
						{#each item.response.quote.tax_lines as tax, t (item.key + t)}
							<tr class="tax">
								<td>{tax.label}</td>
								<td>{rupees(tax.amount_minor)}</td>
							</tr>
						{/each}
						<tr class="total">
							<td>Total</td>
							<td>{rupees(item.response.quote.total_minor)}</td>
						</tr>
					</tbody>
				</table>
			{/if}
			{#if item.response?.approve_url}
				<div class="notice">
					<p style="margin:0">
						The shop wants {rupees(item.response.total_minor)} for
						<code>{item.response.order_id}</code>.
					</p>
					<p class="meta" style="margin:var(--s-1) 0 var(--s-2)">
						You approve this on the shop's own page. I hold no payment credential and cannot
						complete it.
					</p>
					<a class="tap" href={item.response.approve_url} rel="noopener">
						Approve {rupees(item.response.total_minor)} at {item.shop}
					</a>
				</div>
			{/if}
		{/if}
	{/each}

	{#if sending || deciding}
		<!-- Miro's turn is being composed: the same three dots the send
		     button shows, now in a reply bubble so the wait has a place. -->
		<div class="row" data-who="agent" style="--i:{timeline.length}" use:follow aria-label="Miro is typing">
			<span class="who">Miro</span>
			<div class="bubble"><span class="thinking" aria-hidden="true"><i></i><i></i><i></i></span></div>
		</div>
	{/if}

	{#if baskets.length}
		<!-- One basket per shop (ADR-0007) — line for line, with the two things a
		     Consumer should never have to talk an agent into: removing a line
		     they did not ask for, and starting over everywhere at once. No
		     prices — this panel counts nothing. -->
		<div class="basket">
			{#each baskets as { shop, lines } (shop)}
				<p class="widget-title">In the basket — {shopName(shop)}</p>
				{#each lines as line, b (shop + line.sku + b)}
					<div class="basket-line">
						<span class="mono">{line.sku} × {line.qty}</span>
						<form method="POST" action="?/basket" use:enhance={onWidgetSubmit}>
							<input type="hidden" name="sku" value={line.sku} />
							<input type="hidden" name="shop" value={shop} />
							<button class="link" type="submit" disabled={sending}>Remove</button>
						</form>
					</div>
				{/each}
			{/each}
			<form method="POST" action="?/basket" use:enhance={onWidgetSubmit}>
				<button class="link" type="submit" disabled={sending}>Start over (every shop)</button>
			</form>
		</div>
	{/if}

	{#if data.pending}
		<div class="notice">
			<h2 style="font-size:1rem">
				{data.pending.name === 'start-checkout' || data.pending.name === 'place-order'
					? 'This step builds a paid basket'
					: `Allow ${data.pending.name}?`}
			</h2>
			{#if data.pending.name === 'start-checkout' || data.pending.name === 'place-order'}
				<!-- Grants a SCOPE and never an amount: no total exists yet, and a
				     prompt that reads like an amount approval teaches the Consumer
				     to click through the tap that is one. -->
				<p class="meta" style="margin-top:var(--s-1)">
					No amount is approved here. You will see the exact total, from the shop, and approve
					it on the shop's own page.
				</p>
				<p class="meta" style="margin-top:var(--s-1)">
					This click and the one on the shop's page are different things: this one only lets
					Miro show you a total. Spending real money always needs a second, separate step on
					{data.pending.shopName}'s own site — usually a passkey or a UPI PIN — which
					Miro cannot see, hold, or complete for you. That is why nothing here ever asks you to
					sign anything.
				</p>
				{#if data.pending.overCeiling}
					<p class="meta" style="margin-top:var(--s-1);color:var(--accent)">
						This checkout is {rupees(data.pending.overCeiling)}, above the ceiling you set on the
						<a href="/contacts">Shops page</a>. That is a flag Miro is raising for you, not a
						block — nothing here stops you from approving it on the shop's own page.
					</p>
				{/if}
			{:else if data.pendingNote}
				<!-- Plain words beside the exact JSON, never instead of it: a note
				     that replaced the request could be wrong with no way to tell. -->
				<p class="meta" style="margin-top:var(--s-1)">{data.pendingNote}</p>
			{/if}
			{#if data.pendingHasArgs}
				<!-- Shown only when the args carry something worth reading.
				     An empty `{"id": ""}` is noise, not transparency: the note
				     above already says what the call does. -->
				<pre class="mono">{JSON.stringify(data.pending.args, null, 2)}</pre>
			{/if}
			<form
				method="POST"
				action="?/decide"
				use:enhance={({ formData }) => {
					// Answer first, then run: the buttons die instantly and a
					// working state holds the prompt's place until `update()`
					// lands with whatever the tool and the next turns produced.
					deciding = String(formData.get('verdict') ?? '');
					return async ({ update }) => {
						try {
							await update();
						} finally {
							deciding = null;
						}
					};
				}}
				class="actions"
			>
				<button name="verdict" value="allow-once" type="submit" disabled={deciding !== null}>
					Allow once
				</button>
				{#if ALWAYS_ALLOWABLE.has(data.pending.name)}
					<button
						class="secondary"
						name="verdict"
						value="always"
						type="submit"
						disabled={deciding !== null}
					>
						Always allow {data.pending.name} (never a spend step)
					</button>
				{/if}
				<button
					class="secondary"
					name="verdict"
					value="declined"
					type="submit"
					disabled={deciding !== null}
				>
					Decline
				</button>
			</form>
			{#if deciding}
				<p class="meta" style="margin-top:var(--s-1)" aria-live="polite">
					<span class="thinking" aria-hidden="true"><i></i><i></i><i></i></span>
					{deciding === 'declined' ? 'Declining…' : `Running ${data.pending.name} — one moment…`}
				</p>
			{/if}
		</div>
	{/if}

	<div class="composer">
		<form
			method="POST"
			action="?/send"
			use:enhance={({ formData }) => {
				// Echo first, then send: the message reflects instantly and the
				// typing bubble holds Miro's place until `update()` lands.
				optimisticText = String(formData.get('text') ?? '').trim() || null;
				sending = true;
				resetComposer();
				return async ({ update }) => {
					try {
						await update();
					} finally {
						sending = false;
						optimisticText = null;
					}
				};
			}}
		>
			<div class="composer-field">
				<textarea
					name="text"
					rows="1"
					placeholder="What are you looking for?"
					autocomplete="off"
					required
					bind:value={draft}
					bind:this={composerBox}
					onkeydown={onKeydown}
					oninput={autogrow}
				></textarea>
				<button class="send" type="submit" disabled={sending || !draft.trim()} aria-label="Send message">
					{#if sending}
						<!-- Three dots rather than a spinner: the agent is composing a
						     turn, not blocking on an unknown wait. -->
						<span class="thinking" aria-hidden="true"><i></i><i></i><i></i></span>
					{:else}
						<!-- Arrow, not a word: at this size a label reads as a second
						     button competing with the field. -->
						<svg
							viewBox="0 0 16 16"
							width="18"
							height="18"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M8 13.5V2.5M3.5 7 8 2.5 12.5 7" />
						</svg>
					{/if}
				</button>
			</div>
		</form>

		<div class="composer-hint">
			<!-- Behaviour first, implementation on request: the driver string is
			     one disclosure away instead of sitting in the conversation. -->
			<details class="agent-meta">
				<summary>Miro · buyer agent · {data.tools.length} tools · no payment credential</summary>
				<p class="mono">running: {data.driver}</p>
				<form method="POST" action="?/driver" use:enhance class="model-switch">
					<label class="meta">
						Model
						<select
							name="choice"
							value={data.driverChoice ?? 'default'}
							onchange={(e) => e.currentTarget.form?.requestSubmit()}
						>
							<option value="default">Deploy default</option>
							{#each data.drivers as option (option.id)}
								<option value={option.id} disabled={!option.configured}>
									{option.label}{option.configured ? '' : ' (not configured)'}
								</option>
							{/each}
						</select>
					</label>
				</form>
			</details>
			<form method="POST" action="?/reset" use:enhance>
				<button class="link" type="submit">Reset thread</button>
			</form>
		</div>
	</div>
</div>
