<script lang="ts">
	import type { PageProps } from './$types';
	let { data, form }: PageProps = $props();
</script>

<svelte:head><title>Shops Miro knows</title></svelte:head>
<div class="thread">
	<h1 style="font-size:1.25rem">Shops</h1>
	<p class="meta" style="margin-top:var(--s-1)">
		Paste a shop's address. I fetch its card, check the key, and remember it.
	</p>

	<form method="POST" action="?/add" class="add">
		<label class="field">
			<span class="meta">Card address</span>
			<input
				name="url"
				class="mono"
				placeholder="https://shop.example/.well-known/agent-commerce.json"
			/>
		</label>
		<button type="submit">Add shop</button>
	</form>

	{#if form?.message}
		<p class="mono" style="color:{form.code ? 'var(--accent)' : 'var(--ok)'}">{form.message}</p>
	{/if}

	<h2 style="font-size:1.05rem;margin-top:var(--s-5)">Spending</h2>
	<p class="meta" style="margin-top:var(--s-1)">
		Miro cannot spend and holds no payment credential, whatever you set below — every purchase
		still ends with you approving the exact amount on the shop's own page. This only tells Miro
		to say something extra first if a checkout's total crosses it. It is not a bank-enforced
		limit and nothing here can make it one.
	</p>
	<form method="POST" action="?/ceiling" class="add" style="margin-top:var(--s-2)">
		<label class="field" style="flex:0 0 12rem">
			<span class="meta">Flag checkouts over (₹)</span>
			<input
				name="rupees"
				class="mono"
				type="number"
				step="0.01"
				min="0"
				placeholder="e.g. 2000"
				value={data.spendCeilingMinor ? (data.spendCeilingMinor / 100).toFixed(2) : ''}
			/>
		</label>
		<button type="submit">Save</button>
	</form>
	{#if data.spendCeilingMinor}
		<form method="POST" action="?/ceiling" style="margin-top:var(--s-1)">
			<button type="submit" class="secondary">Clear ceiling</button>
		</form>
	{/if}

	<ul class="contacts">
		{#each data.contacts as contact (contact.domain)}
			<li class="contact">
				<div>
					<strong>{contact.name}</strong>
					<div class="mono meta">
						{contact.domain} · {contact.protocols.join(', ') || 'no protocols listed'}
					</div>
				</div>
				<form method="POST" action="?/forget" style="margin-left:auto">
					<input type="hidden" name="domain" value={contact.domain} />
					<button class="secondary" type="submit">Forget</button>
				</form>
			</li>
		{:else}
			<li class="empty meta">
				No shops yet. Add one above and Miro can start searching it.
			</li>
		{/each}
	</ul>
</div>

<style>
	.add {
		display: flex;
		gap: var(--s-1);
		flex-wrap: wrap;
		align-items: flex-end;
		margin-top: var(--s-3);
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.375rem;
		flex: 1 1 22rem;
		min-width: 0;
	}

	.contacts {
		list-style: none;
		padding: 0;
		margin-top: var(--s-4);
	}
	.contact {
		display: flex;
		gap: var(--s-3);
		align-items: center;
		padding: var(--s-2);
		border: 1px solid var(--line);
		border-radius: var(--r-surface);
		background: var(--bg-raised);
		margin-bottom: var(--s-1);
	}
	.empty {
		padding: var(--s-4) 0;
	}
</style>
