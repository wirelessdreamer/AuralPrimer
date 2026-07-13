# GuitarSet GT extensions

Date: 2026-07-07

Purpose: unlock the extra GuitarSet annotations called out in T3.7 for later
tab, chord, and key evaluation. The existing note-onset/pitch benchmark still
scores the same flattened `MelodicNote` references; these additions expose
extra structured ground truth on each `GroundTruthCase`.

## Added fields

`GroundTruthCase.melodic_note_metadata`

- Aligned 1:1 with `GroundTruthCase.melodic_notes`.
- GuitarSet entries include `string`, `fret`, and `open_midi`.
- String numbering follows GuitarSet: `0` is low E and `5` is high E.

`GroundTruthCase.chord_events`

- Parsed from both GuitarSet `chord` annotations.
- The first track is labeled `mireval`; the second is labeled `pretty_midi`.
- Events keep the original chord label plus onset, offset, and optional confidence.

`GroundTruthCase.key_events`

- Parsed from GuitarSet `key_mode`.
- Events expose `key`, `mode`, original `label`, onset, offset, and optional confidence.

## Real-data smoke

Command:

```powershell
$env:PYTHONPATH='D:\AuralPrimer\python\ingest\src'
@'
from pathlib import Path
from aural_ingest.dataset_adapters.guitarset import yield_cases
case = next(yield_cases(Path(r'E:\AudioSourceOfTruthData\extracted\guitarset'), variant='mic', limit=1))
print(case.case_id)
print('notes', len(case.melodic_notes), 'note_meta', len(case.melodic_note_metadata))
print('chords', len(case.chord_events))
print('keys', [(e.key, e.mode, e.label) for e in case.key_events])
'@ | D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -
```

Observed output:

```text
guitarset:00_BN1-129-Eb_comp:mic
notes 133 note_meta 133
chords 12
keys [('Eb', 'major', 'Eb:major')]
```

## Corpus fingering validation

Command:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\guitar\validate_guitarset_fingering.py `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant all `
  --output benchmarks\guitar\gt_runs\guitarset_fingering_validation_all.json
```

Observed output:

```text
{"ok": true, "summary": {"ok": true, "variant_count": 4, "case_count": 1440, "note_count": 249904, "metadata_count": 249904, "invalid_note_count": 0}}
```

The full report records the per-variant string counts, style counts, and fret
range. Each variant had 360 cases, 62,476 notes, fret range 0-19, and no
invalid string/fret/open-string entries.

This gives T3.7/T3.8 a real GuitarSet source for tab, chord, and key
evaluation without changing the existing melodic benchmark scores.
