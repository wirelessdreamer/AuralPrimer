"""The collection probe must survive the modules it probes.

``conftest`` decides which test files to ignore by importing each one and
seeing whether it blows up on a missing dependency. That probe has to catch
every way a module can say "not here", and one of them is not an exception in
the ordinary sense: ``pytest.importorskip`` raises ``Skipped``, which derives
from ``BaseException``.

It escaped the probe's handlers, propagated out of conftest, and took the
entire collection with it -- so CI ran none of the ingest tests at all while
showing one red job that read like a single broken benchmark.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).with_name("conftest.py")


def _load_conftest():
    spec = importlib.util.spec_from_file_location("_conftest_under_test", _CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe(path: Path) -> bool:
    """Call the probe, turning anything it lets escape into a failure.

    This has to catch BaseException, not Exception, and the reason is the whole
    point of the test: the bug was a BaseException subclass escaping. Letting
    it propagate here would mark THIS test skipped rather than failed -- a
    regression test that quietly goes green is worth less than no test, and is
    how the original bug survived in the first place.
    """
    try:
        return _load_conftest()._module_imports(path)
    except BaseException as exc:  # noqa: BLE001
        raise AssertionError(
            f"_module_imports let {type(exc).__name__} escape; conftest would "
            "have died and taken the whole collection with it"
        ) from exc


def test_module_scope_importorskip_is_ignored_not_fatal(tmp_path):
    """The exact shape that broke it: importorskip at module scope."""
    mod = tmp_path / "test_needs_absent_dep.py"
    mod.write_text(
        "import pytest\n"
        "pytest.importorskip('a_module_that_is_not_installed_anywhere')\n",
        encoding="utf-8",
    )
    assert _probe(mod) is False


def test_a_missing_import_is_ignored(tmp_path):
    mod = tmp_path / "test_plain_missing.py"
    mod.write_text("import a_module_that_is_not_installed_anywhere\n", encoding="utf-8")
    assert _probe(mod) is False


def test_a_real_error_still_surfaces(tmp_path):
    """Ignoring a broken test file would hide it, so only import problems count."""
    mod = tmp_path / "test_broken.py"
    mod.write_text("raise ValueError('this module is genuinely broken')\n", encoding="utf-8")
    assert _probe(mod) is True


def test_a_healthy_module_is_collected(tmp_path):
    mod = tmp_path / "test_fine.py"
    mod.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert _probe(mod) is True
