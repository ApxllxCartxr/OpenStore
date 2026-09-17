# Demo agent is a stranger

The demo chatbot lives outside the sidecar (own folder/process/deps/DB), imports zero sidecar modules, and talks only over public HTTP with its own OAuth client. The build fails on any import across the line in either direction.
