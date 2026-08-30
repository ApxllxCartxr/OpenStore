# A2A mapping table (INTEROP_SPEC §6.4)

The A2A surface is the merchant reasoning agent; it does not translate shopping
cart fields. The external fields below are the only A2A boundary fields we name,
and each appears in `openstore/protocols/a2a/__init__.py`.

| A2A field | Direction | OpenStore field / type |
|---|---|---|
| `url` | out | AgentCard URL (`.well-known/agent.json`) |
| `authentication` | out | `{"schemes": ["oauth2_bearer"]}` |
| `skills` | out | read-only merchant skills (catalog_qa) |
| `agent_card` | out | `agent_card()` return value |
| `name` | out | agent display name |
| `version` | out | agent version |
