from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


STEM_ALIASES: dict[str, tuple[str, ...]] = {
    "kick": ("kick", "bd", "bass_drum"),
    "snare": ("snare", "sd"),
    "toms": ("toms", "tom", "tom_mid"),
    "hh": ("hh", "hi_hat", "hihat", "hat"),
    "ride": ("ride", "rd"),
    "crash": ("crash", "cy"),
}


def _repo_root() -> Path:
    raw = os.environ.get("AURAL_DRUM_STEMSEP_REPO", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "inference.py").is_file():
        return cwd
    raise RuntimeError("set AURAL_DRUM_STEMSEP_REPO to the MSST repository root")


def _config_path(checkpoint_path: Path) -> Path:
    raw = os.environ.get("AURAL_DRUM_STEMSEP_CONFIG", "").strip()
    if raw:
        candidate = Path(raw).expanduser().resolve()
        if not candidate.is_file():
            raise RuntimeError(f"AURAL_DRUM_STEMSEP_CONFIG is not a file: {candidate}")
        return candidate

    for candidate in (
        checkpoint_path.with_suffix(".yaml"),
        checkpoint_path.parent / "config_drumsep_mdx23c.yaml",
        checkpoint_path.parent / "aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.yaml",
    ):
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"could not locate DrumSep MDX23C config beside {checkpoint_path}")


