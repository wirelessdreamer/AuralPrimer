"""A linked git worktree must still find the model packs.

A worktree's assets are not its own: model packs, build output and the portable
all live in the main checkout, while every search root the resolver derives --
from __file__, cwd, sys.executable -- stays inside the worktree. The pack is
then reported "not found in default search locations" and stem separation
degrades to `none`.

That failure is quiet and its consequences are not. Without separation the
guitar-split stage falls back to the raw mix and writes files named
lead_guitar.wav / rhythm_guitar.wav; instrument conditioning reads those names
as evidence of what is in the song and masks the transcriber to guitar. Four
solo-piano recordings imported that way came out with zero keys notes and the
whole part relabelled rhythm guitar.
"""
from __future__ import annotations

from pathlib import Path

from aural_ingest.cli import _default_demucs_modelpack_candidates, _git_main_worktree_root


def _make_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout with a linked worktree pointing back at it."""
    main = tmp_path / "MainCheckout"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / "dist" / "modelpacks").mkdir(parents=True)
    (main / "dist" / "modelpacks" / "demucs_6.zip").write_bytes(b"stub")

    wt = tmp_path / "worktrees" / "wt"
    (wt / "python" / "ingest" / "src" / "aural_ingest").mkdir(parents=True)
    (wt / ".git").write_text(
        f"gitdir: {(main / '.git' / 'worktrees' / 'wt').as_posix()}\n", encoding="utf-8"
    )
    return main, wt


def test_the_main_checkout_is_found_from_a_worktree(tmp_path):
    main, wt = _make_worktree(tmp_path)
    found = _git_main_worktree_root(wt / "python" / "ingest" / "src" / "aural_ingest")
    assert found == main


def test_an_ordinary_clone_adds_nothing(tmp_path):
    """`.git` as a directory means we are already at the root; nothing to add."""
    repo = tmp_path / "clone"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    assert _git_main_worktree_root(repo / "src") is None


def test_a_malformed_git_file_is_not_fatal(tmp_path):
    """Resolution is best-effort: a bad pointer must not break the import."""
    repo = tmp_path / "odd"
    (repo / "src").mkdir(parents=True)
    (repo / ".git").write_text("this is not a gitdir pointer\n", encoding="utf-8")
    assert _git_main_worktree_root(repo / "src") is None


def test_the_real_worktree_can_see_a_modelpack_path():
    """Against this checkout, whatever kind it is.

    In a worktree the candidate list must contain a path under the main
    checkout; in a plain clone this is trivially satisfied by the local roots.
    """
    candidates = _default_demucs_modelpack_candidates(["demucs_6"])
    assert candidates, "no candidate paths at all"
    assert any(c.name == "demucs_6.zip" for c in candidates)

    here = Path(__file__).resolve()
    main = _git_main_worktree_root(here.parent)
    if main is not None:
        assert any(main in c.parents for c in candidates), (
            "running from a worktree but no candidate points at the main checkout"
        )
