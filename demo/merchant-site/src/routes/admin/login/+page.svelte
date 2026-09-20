<script lang="ts">
	import type { PageProps } from './$types';
	let { form }: PageProps = $props();
</script>

<svelte:head><title>Sign in — shop ops</title></svelte:head>
<h1 style="font-weight:600">Shop ops</h1>
<form method="POST" style="display:grid;gap:0.75rem;max-width:24rem">
	<label>Email<input name="email" type="email" required
		style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--raised);color:inherit" /></label>
	<label>Password<input name="password" type="password" required
		style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--raised);color:inherit" /></label>
	<button type="submit">Sign in</button>
</form>
{#if form?.message}<p class="mono" style="color:var(--accent)">{form.message}</p>{/if}
