# Instrument and stem selection in Studio

Open **Import**, choose an audio file or **A folder of already-separated stems**, then select **Recognition mode → MuScriptor — choose instruments and stems**.

## Choose what to recognize

Leave **Instruments to recognize** on **Automatic**, or choose **Choose instruments…** and check any combination of MuScriptor's 35 supported instrument groups. Keys and Guitars are editable shortcuts. A custom selection requires at least one instrument.

Recognition labels are independent of source stem names. For example, select keys and other stems while asking for acoustic piano, organ, and acoustic guitar. Game tracks use Studio's existing role mapping: guitars become guitar tracks, bass becomes bass, voice becomes vocals, and other pitched instruments use Keys.

MuScriptor must be ready under **Configure → Models**. It supplies both drum and melody notes, so the separate engine selectors are disabled. Explicit selections never silently fall back to another recognizer.

## Choose what audio to analyze

- **One audio file:** choose whether it is a full song/mix or an isolated stem and select its role. Isolated stems are used directly without another separation pass.
- **A folder of separated stems:** check the source files and correct their roles. Select multiple roles to combine their audio. Timelines align at the start, shorter stems are padded at the end, and different sample rates are resampled. PCM and floating-point WAVs are supported. The full mix is never substituted. Each selected file currently needs a distinct role.

Stem selection also applies to default per-instrument analysis. Missing or empty selections stop setup with an explanation.

Give the result a recognizable title and click **Import**. The default output chooser creates a separate draft when that name already exists. Open it in Cleanup to compare. The FeedPak retains the instrument selection, source roles and hashes, and raw output under `aural/recognition/selected_instruments.json`.

## Missing sections

Instrument conditioning cannot restore audio removed from a stem. If a passage is absent in keys, include another stem that contains the passage or use a cleaner source. Some audible notes can still be missed by the model.

The October 20 What A God rerun has a 48.6-second gap between attacks at 3:41.75–4:30.35. Its keys stem measures about −71.5 dBFS RMS at 3:45–4:25. Four previously checked gaps gained notes, but the full transcription still has gaps.

## Command line

```text
aural_ingest import "guitar.wav" --out "guitar.feedpak" --source-stem-role guitar --recognition-instruments acoustic_guitar,clean_electric_guitar
aural_ingest import-dir "stems" --out "selected.feedpak" --config "stems.json" --recognition-stems keys,other --recognition-instruments acoustic_piano,organ,acoustic_guitar
```

The config maps roles to files in `input_stem_paths`. JSON may also set `recognition_instruments`, `recognition_stems`, and `source_stem_role`. Omit instruments for automatic conditioning. Different selections and changes to selected source audio produce distinct song identities.

The earlier `--keyboard-only` CLI flag remains for existing scripts; Studio now uses the general selectors.
