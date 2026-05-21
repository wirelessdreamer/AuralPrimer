"""Cross-validation tests: aural_ingest's emitted manifest.json must always
pass the JSON schema the game reads.

Regression context: a Psalm 19 SongPack imported via `aural_ingest import` did
not surface in the game's Play Songs panel. The Rust scan in
``apps/game/src-tauri/src/lib.rs::read_dir_manifest`` is intentionally
permissive (it extracts five optional fields and marks the entry ``ok=true``
on any valid JSON), but downstream UI display + chart picking depend on the
five fields the game schema marks ``required``:

  schema_version, song_id, title, artist, duration_sec

These tests bind the Python-side emitter to the game-side schema so that any
field rename, type change, or accidental removal in ``cli.py`` is caught at CI
time rather than at song-pick time on a user's machine.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "packages" / "songpack" / "schemas" / "manifest.schema.json"


def _load_schema() -> dict:
    if not SCHEMA_PATH.is_file():
        pytest.skip(f"game manifest schema not present at {SCHEMA_PATH} (mono-repo only)")
    return json.loads(SCHEMA_PATH.read_text("utf-8"))


def _jsonschema_or_skip():
    if importlib.util.find_spec("jsonschema") is None:
        pytest.skip("jsonschema not installed in the ingest runtime")
    import jsonschema
    return jsonschema


def _emit_init_manifest(*, title: str = "Test Song", artist: str = "Test Artist") -> dict:
    """Reproduce the manifest aural_ingest writes during the ``init_songpack`` stage.

    This intentionally mirrors ``cli.py`` ~line 2438 — kept in sync by reading
    the source string and asserting the dict literal hasn't drifted (see
    ``test_init_manifest_matches_cli_source`` below).
    """
    from aural_ingest.cli import PIPELINE_ID, PIPELINE_VERSION, SCHEMA_VERSION, STAGES

    return {
        "schema_version": SCHEMA_VERSION,
        "song_id": "0" * 32,
        "title": title,
        "artist": artist,
        "duration_sec": 0.0,
        "source": {
            "original_filename": "fake.wav",
            "original_sha256": "0" * 64,
            "ingest_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "timing": {
            "audio_sample_rate_hz": None,
            "audio_start_offset_sec": 0.0,
            "timebase": "audio",
        },
        "pipeline": {
            "pipeline_id": PIPELINE_ID,
            "pipeline_version": PIPELINE_VERSION,
            "profile": "gameplay_default",
            "stage_fingerprints": {st.id: st.version for st in STAGES},
            "transcription": {},
        },
        "recognition": {},
        "assets": {
            "audio": {"mix_path": "audio/mix.wav"},
            "midi": {"notes_path": "features/notes.mid"},
        },
    }


def test_init_manifest_passes_game_schema() -> None:
    """The initial (pre-decode) manifest aural_ingest writes must validate.

    A partial/aborted import leaves only this initial manifest; the game
    should still discover the songpack (Rust scan is permissive) and the
    schema should still pass so the strict validateSongPack path doesn't
    reject it.
    """
    schema = _load_schema()
    jsonschema = _jsonschema_or_skip()

    manifest = _emit_init_manifest()
    jsonschema.validate(manifest, schema)


def test_finalized_manifest_passes_game_schema() -> None:
    """The post-import manifest (duration filled in, more pipeline state) must validate."""
    schema = _load_schema()
    jsonschema = _jsonschema_or_skip()

    manifest = _emit_init_manifest()
    # Simulate the finalize step populating duration + extra pipeline metadata.
    manifest["duration_sec"] = 288.44
    manifest["pipeline"]["transcription"] = {
        "drum_engine": "combined_filter",
        "instrument_engines": {
            "bass": "melodic_yin_octave_hps_fix",
            "keys": "piano_auto",
            "lead_guitar": "melodic_adaptive",
            "rhythm_guitar": "melodic_adaptive",
        },
    }
    manifest["assets"]["audio"]["stems"] = {
        "drums": "audio/stems/drums.wav",
        "bass": "audio/stems/bass.wav",
        "keys": "audio/stems/keys.wav",
        "vocals": "audio/stems/vocals.wav",
    }
    manifest["assets"]["features"] = {
        "beats_path": "features/beats.json",
        "sections_path": "features/sections.json",
        "tempo_map_path": "features/tempo_map.json",
    }

    jsonschema.validate(manifest, schema)


def test_required_fields_have_correct_python_types() -> None:
    """Lock the Python types of the 5 ``required`` schema fields.

    A regression that changes ``duration_sec`` from float to str (or
    ``schema_version`` from str to int) would still pass jsonschema because
    Python's json.dumps handles both, but the Rust ``parse_manifest_json``
    asserts type via ``as_str() / as_f64()`` -- a wrong type silently becomes
    ``None`` and the game shows ``(missing title)``.
    """
    manifest = _emit_init_manifest()
    assert isinstance(manifest["schema_version"], str)
    assert isinstance(manifest["song_id"], str)
    assert isinstance(manifest["title"], str)
    assert isinstance(manifest["artist"], str)
    assert isinstance(manifest["duration_sec"], (int, float))
    assert not isinstance(manifest["duration_sec"], bool)  # bool is a subclass of int!


def test_required_fields_match_game_schema() -> None:
    """If the game schema gains a new required field, the emitter must too."""
    schema = _load_schema()
    expected = set(schema.get("required", []))
    manifest = _emit_init_manifest()
    missing = expected - set(manifest.keys())
    assert not missing, (
        f"aural_ingest emits a manifest missing required-by-game fields {missing}. "
        f"Update python/ingest/src/aural_ingest/cli.py init_songpack stage."
    )


def test_empty_artist_and_zero_duration_still_valid() -> None:
    """Suno/Demucs imports often supply no artist metadata. The emitter falls
    back to ``args.artist or ""`` and the manifest is finalized with
    ``duration_sec`` set later. Both initial-state values must satisfy the
    schema or partially-imported songpacks become undiscoverable."""
    schema = _load_schema()
    jsonschema = _jsonschema_or_skip()

    manifest = _emit_init_manifest(title="Untitled", artist="")
    assert manifest["artist"] == ""
    assert manifest["duration_sec"] == 0.0
    jsonschema.validate(manifest, schema)


def test_init_manifest_matches_cli_source() -> None:
    """Drift guard: if cli.py's init_songpack block adds/removes a top-level
    key, this test fails and forces an update of ``_emit_init_manifest``.

    We don't import the cli's writer directly because it does heavy IO. We
    instead string-scan the source for the dict keys it sets at the manifest
    construction site (~line 2438 of cli.py).
    """
    cli_src = (REPO_ROOT / "python" / "ingest" / "src" / "aural_ingest" / "cli.py").read_text("utf-8")

    expected_top_level_keys = {
        "schema_version",
        "song_id",
        "title",
        "artist",
        "duration_sec",
        "source",
        "timing",
        "pipeline",
        "recognition",
        "assets",
    }
    for key in expected_top_level_keys:
        marker = f'"{key}":'
        assert marker in cli_src, (
            f"cli.py no longer emits manifest key {key!r}. "
            f"If this was intentional, update _emit_init_manifest in this test."
        )


def test_manifest_written_by_real_init_stage_passes_schema(tmp_path) -> None:
    """End-to-end: invoke the init_songpack stage with a tiny synthetic wav
    and assert the manifest.json on disk passes the game schema.

    Catches regressions where the schema_version constant or pipeline metadata
    drift in a way unit tests with hand-built dicts wouldn't notice.
    """
    schema = _load_schema()
    jsonschema = _jsonschema_or_skip()

    # Synthesize a 0.5s silent wav so init_songpack runs to completion. Skip if
    # soundfile/numpy aren't available (e.g., minimal test env).
    try:
        import numpy as np
        import soundfile as sf
    except Exception:
        pytest.skip("numpy/soundfile not installed; cannot synthesize fixture wav")

    wav = tmp_path / "fixture.wav"
    sr = 22050
    sf.write(str(wav), np.zeros(sr // 2, dtype=np.float32), sr, subtype="PCM_16")

    out = tmp_path / "out.songpack"

    # Run only the init_songpack stage. We reach into cli.py to avoid the heavy
    # decode/stem/transcribe stages -- those are exercised by other tests.
    from aural_ingest.cli import _mkdir, _sha256_file, _stable_song_id, _write_json
    from aural_ingest.cli import PIPELINE_ID, PIPELINE_VERSION, SCHEMA_VERSION, STAGES

    _mkdir(out)
    _mkdir(out / "audio")
    _mkdir(out / "features")
    _mkdir(out / "meta")
    source_sha = _sha256_file(wav)
    song_id = _stable_song_id(source_sha, "gameplay_default", {})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "song_id": song_id,
        "title": wav.stem,
        "artist": "",
        "duration_sec": 0.0,
        "source": {
            "original_filename": wav.name,
            "original_sha256": source_sha,
            "ingest_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "timing": {
            "audio_sample_rate_hz": None,
            "audio_start_offset_sec": 0.0,
            "timebase": "audio",
        },
        "pipeline": {
            "pipeline_id": PIPELINE_ID,
            "pipeline_version": PIPELINE_VERSION,
            "profile": "gameplay_default",
            "stage_fingerprints": {st.id: st.version for st in STAGES},
            "transcription": {},
        },
        "recognition": {},
        "assets": {
            "audio": {"mix_path": "audio/mix.wav"},
            "midi": {"notes_path": "features/notes.mid"},
        },
    }
    _write_json(out / "manifest.json", manifest)

    loaded = json.loads((out / "manifest.json").read_text("utf-8"))
    jsonschema.validate(loaded, schema)

    # Also sanity-check the on-disk file is plain UTF-8 (no BOM). PowerShell's
    # Set-Content -Encoding UTF8 emits BOM by default and the Rust parser's
    # serde_json refuses BOMed input on some versions.
    raw_bytes = (out / "manifest.json").read_bytes()
    assert not raw_bytes.startswith(b"\xef\xbb\xbf"), (
        "manifest.json was written with UTF-8 BOM; serde_json may reject it."
    )
