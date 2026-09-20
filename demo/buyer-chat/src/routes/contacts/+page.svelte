<script lang="ts">
	import type { PageProps } from './$types';
	let { data, form }: PageProps = $props();
</script>

<svelte:head><title>Shops — Duckie</title></svelte:head>
<div class="thread">
	<h1 style="font-size:1.25rem;font-weight:600">Shops</h1>
	<p>Paste a shop’s address. I fetch its card, check the key, and remember it.</p>

	<form method="POST" action="?/add" style="display:flex;gap:0.5rem;flex-wrap:wrap">
		<input name="url" placeholder="https://shop.example/.well-known/agent-commerce.json" class="mono"
			style="flex:1;min-width:20rem;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--raised);color:inherit" />
		<button type="submit">Add shop</button>
	</form>

	{#if form?.message}
		<p class="mono" style="color:{form.code ? 'var(--accent)' : 'var(--ok)'}">{form.message}</p>
	{/if}

	<ul style="list-style:none;padding:0;margin-top:1.5rem">
		{#each data.contacts as contact (contact.domain)}
			<li class="tool" style="margin-bottom:0.5rem;display:flex;gap:1rem;align-items:center">
				<div>
					<strong>{contact.name}</strong>
					<div class="mono" style="color:var(--comment);font-size:0.8125rem">
						{contact.domain} · {contact.protocols.join(', ') || 'no protocols listed'}
					</div>
				</div>
				<form method="POST" action="?/forget" style="margin-left:auto">
					<input type="hidden" name="domain" value={contact.domain} />
					<button class="secondary" type="submit">Forget</button>
				</form>
			</li>
		{:else}
			<li style="color:var(--comment)">No shops yet.</li>
		{/each}
	</ul>
</div>
