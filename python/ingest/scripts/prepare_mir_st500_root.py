from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "python" / "ingest" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aural_ingest.dataset_adapters.mir_st500 import diagnose_corpus  # noqa: E402


DEFAULT_SOURCE_REPO = REPO_ROOT / ".external" / "singing_transcription_ICASSP2021"
DEFAULT_TARGET_ROOT = Path(r"E:\AudioSourceOfTruthData\extracted\mir_st500")
DEFAULT_REPORT = REPO_ROOT / "benchmarks" / "vocals" / "mir_st500_preparation_status.json"
DATASET_DIR = "MIR-ST500_20210206"
METADATA_FILES = (
    "MIR-ST500_corrected.json",
    "MIR-ST500_link.json",
    "metadata.csv",
    "README",
)
DEPENDENCIES = ("yt_dlp", "youtube_dl", "spleeter", "tensorflow")
VOCAL_FILENAMES = ("Vocal.wav", "Vocals.wav", "vocal.wav", "vocals.wav")
MIXTURE_FILENAMES = ("Mixture.wav", "Mixture.mp3", "mixture.wav", "mix.wav")


def _copy_metadata(source_repo: Path, target_root: Path) -> list[str]:
    source_dataset = source_repo / DATASET_DIR
    target_dataset = target_root / DATASET_DIR
    copied: list[str] = []
    target_dataset.mkdir(parents=True, exist_ok=True)
    for filename in METADATA_FILES:
        source = source_dataset / filename
        if not source.is_file():
            continue
        target = target_dataset / filename
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def _load_annotation_ids(target_root: Path) -> list[int]:
    candidates = (
        target_root / "MIR-ST500_corrected.json",
        target_root / DATASET_DIR / "MIR-ST500_corrected.json",
    )
    annotation_json = next((path for path in candidates if path.is_file()), None)
    if annotation_json is None:
        return []
    try:
        payload = json.loads(annotation_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    ids: list[int] = []
    for raw in payload:
        try:
            ids.append(int(str(raw)))
        except ValueError:
            continue
    return sorted(ids)


def _song_split(song_id: int) -> str:
    return "train" if song_id <= 400 else "test"


def _audio_search_roots(target_root: Path, song_id: int) -> tuple[Path, ...]:
    split = _song_split(song_id)
    names = (str(song_id), f"{song_id:03d}")
    roots: list[Path] = []
    for name in names:
        roots.append(target_root / split / name)
        roots.append(target_root / name)
    return tuple(dict.fromkeys(roots))


def _find_audio(target_root: Path, song_id: int, filenames: tuple[str, ...]) -> Path | None:
    for root in _audio_search_roots(target_root, song_id):
        for filename in filenames:
            candidate = root / filename
            if candidate.is_file():
                return candidate
    return None


def _missing_cases(
    target_root: Path,
    *,
    song_ids: list[int],
    split: str,
    filenames: tuple[str, ...],
) -> list[str]:
    missing: list[str] = []
    for song_id in song_ids:
        if split != "all" and _song_split(song_id) != split:
            continue
        if _find_audio(target_root, song_id, filenames) is None:
            missing.append(f"mir_st500:{song_id:03d}")
    return missing


def _dependency_status() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in DEPENDENCIES}


def _external_dependency_status(python: Path | None) -> dict[str, Any] | None:
    if python is None:
        return None
    status: dict[str, Any] = {
        "python": str(python),
        "python_exists": python.exists(),
        "python_is_file": python.is_file(),
        "dependencies": {name: False for name in DEPENDENCIES},
        "returncode": None,
        "stderr_tail": "",
    }
    if not python.is_file():
        return status
    code = (
        "import importlib.util, json; "
        f"mods={list(DEPENDENCIES)!r}; "
        "print(json.dumps({m: importlib.util.find_spec(m) is not None for m in mods}, sort_keys=True))"
    )
    proc = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    status["returncode"] = int(proc.returncode)
    status["stderr_tail"] = (proc.stderr or "")[-2000:]
    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout)
            if isinstance(payload, dict):
                status["dependencies"] = {
                    name: bool(payload.get(name))
                    for name in DEPENDENCIES
                }
        except json.JSONDecodeError:
            status["stderr_tail"] = (status["stderr_tail"] + "\ninvalid dependency JSON").strip()
    return status


