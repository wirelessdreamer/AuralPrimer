# AuralSong fixtures

These fixtures are used as contract tests for:

- JSON Schema validation (`packages/auralsong/schemas/*.schema.json`)
- runtime validation (`packages/auralsong/src/validateAuralSong.ts`)

## Fixtures

### `minimal_valid.auralsong/`
A minimal-but-complete AuralSong directory fixture containing:

- `manifest.json`
- feature JSON under `features/`
- one chart under `charts/`

Audio files are intentionally omitted; runtime validation currently focuses on JSON schema validation.
