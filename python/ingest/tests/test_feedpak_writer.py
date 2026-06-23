"""Tests for the feedpak WRITER (``aural_ingest.feedpak_writer``).

Writes the real ``psalm5.auralsong`` pack to a temp ``.feedpak`` and asserts:
  * ``manifest.yaml`` validates against the vendored manifest schema,
  * each notation + ``song_timeline.json`` validate against their schemas,
  * every manifest pointer (relpath) resolves to a real file,
  * notation round-trips the ``notes.mid`` pitches + onsets with no loss.
"""

from __future__ import annotations

from pathlib import Path

import pretty_midi
import pytest
import yaml

from aural_ingest import feedpak_validate, feedpak_writer

# The real, fully-populated test pack ships in the portable, outside this
# worktree. Skip gracefully if it isn't on disk.
_PSALM5 = Path("D:/AuralPrimer/AuralPrimerPortable/data/songs/psalm5.auralsong")


def _is_relpath(value: str) -> bool:
    return (
        not value.startswith("/")
        and "//" not in value
        and ":" not in value
        and "\\" not in value
        and ".." not in value.split("/")
    )


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory) -> dict:
    if not _PSALM5.exists():
        pytest.skip(f"test pack not present: {_PSALM5}")
    out = tmp_path_factory.mktemp("feedpak_out")
    return feedpak_writer.write_feedpak(_PSALM5, out)


def test_manifest_validates(written: dict) -> None:
    feedpak_dir: Path = written["feedpak_dir"]
    manifest = yaml.safe_load((feedpak_dir / "manifest.yaml").read_text("utf-8"))
    feedpak_validate.validate(manifest, "manifest.schema.json")
    assert manifest["feedpak_version"] == "1.11.0"
    assert manifest["arrangements"]
    assert manifest["stems"]


def test_notation_validates(written: dict) -> None:
    feedpak_dir: Path = written["feedpak_dir"]
    assert written["notation_files"]
    for rel in written["notation_files"]:
        notation = yaml.safe_load((feedpak_dir / rel).read_text("utf-8"))
        errors = feedpak_validate.iter_errors(notation, "notation.schema.json")
        assert not errors, f"{rel}: {errors}"


def test_song_timeline_validates(written: dict) -> None:
    feedpak_dir: Path = written["feedpak_dir"]
    timeline = yaml.safe_load((feedpak_dir / "song_timeline.json").read_text("utf-8"))
    errors = feedpak_validate.iter_errors(timeline, "song-timeline.schema.json")
    assert not errors, errors


def test_all_pointers_resolve(written: dict) -> None:
    """Every relpath in the manifest must resolve to a real file/dir."""
    feedpak_dir: Path = written["feedpak_dir"]
    manifest = written["manifest"]

    pointers: list[str] = []
    for arr in manifest["arrangements"]:
        if "notation" in arr:
            pointers.append(arr["notation"])
        if "file" in arr:
            pointers.append(arr["file"])
    for stem in manifest["stems"]:
        pointers.append(stem["file"])
    for key in ("song_timeline", "drum_tab", "aural_notes_mid", "aural_spectrogram",
                "aural_benchmark"):
        if isinstance(manifest.get(key), str):
            pointers.append(manifest[key])
    if isinstance(manifest.get("aural_refine_candidates"), dict):
        pointers.extend(manifest["aural_refine_candidates"].values())

    assert pointers
    for ptr in pointers:
        assert _is_relpath(ptr), f"not a POSIX relpath: {ptr}"
        assert (feedpak_dir / ptr).exists(), f"pointer does not resolve: {ptr}"


def test_notation_roundtrips_pitches_and_onsets(written: dict) -> None:
    """Notation must preserve every notes.mid pitch + onset (no loss)."""
    feedpak_dir: Path = written["feedpak_dir"]

    pm = pretty_midi.PrettyMIDI(str(_PSALM5 / "features" / "notes.mid"))

    for rel in written["notation_files"]:
        role = Path(rel).stem.replace("notation_", "")
        inst = next(
            (
                ins
                for ins in pm.instruments
                if not ins.is_drum
                and feedpak_writer._role_matches_instrument(role, ins.name)
            ),
            None,
        )
        assert inst is not None, f"no MIDI instrument matched role {role}"

        expected = sorted(
            (round(float(n.start), 4), int(n.pitch)) for n in inst.notes
        )

        notation = yaml.safe_load((feedpak_dir / rel).read_text("utf-8"))
        got: list[tuple[float, int]] = []
        for measure in notation["measures"]:
            for staff in measure.get("staves", {}).values():
                for voice in staff.get("voices", []):
                    for beat in voice.get("beats", []):
                        for note in beat.get("notes", []):
                            got.append((round(float(beat["t"]), 4), int(note["midi"])))
        got.sort()

        assert got == expected, (
            f"{role}: notation lost/changed notes "
            f"(expected {len(expected)}, got {len(got)})"
        )