def _dependencies_ready(status: dict[str, bool]) -> bool:
    return bool(status) and all(bool(value) for value in status.values())


def _external_dependencies_ready(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    dependencies = status.get("dependencies")
    return (
        status.get("python_is_file") is True
        and status.get("returncode") == 0
        and isinstance(dependencies, dict)
        and _dependencies_ready({name: bool(dependencies.get(name)) for name in DEPENDENCIES})
    )


def build_report(
    source_repo: Path,
    target_root: Path,
    *,
    copied_metadata: list[str],
    dependency_python: Path | None = None,
) -> dict[str, Any]:
    ids = _load_annotation_ids(target_root)
    train_ids = [song_id for song_id in ids if song_id <= 400]
    test_ids = [song_id for song_id in ids if song_id >= 401]
    test_vocal = diagnose_corpus(target_root, split="test", variant="vocal")
    test_mixture = diagnose_corpus(target_root, split="test", variant="mixture")
    missing_test_vocals = _missing_cases(
        target_root,
        song_ids=ids,
        split="test",
        filenames=VOCAL_FILENAMES,
    )
    missing_test_mixtures = _missing_cases(
        target_root,
        song_ids=ids,
        split="test",
        filenames=MIXTURE_FILENAMES,
    )
    gate_ready = (
        bool(test_ids)
        and int(test_vocal.get("emitted_count") or 0) == len(test_ids)
        and not missing_test_vocals
    )
    active_dependencies = _dependency_status()
    external_dependency_python = _external_dependency_status(dependency_python)
    active_dependencies_ready = _dependencies_ready(active_dependencies)
    external_dependencies_ready = _external_dependencies_ready(external_dependency_python)
    missing_test_audio = bool(missing_test_vocals or missing_test_mixtures)
    return {
        "source_repo": str(source_repo),
        "target_root": str(target_root),
        "copied_metadata": copied_metadata,
        "annotation_count": len(ids),
        "train_annotation_count": len(train_ids),
        "test_annotation_count": len(test_ids),
        "dependencies": active_dependencies,
        "dependencies_ready": active_dependencies_ready,
        "external_dependency_python": external_dependency_python,
        "external_dependencies_ready": external_dependencies_ready,
        "reconstruction_dependencies_ready": active_dependencies_ready or external_dependencies_ready,
        "audio_source_review_required": True,
        "audio_reconstruction_required": missing_test_audio,
        "audio_reconstruction_status": "missing_test_audio" if missing_test_audio else "ready",
        "test_vocal_diagnostics": test_vocal,
        "test_mixture_diagnostics": test_mixture,
        "missing_test_vocal_case_count": len(missing_test_vocals),
        "missing_test_vocal_case_ids": missing_test_vocals,
        "missing_test_mixture_case_count": len(missing_test_mixtures),
        "missing_test_mixture_case_ids": missing_test_mixtures,
        "gate_ready": gate_ready,
        "required_gate_command": (
            r"D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe "
            r"benchmarks\vocals\run_mir_st500_vocals.py "
            r"--split test --variant vocal --algorithm melodic_rmvpe --write-gate-evidence --progress"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, default=DEFAULT_SOURCE_REPO)
    parser.add_argument("--target-root", type=Path, default=DEFAULT_TARGET_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--dependency-python",
        type=Path,
        default=None,
        help="Optional external Python executable to inspect for yt_dlp/spleeter/tensorflow.",
    )
    parser.add_argument(
        "--copy-metadata",
        action="store_true",
        help="Copy official MIR-ST500 metadata files from the source checkout into the target root.",
    )
    args = parser.parse_args(argv)

    source_repo = args.source_repo.expanduser().resolve()
    target_root = args.target_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    copied_metadata: list[str] = []

    if args.copy_metadata:
        if not (source_repo / DATASET_DIR).is_dir():
            raise SystemExit(f"missing MIR-ST500 metadata checkout: {source_repo / DATASET_DIR}")
        copied_metadata = _copy_metadata(source_repo, target_root)

    dependency_python = args.dependency_python.expanduser().resolve() if args.dependency_python else None
    report = build_report(
        source_repo,
        target_root,
        copied_metadata=copied_metadata,
        dependency_python=dependency_python,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    if not report["gate_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