def _resolve_input_path(raw: Any, *, env_root: str | None = None) -> Path:
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path.resolve()

    candidates = [Path.cwd() / path]
    if env_root:
        root_raw = os.environ.get(env_root, "").strip()
        if root_raw:
            candidates.append(Path(root_raw).expanduser() / path)
    candidates.append(Path(__file__).resolve().parents[3] / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return path.resolve()


def _run_msst(
    *,
    repo: Path,
    wav_path: Path,
    checkpoint_path: Path,
    config_path: Path,
    output_dir: Path,
) -> int:
    with tempfile.TemporaryDirectory(prefix="aural_drumsep_input_") as tmp:
        input_dir = Path(tmp)
        local_mix = input_dir / "drums.wav"
        shutil.copyfile(wav_path, local_mix)
        command = [
            sys.executable,
            str(repo / "inference.py"),
            "--model_type",
            "mdx23c",
            "--config_path",
            str(config_path),
            "--start_check_point",
            str(checkpoint_path),
            "--input_folder",
            str(input_dir),
            "--store_dir",
            str(output_dir),
            "--filename_template",
            "{instr}",
            "--bigshifts",
            os.environ.get("AURAL_DRUM_STEMSEP_BIGSHIFTS", "1"),
            "--pcm_type",
            "FLOAT",
        ]
        if os.environ.get("AURAL_DRUM_STEMSEP_FORCE_CPU", "1").strip().lower() not in {"0", "false", "no"}:
            command.append("--force_cpu")
        proc = subprocess.run(command, cwd=str(repo))
    return int(proc.returncode)


def _load_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(str(path), always_2d=True)
    mono = np.mean(audio, axis=1).astype(np.float32, copy=False)
    return mono, int(sample_rate)


def _frame_rms(audio: np.ndarray, frame_size: int, hop_size: int) -> np.ndarray:
    if audio.size < frame_size:
        padded = np.pad(audio, (0, frame_size - audio.size))
        return np.asarray([float(np.sqrt(np.mean(padded * padded)))], dtype=np.float32)
    frame_count = 1 + (audio.size - frame_size) // hop_size
    out = np.empty(frame_count, dtype=np.float32)
    for idx in range(frame_count):
        start = idx * hop_size
        frame = audio[start : start + frame_size]
        out[idx] = float(np.sqrt(np.mean(frame * frame)))
    return out


def _onsets_for_stem(stem_path: Path, stem: str) -> list[dict[str, Any]]:
    audio, sample_rate = _load_mono(stem_path)
    if audio.size == 0:
        return []

    frame_size = max(512, int(round(sample_rate * 0.046)))
    hop_size = max(128, int(round(sample_rate * 0.012)))
    rms = _frame_rms(audio, frame_size, hop_size)
    if rms.size == 0:
        return []

    max_rms = float(np.max(rms))
    if max_rms <= 1e-6:
        return []

    normalized = rms / max_rms
    diff = np.maximum(0.0, np.diff(np.concatenate([[0.0], normalized])))
    threshold = max(0.08, float(np.percentile(diff, 92)) * 0.75)
    min_gap_frames = max(1, int(round(0.045 * sample_rate / hop_size)))

    events: list[dict[str, Any]] = []
    last_idx = -min_gap_frames
    for idx, value in enumerate(diff):
        if value < threshold or idx - last_idx < min_gap_frames:
            continue
        left = diff[idx - 1] if idx > 0 else 0.0
        right = diff[idx + 1] if idx + 1 < diff.size else 0.0
        if value < left or value < right:
            continue
        velocity = int(max(1, min(127, round(float(normalized[idx]) * 127))))
        events.append(
            {
                "time": round((idx * hop_size) / sample_rate, 6),
                "stem": stem,
                "velocity": velocity,
                "duration": 0.05,
            }
        )
        last_idx = idx

    if not events and max_rms > 1e-4:
        idx = int(np.argmax(rms))
        events.append(
            {
                "time": round((idx * hop_size) / sample_rate, 6),
                "stem": stem,
                "velocity": int(max(1, min(127, round(float(normalized[idx]) * 127)))),
                "duration": 0.05,
            }
        )
    return events


def _find_stem_file(output_dir: Path, canonical_stem: str) -> Path | None:
    aliases = STEM_ALIASES[canonical_stem]
    for alias in aliases:
        for suffix in (".wav", ".flac"):
            candidate = output_dir / f"{alias}{suffix}"
            if candidate.is_file():
                return candidate
    for candidate in output_dir.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in {".wav", ".flac"}:
            continue
        normalized = candidate.stem.lower().replace("-", "_").replace(" ", "_")
        if normalized in aliases:
            return candidate
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_drum_stemsep_msst.py <request.json>", file=sys.stderr)
        return 2

    request_path = Path(sys.argv[1]).expanduser().resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    wav_path = _resolve_input_path(request["wav_path"], env_root="AURAL_MODEL_UPGRADE_EVIDENCE_ROOT")
    out_json = Path(str(request["out_json"])).expanduser().resolve()
    checkpoint_path = _resolve_input_path(request["checkpoint_path"])
    stems = [str(stem) for stem in request.get("stems") or []]

    if not wav_path.is_file():
        raise RuntimeError(f"input WAV is missing: {wav_path}")
    if not checkpoint_path.is_file():
        raise RuntimeError(f"checkpoint is missing: {checkpoint_path}")

    repo = _repo_root()
    config_path = _config_path(checkpoint_path)
    with tempfile.TemporaryDirectory(prefix="aural_drumsep_msst_") as tmp:
        output_dir = Path(tmp) / "separated"
        output_dir.mkdir(parents=True, exist_ok=True)
        returncode = _run_msst(
            repo=repo,
            wav_path=wav_path,
            checkpoint_path=checkpoint_path,
            config_path=config_path,
            output_dir=output_dir,
        )
        if returncode != 0:
            return returncode

        canonical_stems = [stem if stem != "hi_hat" else "hh" for stem in stems]
        if not canonical_stems:
            canonical_stems = ["kick", "snare", "toms", "hh", "ride", "crash"]
        events: list[dict[str, Any]] = []
        for stem in canonical_stems:
            if stem not in STEM_ALIASES:
                continue
            stem_path = _find_stem_file(output_dir, stem)
            if stem_path is None:
                continue
            event_stem = "hi_hat" if stem == "hh" else stem
            events.extend(_onsets_for_stem(stem_path, event_stem))

    events.sort(key=lambda item: (float(item["time"]), str(item["stem"])))
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"events": events}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
