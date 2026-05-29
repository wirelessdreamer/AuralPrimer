from __future__ import annotations

import contextlib
import importlib
import os
import tempfile
from pathlib import Path
from typing import Iterator

from aural_ingest.algorithms.piano_midi import decode_midi_notes
from aural_ingest.device import select_device
from aural_ingest.transcription import MelodicNote


def _allow_download() -> bool:
    return os.environ.get("AURAL_PIANO_PTI_ALLOW_DOWNLOAD", "").strip().lower() in {"1", "true", "yes"}


def _resolve_bundled_checkpoint() -> Path | None:
    from aural_ingest.transcription import (
        _default_basic_pitch_model_roots,
        resolve_piano_pti_checkpoint_path,
    )

    return resolve_piano_pti_checkpoint_path(_default_basic_pitch_model_roots())


def _checkpoint_path() -> str | None:
    raw = os.environ.get("AURAL_PIANO_PTI_CHECKPOINT", "").strip()
    if raw:
        checkpoint = Path(raw)
        if not checkpoint.is_file():
            raise RuntimeError(f"piano_pti checkpoint not found: {checkpoint}")
        return str(checkpoint)
    bundled = _resolve_bundled_checkpoint()
    if bundled is not None:
        return str(bundled)
    if _allow_download():
        return None
    raise RuntimeError(
        "piano_pti requires AURAL_PIANO_PTI_CHECKPOINT, a bundled checkpoint under "
        "<modelpack>/piano_pti/, or AURAL_PIANO_PTI_ALLOW_DOWNLOAD=1 for the "
        "upstream downloader"
    )


def _device() -> str:
    """Pick the inference device for PTI.

    Honors an explicit ``AURAL_PIANO_DEVICE`` override; otherwise defaults to
    CUDA when a GPU is available (the project standard -- matches the demucs
    separation path), falling back to CPU. PTI on the RTX 5090 is ~5-10x
    faster than CPU, so defaulting to cuda avoids the multi-hour CPU runs.
    """
    return select_device("AURAL_PIANO_DEVICE")


@contextlib.contextmanager
def _torch_load_compat() -> Iterator[None]:
    """Allow loading the Kong / Edwards-robust checkpoint under PyTorch >= 2.6.

    PyTorch 2.6 flipped ``torch.load``'s ``weights_only`` default to ``True``,
    which refuses to unpickle the numpy arrays embedded in the Kong-style
    checkpoint. ``piano_transcription_inference`` calls ``torch.load`` without
    passing ``weights_only=False``, so we monkey-patch the default for the
    duration of the load. The Zenodo checkpoint is a known artifact downloaded
    by the bundled script with a verified SHA-256, so disabling weights-only
    is safe here.
    """
    try:
        torch = importlib.import_module("torch")
    except Exception:
        # No torch installed -- piano_transcription_inference would have
        # failed earlier; let the caller surface that error.
        yield
        return

    original = torch.load

    def _load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return original(*args, **kwargs)

    torch.load = _load
    try:
        yield
    finally:
        torch.load = original


