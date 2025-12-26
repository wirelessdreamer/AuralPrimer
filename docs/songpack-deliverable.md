# SongPack deliverable

This repo defines two interchangeable SongPack container forms:

- **Directory SongPack**: `MySong.songpack/` (developer-friendly)
- **Zip SongPack**: `MySong.songpack` (distribution artifact)

The **SongPack deliverable** is the deterministic **zip SongPack** (`*.songpack` file).

## Goals

- Produce a distribution-friendly `.songpack` file from a directory SongPack.
- Ensure deterministic output:
  - stable zip file bytes across runs
  - stable JSON formatting/key ordering inside the zip

## Library API

The `@auralprimer/songpack` package exports:

- `canonicalJsonStringify(value, opts)`
  - stable recursive key ordering
  - optional float quantization (default `1e-6`)

- `buildSongPackZipFromDirectory(songPackDir, opts)` → `Uint8Array`
  - validates the input directory SongPack (by default)
  - canonicalizes `*.json` files (by default)
  - uses deterministic ordering for zip entries
  - uses a fixed in-range ZIP `mtime` by default (`1980-01-01T00:00:00`)

- `writeSongPackZipFromDirectory(songPackDir, outSongPackZipPath, opts)`

### Example

```ts
import { writeSongPackZipFromDirectory } from "@auralprimer/songpack";

await writeSongPackZipFromDirectory(
  "/abs/path/MySong.songpack",
  "/abs/path/MySong.songpack" // output zip
);
```

## Tests / contracts

- `packages/songpack/tests/buildSongPackZip.test.ts` asserts:
  - produced zip validates via `validateSongPack()`
  - output is byte-for-byte deterministic
  - JSON is canonicalized inside the zip
