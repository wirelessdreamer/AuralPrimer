from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


def _load_installer():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "install_rmvpe_checkpoint.py"
    spec = importlib.util.spec_from_file_location("_test_install_rmvpe_checkpoint", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_rmvpe_installer_requires_license_confirmation(tmp_path: Path) -> None:
    installer = _load_installer()
    source = tmp_path / "rmvpe.pt"
    source.write_bytes(b"checkpoint")

    try:
        installer.main(["--source-file", str(source), "--dest", str(tmp_path / "out")])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse should exit on parser.error
        raise AssertionError("expected argparse to reject missing --license-confirmed")


def test_rmvpe_installer_source_file_enforces_expected_sha256(tmp_path: Path) -> None:
    installer = _load_installer()
    source = tmp_path / "reviewed.pt"
    data = b"reviewed checkpoint"
    source.write_bytes(data)
    dest = tmp_path / "models"

    rc = installer.main(
        [
            "--source-file",
            str(source),
            "--dest",
            str(dest),
            "--expected-sha256",
            _sha256(data),
            "--license-confirmed",
        ]
    )

    assert rc == 0
    assert (dest / "rmvpe.pt").read_bytes() == data
    manifest = json.loads((dest / "rmvpe.checkpoint.json").read_text(encoding="utf-8"))
    assert manifest["filename"] == "rmvpe.pt"
    assert manifest["sha256"] == _sha256(data)
    assert manifest["license_confirmed"] is True
    assert manifest["source_kind"] == "file"


def test_rmvpe_installer_requires_expected_sha256(tmp_path: Path) -> None:
    installer = _load_installer()
    source = tmp_path / "reviewed.pt"
    source.write_bytes(b"checkpoint")

    try:
        installer.main(["--source-file", str(source), "--dest", str(tmp_path / "out"), "--license-confirmed"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse should exit on parser.error
        raise AssertionError("expected argparse to reject missing --expected-sha256")


def test_rmvpe_installer_rejects_invalid_expected_sha256(tmp_path: Path) -> None:
    installer = _load_installer()
    source = tmp_path / "reviewed.pt"
    source.write_bytes(b"checkpoint")

    try:
        installer.main(
            [
                "--source-file",
                str(source),
                "--dest",
                str(tmp_path / "out"),
                "--expected-sha256",
                "not-a-sha",
                "--license-confirmed",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse should exit on parser.error
        raise AssertionError("expected argparse to reject invalid --expected-sha256")


def test_rmvpe_installer_sha256_mismatch_leaves_target_untouched(tmp_path: Path) -> None:
    installer = _load_installer()
    source = tmp_path / "reviewed.pt"
    source.write_bytes(b"bad checkpoint")
    dest = tmp_path / "models"

    rc = installer.main(
        [
            "--source-file",
            str(source),
            "--dest",
            str(dest),
            "--expected-sha256",
            "0" * 64,
            "--license-confirmed",
        ]
    )

    assert rc == 1
    assert not (dest / "rmvpe.pt").exists()
    assert not (dest / "rmvpe.pt.part").exists()


def test_rmvpe_installer_source_url_passes_timeout_and_hashes_download(
    monkeypatch,
    tmp_path: Path,
) -> None:
    installer = _load_installer()
    data = b"downloaded checkpoint"
    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body
            self._offset = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size: int = -1) -> bytes:
            if self._offset >= len(self._body):
                return b""
            if size is None or size < 0:
                size = len(self._body) - self._offset
            chunk = self._body[self._offset : self._offset + size]
            self._offset += len(chunk)
            return chunk

    def fake_urlopen(url: str, timeout: float):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse(data)

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "models"

    rc = installer.main(
        [
            "--source-url",
            "https://example.invalid/reviewed/rmvpe.pt",
            "--dest",
            str(dest),
            "--timeout-sec",
            "7.5",
            "--expected-sha256",
            _sha256(data),
            "--license-confirmed",
        ]
    )

    assert rc == 0
    assert captured == {"url": "https://example.invalid/reviewed/rmvpe.pt", "timeout": 7.5}
    assert (dest / "rmvpe.pt").read_bytes() == data
    manifest = json.loads((dest / "rmvpe.checkpoint.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == _sha256(data)
    assert manifest["source_kind"] == "url"


def test_rmvpe_installer_rejects_non_https_source_url(tmp_path: Path) -> None:
    installer = _load_installer()
    dest = tmp_path / "models"

    rc = installer.main(
        [
            "--source-url",
            "http://example.invalid/rmvpe.pt",
            "--dest",
            str(dest),
            "--expected-sha256",
            "0" * 64,
            "--license-confirmed",
        ]
    )

    assert rc == 1
    assert not (dest / "rmvpe.pt").exists()
