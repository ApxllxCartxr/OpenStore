# No shopping UI in the sidecar

The thin `/chat` reference buyer is killed. Shopping UI lives entirely in the Buyer Agent, including the air-gapped demo stranger. The sidecar exposes protocol, tap ceremonies, signed totals, and the evidence viewer only. Merchant console lives on the same domain under `/agentic`, split by reverse proxy; shop ops stay in the store's own admin.
