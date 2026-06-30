"""Regression tests for the ``--out`` extension rewrite in ``cmd_import_dir``.

The bug fixed here: ``_convert_auralsong_to_feedpak`` used to rename the
written feedpak onto whatever path the caller passed as ``--out``, even when
the caller passed a ``.auralsong`` extension. The pipeline emits feedpak
content (manifest.yaml + arrangements/notation_*.json + aural/ subdir), but
Studio's Cleanup readiness check and the game's discovery loop both route by
file extension to pick the right reader. So a ``.auralsong`` folder
containing feedpak content reads as "Invalid (missing title)" in Studio.

These tests pin the new contract: the converter's final on-disk name uses
the ``.feedpak`` extension regardless of what extension the caller passed,
and emits a stderr notice when it had to rewrite the extension.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aural_ingest import cli as cli_mod


def _make_minimal_working_dir(working_dir: Path) -> None:
    """Create a tiny on-disk shape that ``write_feedpak`` will accept.

    We don't run the real import pipeline here -- we monkeypatch
    ``write_feedpak`` to return a stub directory. These tests only pin the
    rename / extension contract in ``_convert_auralsong_to_feedpak`` itself.
    """
    working_dir.mkdir(parents=True)
    (working_dir / "manifest.json").write_text("{}", encoding="utf-8")


def _stub_write_feedpak(working_dir: Path, parent: Path) -> dict[str, str]:
    """Stand-in for ``aural_ingest.feedpak_writer.write_feedpak``.

    Mimics writer output: creates a uniquely-named feedpak dir next to the
    working dir (avoids colliding with either the working dir itself or any
    pre-existing stale artifact in the parent), drops a manifest.yaml in it
    so the rename target is non-empty, and returns a summary in the schema
    the real writer uses. The converter under test is responsible for
    moving / renaming this dir to the final extension-rewritten name.
    """
    base_stem = working_dir.name
    if base_stem.endswith(".auralsong"):
        base_stem = base_stem[: -len(".auralsong")]
    elif base_stem.endswith(".feedpak"):
        base_stem = base_stem[: -len(".feedpak")]
    feedpak_dir = parent / f"{base_stem}.feedpak.writer_out"
    feedpak_dir.mkdir()
    (feedpak_dir / "manifest.yaml").write_text("feedpak_version: 1.11.0\n", encoding="utf-8")
    return {"feedpak_dir": str(feedpak_dir)}


@pytest.fixture
def stub_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "write_feedpak", _stub_write_feedpak)


def test_auralsong_out_extension_gets_rewritten_to_feedpak(
    tmp_path: Path,
    stub_writer: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Caller passes ``foo.auralsong`` -> on-disk artifact is ``foo.feedpak``.

    This is the regression case from the 2026-06-29 import-piano-psalms run.
    """
    working_dir = tmp_path / "psalm_10_why.auralsong"
    _make_minimal_working_dir(working_dir)

    final = cli_mod._convert_auralsong_to_feedpak(working_dir)

    assert final.name == "psalm_10_why.feedpak", (
        f"expected the on-disk artifact to use .feedpak, got {final.name!r}"
    )
    assert final.is_dir()
    assert (final / "manifest.yaml").is_file()
    # The original .auralsong directory should be gone -- otherwise Studio
    # picks up TWO entries (the new feedpak and the old empty auralsong) on
    # its next scan and the cleanup-readiness check fires on the stale one.
    assert not working_dir.exists(), (
        f"old working_dir {working_dir!r} should have been removed"
    )

    err = capsys.readouterr().err
    assert "notice:" in err and ".feedpak" in err, (
        "rewrite path should emit a stderr notice so callers can discover the new name"
    )


def test_feedpak_out_extension_is_a_no_op_passthrough(
    tmp_path: Path,
    stub_writer: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Caller passes ``foo.feedpak`` -> final artifact stays at that exact path.

    Verifies the rewrite branch is gated on the extension and doesn't fire
    when callers already speak the new format (Studio's Tauri side, the
    updated import_piano_psalms.py driver, etc.).
    """
    working_dir = tmp_path / "psalm_10_why.feedpak"
    _make_minimal_working_dir(working_dir)

    final = cli_mod._convert_auralsong_to_feedpak(working_dir)

    assert final == working_dir, (
        f"feedpak-extension input should land at the same path; "
        f"got {final!r} vs {working_dir!r}"
    )
    assert final.is_dir()
    assert (final / "manifest.yaml").is_file()

    err = capsys.readouterr().err
    assert "notice:" not in err, (
        "no rewrite notice should fire when the caller already passed .feedpak"
    )


def test_rewrite_collides_cleanly_with_existing_stale_artifact(
    tmp_path: Path,
    stub_writer: None,
) -> None:
    """A stale ``foo.feedpak`` next to ``foo.auralsong`` shouldn't break the rewrite.

    If a prior import left a partial ``.feedpak`` directory next to a freshly
    written ``.auralsong``, the converter should clobber the stale one rather
    than fail at the final rename. This is the failure mode that bit the
    in-place piano-psalm re-import when the script was inadvertently re-run.
    """
    working_dir = tmp_path / "psalm_10_why.auralsong"
    _make_minimal_working_dir(working_dir)

    stale = tmp_path / "psalm_10_why.feedpak"
    stale.mkdir()
    (stale / "stale_marker").write_text("from a prior run", encoding="utf-8")

    final = cli_mod._convert_auralsong_to_feedpak(working_dir)

    assert final.name == "psalm_10_why.feedpak"
    # The fresh feedpak should overwrite the stale one (no stale_marker
    # because the stub_writer only emits manifest.yaml).
    assert not (final / "stale_marker").exists()
    assert (final / "manifest.yaml").is_file()
