"""Research-only adapter: Magenta Onsets & Frames E-GMD drum transcription.

**STATUS: RESEARCH / BENCHMARK ONLY -- NOT A SHIPPABLE ENGINE.**

This wraps Google Magenta's pretrained Onsets-and-Frames *drums* checkpoint
(trained on E-GMD) purely so it can be scored head-to-head against our DSP
drum stack via ``ground_truth_benchmark``. It is deliberately NOT registered
as a default in ``KNOWN_DRUM_ENGINES`` because:

  * The Magenta *code* is Apache-2.0, but the *checkpoint's own license is
    unstated* -- the ``e-gmd_checkpoint.zip`` ships no LICENSE and no license
    statement accompanies the download (raw GCS object). Shipping the weights
    would be a licensing risk. Benchmarking with them is fine.
  * It is a TF1-era estimator model (``tf.compat.v1`` / archived magenta repo)
    that cannot coexist with this package's Python 3.13 + onnxruntime/torch
    environment. It runs only in a separate pinned Python 3.10 + TensorFlow
    2.9 + magenta 2.1.4 venv.

Because of the above, this module NEVER imports tensorflow / magenta at import
time. ``transcribe()`` shells out to an external inference venv (whose Python
interpreter is given by the ``AURAL_MAGENTA_PY`` env var) and parses the
resulting MIDI into our canonical ``DrumEvent`` list. If that venv / checkpoint
isn't configured, it raises a clear ``RuntimeError`` -- it can never break the
sidecar just by being importable.

Setup + benchmark numbers: ``benchmarks/drums/magenta_egmd_anchor.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from aural_ingest.transcription import DrumEvent

# Env vars that point this adapter at the external research venv + checkpoint.
_ENV_PY = "AURAL_MAGENTA_PY"          # path to the py3.10 magenta venv python.exe
_ENV_CKPT = "AURAL_MAGENTA_EGMD_CKPT"  # path to the extracted e-gmd checkpoint dir


def _require_env() -> tuple[Path, Path]:
    py = os.environ.get(_ENV_PY)
    ckpt = os.environ.get(_ENV_CKPT)
    if not py or not Path(py).exists():
        raise RuntimeError(
            f"magenta_egmd_drums is research-only and needs a separate TF1/magenta "
            f"venv. Set {_ENV_PY} to that venv's python.exe (Python 3.10 + "
            f"tensorflow==2.9.3 + magenta==2.1.4). See benchmarks/drums/magenta_egmd_anchor.md."
        )
    if not ckpt or not Path(ckpt).exists():
        raise RuntimeError(
            f"Set {_ENV_CKPT} to the extracted E-GMD checkpoint dir (containing "
            f"model.ckpt-569400.*). The weights are NOT shipped with this repo "
            f"(unstated license). See benchmarks/drums/magenta_egmd_anchor.md."
        )
    return Path(py), Path(ckpt)


# Inference driver executed *inside* the magenta venv. Kept as a string so this
# module has zero import-time dependency on tensorflow/magenta.
_DRIVER = r"""
import os, sys, json
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
import tensorflow.compat.v1 as tf
tf.disable_v2_behavior()
from magenta.models.onsets_frames_transcription import (
    configs, data, infer_util, train_util, audio_label_data_utils)
from note_seq import midi_io
from note_seq.protobuf import music_pb2
import six

ckpt_dir, wav_path, out_json = sys.argv[1], sys.argv[2], sys.argv[3]

def create_example(filename, sample_rate):
    wav_data = tf.gfile.Open(filename, 'rb').read()
    lst = list(audio_label_data_utils.process_record(
        wav_data=wav_data, sample_rate=sample_rate, ns=music_pb2.NoteSequence(),
        example_id=six.ensure_text(filename, 'utf-8'), min_length=0, max_length=-1,
        allow_empty_notesequence=True, load_audio_with_librosa=False))
    assert len(lst) == 1
    return lst[0].SerializeToString()

config = configs.CONFIG_MAP['drums']
hparams = config.hparams
hparams.parse('')
hparams.batch_size = 1
hparams.truncated_length_secs = 0

with tf.Graph().as_default():
    examples = tf.placeholder(tf.string, [None])
    dataset = data.provide_batch(examples=examples, preprocess_examples=True,
        params=hparams, is_training=False, shuffle_examples=False,
        skip_n_initial_records=0)
    estimator = train_util.create_estimator(config.model_fn, os.path.expanduser(ckpt_dir), hparams)
    it = tf.data.make_initializable_iterator(dataset)
    nxt = it.get_next()
    with tf.Session() as sess:
        sess.run([tf.initializers.global_variables(), tf.initializers.local_variables()])
        sess.run(it.initializer, {examples: [create_example(wav_path, hparams.sample_rate)]})
        def tdata(params):
            del params
            return tf.data.Dataset.from_tensors(sess.run(nxt))
        input_fn = infer_util.labels_to_features_wrapper(tdata)
        preds = list(estimator.predict(input_fn, yield_single_examples=False))
        seq = music_pb2.NoteSequence.FromString(preds[0]['sequence_predictions'][0])

events = [[float(n.start_time), int(n.pitch), int(n.velocity) or 64] for n in seq.notes]
json.dump(events, open(out_json, 'w'))
"""


def transcribe(stem_path: Path) -> list[DrumEvent]:
    """Transcribe one drum stem with the Magenta E-GMD checkpoint (research-only).

    Shells out to the external magenta venv, which writes a JSON list of
    ``[onset_sec, midi_pitch, velocity]`` that we lift into ``DrumEvent``s.
    The model emits the 8-hit reduced drum vocabulary (base pitches 36/38/48/
    46/51/53/49/75), which our benchmark drum-class map already understands.
    """
    py, ckpt = _require_env()
    stem_path = Path(stem_path)

    with tempfile.TemporaryDirectory() as td:
        driver = Path(td) / "_driver.py"
        driver.write_text(_DRIVER)
        out_json = Path(td) / "events.json"
        proc = subprocess.run(
            [str(py), str(driver), str(ckpt), str(stem_path), str(out_json)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not out_json.exists():
            raise RuntimeError(
                f"magenta inference failed for {stem_path.name} "
                f"(rc={proc.returncode}): {proc.stderr[-800:]}"
            )
        raw = json.loads(out_json.read_text())

    return [DrumEvent(time=float(t), note=int(p), velocity=int(v)) for t, p, v in raw]
