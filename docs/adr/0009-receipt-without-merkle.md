# Receipt is a signed hash-chain, no Merkle in v1

Merkle batch-anchoring is mall-scale complexity a single Merchant doesn't need to prove one sale. V1 evidence is the 5-section bundle (bought / tapped / decided / told / moved) hash-chained and Merchant-ES256-signed with the per-item Attestation pinned by hash; the bundle carries its JWKS snapshot so offline verify is really offline, and opens by unguessable 128-bit receipt ID instead of login.

Consequences: weakens the old S5 "Merkle-stamped" gate with grill evidence — single-Merchant disputes need chain + signature, not batch roots. Merkle returns in phase-two with zero money-core changes.