def _parse_threshold(env_var: str) -> float | None:
    """Read a [0, 1] float threshold from an env var; return None when unset or invalid.

    Public so the consensus wrapper + tests can share the parsing rules.
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not (0.0 <= value <= 1.0):
        return None
    return value


def _apply_thresholds(transcriptor) -> None:
    """Apply env-var threshold overrides to a constructed ``PianoTranscription``.

    PTI exposes per-instance ``onset_threshold`` / ``offset_threshod`` (sic) /
    ``frame_threshold`` attributes that the post-processor reads each call.
    Raising ``onset_threshold`` from the default 0.3 to ~0.45 trades a small
    amount of recall for a large drop in low-confidence ghost onsets -- the
    main "tons of noise" failure mode on Demucs-separated keys stems where
    synth bleed and reverb tails get interpreted as real onsets.

    The upstream attribute name ``offset_threshod`` is misspelled in the
    package; we honor that spelling so the override actually lands.
    """
    onset = _parse_threshold("AURAL_PIANO_PTI_ONSET_THRESHOLD")
    if onset is not None:
        transcriptor.onset_threshold = onset

    offset = _parse_threshold("AURAL_PIANO_PTI_OFFSET_THRESHOLD")
    if offset is not None:
        # Upstream attribute is intentionally misspelled.
        transcriptor.offset_threshod = offset

    frame = _parse_threshold("AURAL_PIANO_PTI_FRAME_THRESHOLD")
    if frame is not None:
        transcriptor.frame_threshold = frame


def _load_audio(stem_path: Path, sample_rate: int):
    librosa = importlib.import_module("librosa")
    audio, _sr = librosa.load(path=str(stem_path), sr=sample_rate, mono=True)
    return audio


def _build_transcriptor():
    """Construct + configure a PianoTranscription instance.

    Factored out so the consensus path can reuse a single loaded model
    across the stem + full-mix passes (the Edwards-robust checkpoint is
    ~165 MB and the model build dominates per-call cost when ``device=cpu``).
    """
    pti = importlib.import_module("piano_transcription_inference")
    checkpoint_path = _checkpoint_path()
    transcriptor_cls = getattr(pti, "PianoTranscription")

    # Default model_type is "Note_pedal" which expects ``checkpoint['model']
    # = {'note_model': ..., 'pedal_model': ...}``. The Edwards-robust Kong
    # checkpoint omits ``pedal_model`` (and the original Kong checkpoint
    # works with the underlying ``Regress_onset_offset_frame_velocity_CRNN``
    # too), so we pin to the no-pedal variant.
    model_type = os.environ.get(
        "AURAL_PIANO_PTI_MODEL_TYPE", "Regress_onset_offset_frame_velocity_CRNN"
    ).strip() or "Regress_onset_offset_frame_velocity_CRNN"

    sample_rate = int(getattr(pti, "sample_rate"))

    with _torch_load_compat():
        transcriptor = transcriptor_cls(
            model_type=model_type,
            device=_device(),
            checkpoint_path=checkpoint_path,
        )
    _apply_thresholds(transcriptor)
    return transcriptor, sample_rate


def _transcribe_audio(transcriptor, audio, instrument: str) -> list[MelodicNote]:
    with tempfile.TemporaryDirectory(prefix="aural_pti_") as tmp:
        out_midi = Path(tmp) / "pti.mid"
        transcriptor.transcribe(audio, str(out_midi))
        if not out_midi.is_file():
            raise RuntimeError("piano_pti completed without writing a MIDI file")
        return decode_midi_notes(out_midi, instrument=instrument)


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
) -> list[MelodicNote]:
    try:
        importlib.import_module("piano_transcription_inference")
        importlib.import_module("librosa")
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "piano_pti requires the optional 'piano_transcription_inference' package in the ingest runtime"
        ) from exc

    transcriptor, sample_rate = _build_transcriptor()
    audio = _load_audio(stem_path, sample_rate)
    return _transcribe_audio(transcriptor, audio, instrument)


def merge_consensus(
    stem_notes: list[MelodicNote],
    mix_notes: list[MelodicNote],
    *,
    onset_tolerance_sec: float = 0.10,
    pitch_tolerance_semitones: int = 2,
) -> list[MelodicNote]:
    """Keep stem notes that have a matching onset in the mix transcription.

    PTI trained on solo piano hallucinates onsets on Demucs-separated keys
    stems whenever there's bleed or reverb the network has never seen. The
    same network run against the *full mix* has correct harmonic context
    and is less likely to invent notes -- it picks up other instruments,
    but those typically don't fall at the same onset times as the stem's
    hallucinations.

    Strategy: for each onset detected on the stem, require a corresponding
    onset on the mix within ``onset_tolerance_sec`` and pitch within
    ``pitch_tolerance_semitones`` (octaves DO NOT match -- the mix and
    stem must agree on register). Notes only the stem saw are dropped as
    likely false positives.

    Velocity / pitch / duration come from the stem (the cleaner spectral
    profile gives better velocity); the mix is used only as a gate.

    Pure function, no PTI invocation -- safe to unit-test without GPU /
    checkpoint.
    """
    if not stem_notes or not mix_notes:
        # No stem -> nothing to filter. No mix -> nothing corroborated
        # everything, so drop the whole list (the whole point of the gate).
        return []
    # Defensive: callers may pass MelodicNote-likes. We only need t_on + pitch.
    mix_sorted = sorted(mix_notes, key=lambda n: getattr(n, "t_on", 0.0))
    mix_onsets: list[tuple[float, int]] = [
        (float(getattr(n, "t_on", 0.0)), int(getattr(n, "pitch", -1))) for n in mix_sorted
    ]
    out: list[MelodicNote] = []
    j_lo = 0
    for note in stem_notes:
        t = float(getattr(note, "t_on", 0.0))
        p = int(getattr(note, "pitch", -1))
        # Advance the lower-bound cursor: any mix onset older than
        # (t - tolerance) can never match this or any later stem note.
        while j_lo < len(mix_onsets) and mix_onsets[j_lo][0] < t - onset_tolerance_sec:
            j_lo += 1
        # Scan forward until past the tolerance window; look for a pitch match.
        matched = False
        j = j_lo
        while j < len(mix_onsets) and mix_onsets[j][0] <= t + onset_tolerance_sec:
            if abs(mix_onsets[j][1] - p) <= pitch_tolerance_semitones:
                matched = True
                break
            j += 1
        if matched:
            out.append(note)
    return out


def _find_mix_audio(stem_path: Path) -> Path | None:
    """Locate the full-mix audio for a stem in a standard SongPack layout.

    SongPacks store stems under ``<pack>/audio/stems/<inst>.wav`` and the
    full mix at ``<pack>/audio/mix.{wav,mp3,ogg}``. We walk up from the
    stem and look for the first existing mix file.
    """
    if not stem_path.is_file():
        return None
    audio_dir = stem_path.parent.parent  # stems/ -> audio/
    if audio_dir.name != "audio":
        return None
    for ext in ("wav", "mp3", "ogg", "flac"):
        candidate = audio_dir / f"mix.{ext}"
        if candidate.is_file():
            return candidate
    return None


def transcribe_consensus(
    stem_path: Path,
    *,
    instrument: str = "keys",
    mix_path: Path | None = None,
    onset_tolerance_sec: float = 0.10,
    pitch_tolerance_semitones: int = 2,
) -> list[MelodicNote]:
    """Run PTI on both the keys stem and the full mix, intersect onsets.

    Returns the consensus list of notes -- those the stem detected AND that
    a separate pass on the full mix corroborated within the onset/pitch
    tolerances. The stem pass is authoritative for velocity and duration;
    the mix pass is used purely as a gate.

    Falls back to a plain stem-only transcription if ``mix_path`` can't be
    located (caller passed ``None`` and the stem isn't in a standard
    SongPack layout). Single PianoTranscription instance is reused across
    the two passes to avoid reloading the 165 MB checkpoint.
    """
    try:
        importlib.import_module("piano_transcription_inference")
        importlib.import_module("librosa")
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "piano_pti requires the optional 'piano_transcription_inference' package in the ingest runtime"
        ) from exc

    resolved_mix = mix_path if mix_path is not None else _find_mix_audio(stem_path)

    transcriptor, sample_rate = _build_transcriptor()
    stem_audio = _load_audio(stem_path, sample_rate)
    stem_notes = _transcribe_audio(transcriptor, stem_audio, instrument)

    if resolved_mix is None:
        # No mix available -- behave like the plain stem pass so the
        # algorithm runner can still consume the result.
        return stem_notes

    mix_audio = _load_audio(resolved_mix, sample_rate)
    mix_notes = _transcribe_audio(transcriptor, mix_audio, instrument)

    return merge_consensus(
        stem_notes,
        mix_notes,
        onset_tolerance_sec=onset_tolerance_sec,
        pitch_tolerance_semitones=pitch_tolerance_semitones,
    )
