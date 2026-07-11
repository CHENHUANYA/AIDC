# BM25 Index Upgrade

- Status: **PASS**
- Mode: `apply`
- Target tokenizer: `unicode-domain-v1`
- Git revision: `cee0816f8f5b81769ce734bb3989a4ed32344348`
- Backup directory: `backups/bm25-index-upgrade/20260711T080409Z`

| Collection | Sections | From | To | Result | Before SHA-256 | After SHA-256 |
|---|---:|---|---|---|---|---|
| 808d | 2075 | legacy-whitespace-v0 | unicode-domain-v1 | upgraded | `6db4015acb04123cd28884db295cbcf074a743a0e06c803eb0467021c10beea8` | `2c3cf43f305727262926c9df762d98e345bc6c7f33c123d2494d6cc0bae6437f` |
| 840d | 3143 | legacy-whitespace-v0 | unicode-domain-v1 | upgraded | `77b1ba83bf0e51b0c3077bb0dee45148d973994679ccc02182dcd10d6d2a1474` | `dffed472a4f9f9e7fbc01dbfb2429469782e82df0eeee09ca400803ce710bfd5` |
| 840dsl | 4449 | legacy-whitespace-v0 | unicode-domain-v1 | upgraded | `59af62d974abd7563bfd9038b5579b80952abe1ef4b8ac754caa25d12b20dc3d` | `5ce543b7b8664be2ea5e77ae7f24a91e8a05ed574b0e1d29630f6c156d1e27c4` |

> This tool only accepts trusted, locally generated pickle indexes. Never use it with an untrusted pickle file.
