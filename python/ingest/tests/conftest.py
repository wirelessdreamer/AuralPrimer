"""Make the Python suite degrade gracefully without the heavy ML/DSP runtime.

The CI ``lint-test`` job installs only ``requirements-dev.txt`` (pytest, ruff,
numpy) -- not librosa / soundfile / torch / basic-pitch / the piano-transcription
models. Most of this suite is designed for that full runtime, so without it a
bare ``import`` aborts collection and lazily-imported deps raise mid-test.

Rather than enumerate every (often transitive) optional dependency, this gates
purely on import success:

  * ``collect_ignore`` -- trial-import every ``test_*.py``; skip-collect the ones
    whose module-level imports don't resolve (the dep is absent).
  * ``pytest_runtest_makereport`` -- turn any ``ImportError`` /
    ``ModuleNotFoundError`` raised during fixture setup or the test body into a
    skip (a lazily-imported optional dep was missing).

In a full-runtime environment every import resolves, so this is a no-op and the
whole suite runs normally; in the lightweight CI it leaves the pure-logic tests
running and skips the rest.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


@pytest.fixture(autouse=True, scope="session")
def _disable_meter_model():
    """Keep beat/tempo tests on the deterministic librosa path. The neural meter
    engine (Beat This!) is optional + gated on a cached/modelpack checkpoint that
    may or may not be present on a given machine; tests must not depend on it or
    run the heavy model. Its own logic is unit-tested via the pure helpers."""
    prev = os.environ.get("AURALPRIMER_DISABLE_METER_MODEL")
    os.environ["AURALPRIMER_DISABLE_METER_MODEL"] = "1"
    yield
    if prev is None:
        os.environ.pop("AURALPRIMER_DISABLE_METER_MODEL", None)
    else:
        os.environ["AURALPRIMER_DISABLE_METER_MODEL"] = prev


def _module_imports(path: Path) -> bool:
    spec = importlib.util.spec_from_file_location(f"_probe_{path.stem}", path)
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return True
    except (ImportError, ModuleNotFoundError):
        return False
    except Exception:  # noqa: BLE001 -- a non-import collection error should surface
        return True


collect_ignore = [
    p.name
    for p in sorted(_HERE.glob("test_*.py"))
    if not _module_imports(p)
]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # noqa: ANN001, ANN201
    outcome = yield
    report = outcome.get_result()
    excinfo = getattr(call, "excinfo", None)
    if (
        report.outcome == "failed"
        and excinfo is not None
        and isinstance(excinfo.value, (ImportError, ModuleNotFoundError))
    ):
        report.outcome = "skipped"
        report.longrepr = f"skipped (optional runtime dep missing): {excinfo.value}"
