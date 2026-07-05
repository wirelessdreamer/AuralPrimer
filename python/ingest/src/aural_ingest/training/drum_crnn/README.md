# Drum CRNN — in-house training harness

Trains **our own** 5-class drum-onset transcription model on the **Expanded
Groove MIDI Dataset (E-GMD)** so the product can ship its own weights instead
of depending on license-blocked models (ADTOF is CC BY-NC-SA; the Magenta drum
checkpoint has no stated license). This directory is the **harness** — feature
extraction, target building, the model, the training loop, decoding, and an
ONNX export path — proven to run end to end on a tiny smoke sample.

Nothing here is wired into the runtime transcription pipeline
(`KNOWN_DRUM_ENGINES` in `transcription.py`). It is offline tooling that
produces an exportable ONNX model; integrating a *trained* model into the
benchmark/inference path is a separate follow-up.

---

## Architecture

A compact **convolutional-recurrent neural network (CRNN)** in the standard
automatic-drum-transcription (ADT) family:

```
log-mel frames (T × M)
  → conv stack: 3 × [Conv2d 3×3 → BatchNorm → ReLU → MaxPool(1×2 on the mel axis)]
  → collapse (channels × mel') into a per-frame feature vector
  → bi-directional GRU over the frame sequence (temporal context)
  → per-class Linear → one logit per drum class per frame
  → sigmoid (multi-label; one independent probability per class per frame)
  → per-class peak-pick → onset events
```

Pooling is applied on the **mel axis only** (`MaxPool(1×2)`), so the time axis
keeps exactly one output step per input frame and the GRU sees one step per
feature frame — the model predicts a per-frame activation for every class.

### Provenance / citation

The architecture is **reimplemented from the public description** of the CRNN
used for automatic drum transcription in:

- R. Vogl, M. Dorfer, G. Widmer, P. Knees, *"Drum Transcription via Joint Beat
  and Drum Modeling using Convolutional Recurrent Neural Networks"*, Proc. of
  the 18th International Society for Music Information Retrieval Conf. (ISMIR),
  2017; and the follow-up *"Towards multi-instrument drum transcription"*,
  Proc. of the 21st International Conf. on Digital Audio Effects (DAFx), 2018.
- Related CRNN-for-ADT work: C. Southall, R. Stables, J. Hockman,
  *"Automatic Drum Transcription for Polyphonic Recordings Using Soft
  Attention Mechanisms and Convolutional Neural Networks"*, ISMIR 2017.

The "log-mel front-end → conv → BiRNN → per-class sigmoid activations →
peak-pick" design and the target-smoothing (label-widening) trick are standard
and described in those papers. **No code was copied from the ADTOF repository
(CC BY-NC-SA) or any NC / GPL source.** The implementation here is plain
PyTorch written against the public description.

### Size

The default `ModelConfig` is ~**300 K** parameters (well under the ~5 M
sidecar budget); the smoke config shrinks it further to ~75 K. A CPU forward
pass on an 8-second clip (~802 frames) is roughly **50 ms**.

---

## Feature configuration (`config.py :: FeatureConfig`)

Deterministic log-mel spectrogram. The same WAV and config always yield the
same array.

| field         | default | notes |
|---------------|---------|-------|
| `sample_rate` | 22050   | mono |
| `n_fft`       | 1024    | STFT window |
| `hop_length`  | 220     | → **100.2 frames/s** (~10 ms/frame) |
| `n_mels`      | 84      | mel bands (model input feature dim) |
| `fmin`        | 30.0    | Hz |
| `fmax`        | 11025.0 | Hz (Nyquist at 22.05 kHz) |
| `log_offset`  | 1e-6    | floor before `log()` |

`librosa.feature.melspectrogram(..., power=2.0, center=True)`, then
`log(mel_power + log_offset)`. Output is `(n_frames, n_mels)` float32, with
`n_frames = 1 + n_samples // hop_length` (the `center=True` frame count).

**Targets** (`TargetConfig`): each ground-truth onset maps to a frame via
`round(t_sec · sr / hop)` and writes a triangular bump of half-width
`smoothing_frames` (default ±1 frame) into its class column, taking the max
where bumps overlap. GM notes fold into the 5-class taxonomy using the
production benchmark note map (`_BENCHMARK_NOTE_TO_CLASS`), collapsing
`tom1/tom2/tom3 → toms` and `crash/ride → cymbals`.

