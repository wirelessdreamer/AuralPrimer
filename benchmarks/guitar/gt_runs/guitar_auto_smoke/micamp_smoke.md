# guitar_auto smoke run — guitar_techs micamp

Partial results (5 guitar_auto cases; 4 melodic_combined_guitar comparisons
before I stopped the run — the machine was overloaded with other Python jobs
and each guitar_auto case was taking 5+ min instead of the expected ~1 min).

Environment: torchcrepe NOT installed in this venv, so guitar_auto's lead
channel falls back to melodic_combined; only the rhythm channel gets the
basic_pitch upgrade. This is the "Phase 2 alone" scenario.

## Per case (F1 / P / R)

| Case ID     | guitar_auto            | melodic_combined_guitar |
|-------------|------------------------|-------------------------|
| Drop3_7     | 0.411 / 0.295 / 0.679  | 0.206 / 0.173 / 0.257   |
| Drop3_m7    | 0.373 / 0.261 / 0.652  | 0.209 / 0.170 / 0.269   |
| Drop3_m7b5  | 0.467 / 0.314 / 0.914  | 0.235 / 0.176 / 0.350   |
| Drop3_Maj7  | 0.366 / 0.240 / 0.767  | 0.153 / 0.114 / 0.233   |
| Set1_7      | 0.370 / 0.242 / 0.782  | (not run)               |

## Micro aggregate

| Algorithm               | F1     | P      | R      | tp  | fp   | fn  |
|-------------------------|--------|--------|--------|-----|------|-----|
| guitar_auto             | 0.396  | 0.269  | 0.748  | 855 | 2326 | 288 |
| melodic_combined_guitar | 0.201  | 0.158  | 0.275  | 256 | 1366 | 676 |

## Read

- **F1 nearly doubles** on P1_chords / micamp: 0.396 vs 0.201.
- The lift is driven almost entirely by **recall** (0.748 vs 0.275) — basic_pitch
  on the rhythm channel is catching chord tones melodic_combined can't emit
  because it's monophonic.
- Precision improves slightly too (0.269 vs 0.158). The rhythm channel's basic_pitch
  is adding a lot of false positives (2326 FPs across 5 cases) — this is exactly
  the shape Phase 3 (`guitar_cleanup`) and Phase 4 (`guitar_chord_supplement`) are
  designed to prune. Without them, we get "high recall, mediocre precision, decent
  F1".
- Sample is 100% chord cases (P1_chords first page). The lift would look different
  on singlenotes/scales, where torchcrepe (unavailable in this venv) would carry
  more weight.

## Not a tuning benchmark

Per orchestrator brief: this is a smoke sanity check, not a tuning input. No
knobs were adjusted based on these numbers.
