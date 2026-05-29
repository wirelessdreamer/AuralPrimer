import sys
import types

import pytest

from aural_ingest.device import select_device


def test_select_device_honors_explicit_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURAL_TEST_DEVICE", "mps")
    # Override wins even though a GPU may be available.
    assert select_device("AURAL_TEST_DEVICE") == "mps"


def test_select_device_blank_override_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURAL_TEST_DEVICE", "   ")
    # Blank override falls through to detection; force CPU for determinism.
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert select_device("AURAL_TEST_DEVICE") == "cpu"


def test_select_device_defaults_to_cuda_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AURAL_TEST_DEVICE", raising=False)
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert select_device("AURAL_TEST_DEVICE") == "cuda"


def test_select_device_falls_back_to_cpu_when_cuda_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AURAL_TEST_DEVICE", raising=False)
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert select_device("AURAL_TEST_DEVICE") == "cpu"


def test_select_device_falls_back_to_cpu_when_torch_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AURAL_TEST_DEVICE", raising=False)
    # Simulate torch import failing.
    monkeypatch.setitem(sys.modules, "torch", None)
    assert select_device("AURAL_TEST_DEVICE") == "cpu"


def test_select_device_prefer_gpu_false_skips_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AURAL_TEST_DEVICE", raising=False)
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert select_device("AURAL_TEST_DEVICE", prefer_gpu=False) == "cpu"


def test_select_device_without_env_var_uses_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert select_device() == "cuda"
