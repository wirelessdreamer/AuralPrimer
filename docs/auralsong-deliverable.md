# AuralSong deliverable

This repo defines two interchangeable AuralSong container forms:

- **Directory AuralSong**: `MySong.auralsong/` (developer-friendly)
- **Zip AuralSong**: `MySong.auralsong` (distribution artifact)

The **AuralSong deliverable** is the deterministic **zip AuralSong** (`*.auralsong` file).

## Goals

- Produce a distribution-friendly `.auralsong` file from a directory AuralSong.
- Ensure deterministic output:
  - stable zip file bytes across runs
  - stable JSON formatting/key ordering inside the zip

## Library API

The `@auralprimer/auralsong` package exports:

- `canonicalJsonStringify(value, opts)`
  - stable recursive key ordering
  - optional float quantization (default `1e-6`)

- `buildAuralSongZipFromDirectory(auralSongDir, opts)` → `Uint8Array`
  - validates the input directory AuralSong (by default)
  - canonicalizes `*.json` files (by default)
  - uses deterministic ordering for zip entries
  - uses a fixed in-range ZIP `mtime` by default (`1980-01-01T00:00:00`)

- `writeAuralSongZipFromDirectory(auralSongDir, outAuralSongZipPath, opts)`

### Example

```ts
import { writeAuralSongZipFromDirectory } from "@auralprimer/auralsong";

await writeAuralSongZipFromDirectory(
  "/abs/path/MySong.auralsong",
  "/abs/path/MySong.auralsong" // output zip
);
```

## Tests / contracts

- `packages/auralsong/tests/buildAuralSongZip.test.ts` asserts:
  - produced zip validates via `validateAuralSong()`
  - output is byte-for-byte deterministic
  - JSON is canonicalized inside the zip
