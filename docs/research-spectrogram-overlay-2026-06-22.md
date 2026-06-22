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
3. **Artifact — pre-rendered PNG (rows = semitone bins, cols = time frames) +
   geometry JSON** `{fmin_midi, bins_per_semitone, frames_per_sec, time_range,
   midi_range}` stored per-stem in the `.auralsong` pack. PNG is compact, tiles
   for long songs, and `drawImage`s fast.
4. **Overlay** — draw the spectrogram on an offscreen canvas as a static layer
   *beneath* the editable note layer; row for MIDI p = `(p − fmin_midi) ×
   bins_per_semitone`; draw notes with a contrasting translucent outline
   (cyan/green) over the dark→hot colormap.

## Prior art / UX

Tony (the SV authors' note editor) overlays pitch + note + spectrogram +
waveform and **separates pitch editing from timing editing** — adopt that in the
Refine Workspace. Tony is monophonic-only, so polyphonic piano is new work
(CQT being polyphony-friendly is why it's the right backdrop).

## Licensing

Reimplement via **librosa (ISC)** / JS — do **not** copy code from SV / Vamp /
QM-DSP (all GPL). The algorithm itself isn't copyrightable.
