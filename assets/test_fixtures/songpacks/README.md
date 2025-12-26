# SongPack fixtures

These fixtures are used as contract tests for:

- JSON Schema validation (`packages/songpack/schemas/*.schema.json`)
- runtime validation (`packages/songpack/src/validateSongPack.ts`)

## Fixtures

### `minimal_valid.songpack/`
A minimal-but-complete SongPack directory fixture containing:

- `manifest.json`
- feature JSON under `features/`
- one chart under `charts/`

Audio files are intentionally omitted; runtime validation currently focuses on JSON schema validation.
