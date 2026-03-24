# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPEC).resolve().parent
src_root = project_root / "src"
entry_path = src_root / "aural_ingest" / "cli.py"

datas = []
binaries = []
hiddenimports = []


def collect_optional(package_name):
    try:
        return collect_all(package_name)
    except Exception:
        return [], [], []


for package_name in (
    "torch",
    "torchaudio",
    "demucs",
    "julius",
    "openunmix",
    "dora_search",
    "hydra",
    "omegaconf",
    "antlr4",
    "lameenc",
    "retrying",
    "treetable",
):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_optional(package_name)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports


a = Analysis(
    [str(entry_path)],
    pathex=[str(src_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="aural_ingest",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
