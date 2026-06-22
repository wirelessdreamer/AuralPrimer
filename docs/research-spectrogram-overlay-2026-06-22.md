# Research — Sonic Visualiser-style spectrogram overlay for guided edit (2026-06-22)

Deep, adversarially-verified research (25/25 claims confirmed). Goal: render a
pitch-aligned melodic-range spectrogram as a backdrop in the Studio Refine
Workspace ("guided edit"), overlay our transcription notes on it, and let a
human verify/correct against the actual harmonic energy.

## What Sonic Visualiser's "Melodic Range Spectrogram" is

A **preset STFT** (not constant-Q): ~40 Hz–1.5 kHz, log-frequency / linear-pitch
axis, linear colour scale, 8192-sample window ~94% overlap. SV is **GPL** — we
reuse the *technique*, not its code. (SV's "Peak Frequency" spectrogram is the
separate reassignment-based one.)

## Recommendation

1. **Representation — Constant-Q Transform (CQT) via librosa, not SV's log-STFT.**
   With `bins_per_octave=12, fmin=C1`, librosa CQT **bin k = MIDI 24+k** — an
   exact 1:1 row-to-semitone mapping onto our MIDI-pitch y-axis, so the note
   overlay registers with zero remap. CQT is best for polyphony; the reassigned
   spectrogram is unsuitable for dense polyphony; `librosa.salience` is an option
   to emphasise fundamentals. Consider 36 bpo for finer vertical detail.
   (Validate `bin k = MIDI 24+k` empirically — specshow labelling quirk, librosa
   issue #586.)
2. **Compute in the Python sidecar at import** (we already have librosa). A
   desktop editing surface doesn't need realtime, and in-browser WebAudio FFT
   can't produce a clean CQT.
3. **Artifact — quantized CQT magnitude MATRIX, NOT a pre-coloured PNG**
   (see "Interactive rendering" below — the static-PNG idea is superseded).
   Rows = 12×octaves (`bin k = MIDI 24+k`), cols = time frames, dB-domain,
   8-bit (16-bit if dB-floor sweeps band), tiled + mip pyramid, plus geometry
   JSON `{fmin_midi, bins_per_semitone, frames_per_sec, time_range, midi_range}`.
4. **Overlay** — render the spectrogram in WebGL beneath the editable note layer;
   row for MIDI p = `(p − fmin_midi) × bins_per_semitone`; notes as a contrasting
   translucent outline over the heat colormap.

## Interactive rendering (2026-06-22 follow-up — SUPERSEDES the static-PNG plan)

The user wants Sonic Visualiser's LIVE interactivity (zoom/pan/gain/contrast/
threshold/colormap), not a baked image. Verified architecture (24/25 confirmed):

- **SV separates a cached magnitude model (FFTModel) from the renderer
  (SpectrogramLayer).** Gain, threshold, colour-scale, colormap, rotation, and
  normalization are render-time display properties applied to cached bins with
  NO recompute. Only window size / overlap / oversampling / window shape /
  transform type force a recompute.
- **Reproduce it on the web: precompute the magnitude matrix in the sidecar →
  upload as a single-channel texture → recolor/scale/zoom live in a WebGL
  fragment shader.** Gain = per-pixel multiply; colormap = a 1D LUT texture
  indexed by magnitude (swapping colormap = swap LUT). Pattern: `calebj0seph/
  spectro`. NOT in-browser FFT, NOT a coloured PNG.
- **Tiling + mip pyramid mandatory:** cross-platform-safe `MAX_TEXTURE_SIZE` is
  4096px ≈ 48s at ~86 cols/s — multi-minute songs tile across textures with a
  multiscale pyramid for smooth zoom-out.
- **Pure-render (client, instant):** gain, contrast, threshold, dB-floor,
  colormap, zoom, pan, normalization. **Needs sidecar recompute:** window/overlap/
  window-shape/transform, CQT bpo/fmin.
- **Licensing:** SV (GPL-2.0+) and Friture (GPL-3) → technique only. **wavesurfer.js
  (BSD-3)** is the only permissive prior art — takes a precomputed matrix
  (`frequenciesDataUrl`) with gainDB/rangeDB/colorMap, but Canvas-2D only (no
  shader recolor) → reference/fallback or player/timeline substrate, not the full
  solution.
- Caveat: `calebj0seph/spectro` is a realtime mic demo (pattern transfers by
  analogy); 8-bit over 80 dB ≈ 0.3 dB steps (validate banding on steep dB-floor).

## Prior art / UX

Tony (the SV authors' note editor) overlays pitch + note + spectrogram +
waveform and **separates pitch editing from timing editing** — adopt that in the
Refine Workspace. Tony is monophonic-only, so polyphonic piano is new work
(CQT being polyphony-friendly is why it's the right backdrop).

## Licensing

Reimplement via **librosa (ISC)** / JS — do **not** copy code from SV / Vamp /
QM-DSP (all GPL). The algorithm itself isn't copyrightable.
