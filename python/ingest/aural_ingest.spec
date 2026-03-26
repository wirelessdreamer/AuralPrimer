# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(globals().get("SPECPATH", Path.cwd())).resolve()
SRC_DIR = ROOT / "src"

COLLECT_PACKAGES = [
    "aural_ingest",
    "beartype",
    "demucs",
    "ema_pytorch",
    "einx",
    "librosa",
    "lightning_fabric",
    "lightning_utilities",
    "mir_eval",
    "mido",
    "mt3_infer",
    "numpy",
    "pretty_midi",
    "pytorch_lightning",
    "scipy",
    "soundfile",
    "tokenizers",
    "torch",
    "torchmetrics",
    "torchaudio",
    "torchvision",
    "transformers",
    "x_transformers",
]

SUBMODULE_PACKAGES = [
    "aural_ingest",
    "beartype",
    "demucs",
    "ema_pytorch",
    "einx",
    "mt3_infer",
    "pytorch_lightning",
    "torch",
    "torchmetrics",
    "torchaudio",
    "torchvision",
    "transformers",
    "x_transformers",
]

datas = []
binaries = []
hiddenimports = []
seen = set()


def _dedupe(items):
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


for package in COLLECT_PACKAGES:
    try:
        pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
        datas.extend(pkg_datas)
        binaries.extend(pkg_binaries)
        hiddenimports.extend(pkg_hiddenimports)
    except Exception:
        continue

for package in SUBMODULE_PACKAGES:
    try:
        hiddenimports.extend(collect_submodules(package))
    except Exception:
        continue

hiddenimports = _dedupe(hiddenimports)

a = Analysis(
    [str(SRC_DIR / "aural_ingest" / "cli.py")],
    pathex=[str(SRC_DIR)],
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
