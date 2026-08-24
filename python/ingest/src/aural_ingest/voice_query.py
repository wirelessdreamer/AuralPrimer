"""Turn a spoken search query into text, for the MR client's voice search.

The headset has no recogniser, and the library it is searching lives on this
machine anyway, so the audio comes here (see ``docs/mr-link-protocol.md`` §6).

Whisper runs through ``transformers``, which the sidecar already depends on for
other engines -- so voice search adds model weights but no new package. The
weights are fetched on first use rather than bundled: they are MIT-licensed and
could ship, but they would be dead weight in every install that never says a
word to the headset.

Deliberately a one-shot command rather than a resident service. The sidecar is
spawned per call by the Rust host, so the model loads each time; ``tiny.en``
is the default precisely because that load dominates the cost, and a search
query is a handful of words where a larger model buys accuracy nobody asked
for at a latency they would notice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Smallest English Whisper. See the module docstring for why the default is
#: the fastest to LOAD rather than the most accurate.
DEFAULT_MODEL = "openai/whisper-tiny.en"

#: Refuse anything longer than the protocol's cap. A client is free to send
#: whatever it likes, and an hour of audio would sit here transcribing while the
#: session's read loop waits for it.
MAX_SECONDS = 10.0


def transcribe_wav(path: Path, model_id: str = DEFAULT_MODEL) -> str:
    """Transcribe a mono 16 kHz WAV. Returns the text, possibly empty."""
    import numpy as np
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32", always_2d=False)

    if getattr(audio, "ndim", 1) > 1:
        # The protocol says mono, but a client that sent stereo should get an
        # answer rather than a shape error out of the model.
        audio = audio.mean(axis=1)

    seconds = len(audio) / float(rate or 1)
    if seconds > MAX_SECONDS:
        raise ValueError(
            f"query is {seconds:.1f}s; the protocol caps it at {MAX_SECONDS:.0f}s"
        )
    if len(audio) == 0:
        return ""

    import torch
    from transformers import pipeline

    # GPU when there is one. Loading is the dominant cost either way, but a
    # CUDA box still halves the transcribe step, and the check is free.
    device = 0 if torch.cuda.is_available() else -1

    asr = pipeline(
        "automatic-speech-recognition",
        model=model_id,
        device=device,
    )
    result: Any = asr({"raw": np.asarray(audio, dtype="float32"), "sampling_rate": rate})

    text = ""
    if isinstance(result, dict):
        text = str(result.get("text") or "")
    elif isinstance(result, list) and result:
        text = str(result[0].get("text") or "")

    return text.strip()


def cmd_transcribe_query(args: Any) -> int:
    """``aural_ingest transcribe-query`` -- one WAV in, one line of JSON out.

    Always exits 0 with a JSON object, even on failure. The caller is the Rust
    host, which turns this straight into a VOICE_RESULT frame; a non-zero exit
    with a stack trace on stderr would leave it with nothing to say to the
    headset except "it did not work".
    """
    path = Path(args.wav)
    if not path.is_file():
        print(json.dumps({"ok": False, "text": "", "error": f"no such file: {path}"}))
        return 0

    try:
        text = transcribe_wav(path, args.model or DEFAULT_MODEL)
    except Exception as exc:  # noqa: BLE001 - reported, not raised, see docstring
        print(json.dumps({"ok": False, "text": "", "error": str(exc)}))
        return 0

    print(json.dumps({"ok": True, "text": text, "error": None}))
    return 0
