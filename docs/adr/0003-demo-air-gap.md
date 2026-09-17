# Demo agent is a stranger

The demo chatbot lives outside the sidecar (own folder/process/deps/DB), imports zero sidecar modules, and talks only over public HTTP with its own OAuth client. The build fails on any import across the line in either direction.

Amended by ADR-0012: the demo chat is admitted by its own published Agent Profile rather than a Merchant-issued OAuth client, which is what makes it a genuine stranger. The air-gap rule itself — own folder, process, deps and DB, zero imports across the line, HTTP only — is unchanged and is the part this decision is about.