### Class taxonomy (fixed order)

```
("kick", "snare", "hi_hat", "toms", "cymbals")
```

Matches `STANDARD_5CLASS_DRUM_VOCABULARY` in `algorithms/_common.py`. Output
channel index *i* corresponds to `CLASSES[i]`. Canonical GM MIDI on decode:
kick=36, snare=38, hi_hat=42, toms=47, cymbals=49.

---

## Running a real training run

From `python/ingest` (using the project venv):

```bash
.venv/Scripts/python.exe -c "
from aural_ingest.training.drum_crnn.config import TrainConfig
from aural_ingest.training.drum_crnn.train import train
cfg = TrainConfig(
    corpus_root=r'E:\AudioSourceOfTruthData\extracted\e_gmd',
    output_dir=r'D:\path\OUTSIDE\the\repo\drum_crnn_run',  # NEVER inside the repo
    epochs=30,
    batch_size=8,
    clip_seconds=8.0,
    # train_limit / val_limit=None -> the whole E-GMD split (444 h; days of CPU).
    device='auto',   # 'cuda' when available, else 'cpu'
)
print(train(cfg))
"
```

This streams `(features, targets)` per file (memory-bounded), trains with
`BCEWithLogitsLoss` + Adam, checkpoints every epoch (`checkpoint_epochNN.pt`
and `checkpoint_best.pt`), and writes `history.json` with per-epoch per-class
**frame** F1. A full run is days of compute on CPU — use a CUDA build and cap
with `train_limit` for iteration.

### Export to ONNX

```python
from aural_ingest.training.drum_crnn.model import DrumCRNN
from aural_ingest.training.drum_crnn.export_onnx import export_to_onnx, verify_onnx
import torch

from aural_ingest.training.drum_crnn.config import ModelConfig

ckpt = torch.load("checkpoint_best.pt", map_location="cpu")
model = DrumCRNN(ModelConfig(**ckpt["model_config"]))  # rebuild from saved config
model.load_state_dict(ckpt["model_state"]); model.eval()

export_to_onnx(model, "drum_crnn.onnx", n_mels=ckpt["feature_config"]["n_mels"])
verify_onnx(model, "drum_crnn.onnx", n_mels=ckpt["feature_config"]["n_mels"])
```

ONNX (via the already-shipped `onnxruntime`) is the packaging route for the
PyInstaller one-file sidecar — it avoids dragging the full PyTorch stack into
the frozen binary. Export uses the legacy TorchScript path (`dynamo=False`),
which needs only the `onnx` package (a dev-time dependency), not `onnxscript`.

### Smoke run (plumbing proof)

```bash
.venv/Scripts/python.exe -m aural_ingest.training.drum_crnn.smoke \
    --output-dir <scratch>/drum_crnn_smoke --files 5 --epochs 3 --device cpu
```

Trains on a handful of E-GMD files for a few epochs purely to prove the
pipeline runs: features → targets → train step → val frame-F1 → checkpoint →
decode → ONNX export → onnxruntime forward. **The metrics it prints are
smoke-only and say nothing about model quality.**

---

## Data attribution — E-GMD (CC BY 4.0)

The Expanded Groove MIDI Dataset (E-GMD) is licensed **Creative Commons
Attribution 4.0 International (CC BY 4.0)**, which permits commercial use and
retraining **with attribution**. Any model, dataset artifact, or product that
is trained on or derived from E-GMD **must credit the dataset**. Suggested
attribution:

> This model was trained on the Expanded Groove MIDI Dataset (E-GMD) by
> Callender, Hawthorne, and Engel (Magenta / Google), licensed under CC BY 4.0.
> Dataset: https://magenta.withgoogle.com/datasets/e-gmd

Reference: L. Callender, C. Hawthorne, J. Engel, *"Improving Perceptual
Quality of Drum Transcription with the Expanded Groove MIDI Dataset"*, 2020.

**Do not commit E-GMD audio, MIDI, or any trained weights/checkpoints into this
repository.** Point `output_dir` at a location outside the repo tree; the
harness writes checkpoints and ONNX there.
