# Research — MuScriptor whole-mix instrument attribution (2026-09-08)

Investigation prompted by `psalm_121_my_help_muscriptor.feedpak`, reported as
having "large areas of dead zone... piano cuts out for five, ten seconds" and
"vocal parts being transcribed for the piano, making the piano part too busy".

Both reports are correct. They are two different failures of the same thing:
MuScriptor decides which instrument it is hearing, and on a sparse mix it
decides badly and inconsistently. Nothing downstream questions the answer.

Source recording: piano and voice only. 128.92 s. Imported 2026-08-01 with
`muscriptor_wholemix`, unconditioned.

## Finding 1 — the dead zones are the piano relabelled as guitar

MuScriptor decodes in **5.0 s chunks** (`128.9s → 26 chunk(s) of 5.0s`). For
two chunks it labelled the piano `acoustic_guitar` / `clean_electric_guitar`.
`_DEFAULT_ROLE_MAP` sends those to `rhythm_guitar`, so the Keys part went
silent for exactly those chunks.

| evidence | value |
|---|---|
| Keys gaps | 40.00→45.33 s, 50.00→55.35 s |
| gap boundaries | exact chunk boundaries; notes truncated at t=40.000, 50.000 |
| "Rhythm Guitar" track | exactly 50 notes, spanning 40.16→55.00 s only |
| manifest group counts | `acoustic_guitar: 23` + `clean_electric_guitar: 27` = 50 |
| notes below guitar's low E (MIDI 40) | 4 (all pitch 39) |
| keys stem level during gap 1 | −24.8 dBFS median, vocals −76.3 (silent) |

The piano is plainly audible in both windows, and gap 1 has no vocal at all —
so this is not the voice masking the piano. Folding the 50 notes back into
Keys takes the worst gap from 5.35 s to 1.49 s.

**The assignment is unstable run-to-run.** Re-transcribing the same audio
today (mix reconstructed by summing the six demucs stems) produced *no guitar
group at all*: `{acoustic_piano: 634, voice: 53, flutes: 23}`, worst Keys gap
0.22 s. Same model, same settings, materially different attribution. The
August import lost a coin flip.

## Finding 2 — the busy piano is the sung melody decoded as piano

Measured as: Keys notes matching a vocal note in pitch, onset within 40–50 ms.

| condition | vocal melody landing in the piano |
|---|---|
| the pack as imported | 23 notes = 55% of its (sparse, 42-note) vocal track |
| whole-mix, conditioned `[acoustic_piano, voice]` | **69%** |
| whole-mix, conditioned `[acoustic_piano]` only | **73%** |
| **isolated `keys.wav` stem — the honest baseline** | **14%** |

The pianist genuinely doubles the melody about one note in seven. The
whole-mix decode puts in five times that.

Density confirms the "too busy" report:

| | notes | notes/sec | 6+ notes sounding |
|---|---|---|---|
| whole-mix (piano+voice) | 695 | 5.4 | 39% of the time |
| isolated keys stem | 468 | 3.6 | 12% |

Contributing mechanism: MuScriptor emits a `flutes` group (18 events in the
import, 23 on re-run) that is not in `_DEFAULT_ROLE_MAP`, so
`_CATCHALL_ROLE = "keys"` sweeps it into the piano. Masking `flutes` out does
**not** solve the doubling, though — see Finding 3.

## Finding 3 — conditioning fixes the dead zones, not the doubling

`instruments=` is a hard decode mask over what the model *may* emit, not an
instruction about which sound goes where. Allowing `voice` does not stop the
model labelling singing as piano.

| run | keys notes | worst Keys gap | phantom guitar | melody in piano |
|---|---|---|---|---|
| the pack (2026-08-01) | 595 | **5.35 s** | 50 notes | 55% |
| unconditioned, today | 657 | 0.22 s | none | — |
| auto-conditioning (current code) | 535 | 1.48 s | **50 notes** | — |
| declared `[acoustic_piano, voice]` | 695 | **0.03 s** | none | 69% |
| declared `[acoustic_piano]` | 659 | 0.21 s | none | 73% |

## Finding 4 — auto-conditioning is fooled by separation bleed

`instruments_from_stems` gates on the 95th-percentile of per-second RMS
against `_STEM_PRESENT_DBFS = -50.0`. On this song:

| stem | p95 RMS | passes? | correct? |
|---|---|---|---|
| keys | −17.6 | yes | yes |
| vocals | −18.0 | yes | yes |
| **guitar** | **−25.7** | **yes** | **no — there is no guitar** |
| rhythm_guitar | −30.6 | yes | no |
| lead_guitar | −32.9 | yes | no |
| bass | −65.0 | no | correct |
| drums | −73.8 | no | correct |

The gate works for stems the separator genuinely emptied (bass, drums) and
fails for stems it filled with bleed. The docstring's claim — "a stem carrying
a real part sits at −28 to −38 dBFS, one the separator emptied sits at −72 to
−78, and nothing lives in between" — is falsified here: piano bleed in the
guitar stem sits at −25.7, above the range claimed for *real* parts.

Consequence: re-importing this song with today's code still emits the phantom
50-note guitar part (run C above).

## Finding 5 — per-stem transcription is not the answer either

Transcribing the isolated `keys.wav` gives the clean 14% doubling figure and
playable density, but has a **10.55 s dead zone at 13.1 s** — because demucs
*emptied* the keys stem there (−76.8 dBFS median = digital silence) while the
piano is clearly audible in the mix. The whole-mix run finds 30 Keys notes in
that window.

So: whole-mix sees everything but mislabels it; the stem labels correctly by
construction but inherits the separator's dropouts.

## Finding 6 — the keys stem separates the two cleanly, as a *gate*

For each whole-mix Keys note, energy at that note's own f0 (plus 2nd harmonic)
in `keys.wav` vs `vocals.wav`:

| group | n | median keys-vs-vocals | voice-dominated (<0 dB) |
|---|---|---|---|
| Keys notes on the vocal line | 101 | **−32.0 dB** | 85% |
| Keys notes not on it (control) | 200 | **+64.5 dB** | 12% |

~96 dB of median separation. A gate at 0 dB would drop ~85% of the doubled
notes while keeping 88% of genuine piano notes — without inheriting the
separator dropouts of Finding 5, because the decode still comes from the mix.

## Suggested direction (not implemented)

1. Let the import declare the instrument set, defaulting to the stem-derived
   guess but user-correctable — Finding 4 shows the guess cannot be trusted
   alone. Fixes the dead zones (5.35 s → 0.03 s).
2. Add a stem-support gate on whole-mix melodic notes (Finding 6). Fixes the
   doubling, which no amount of conditioning does.

Note that `AURAL_MUSCRIPTOR_INSTRUMENTS` cannot express (1) today:
`cli.py` always passes `instruments=instruments_from_stems(...)`, and
`transcribe_mix` does `instruments or _parse_instruments(env)`, so a non-empty
stem-derived list always wins and the env var is dead on the import path.

## Reproduction

Scripts used are one-off; the measurements above come from
`aural/notes.mid` in the pack plus `M.transcribe_mix()` called directly with
explicit `instruments=` lists, against a mix reconstructed by summing the six
demucs stems (bass, drums, guitar, keys, other, vocals — *not* the derived
`*_guitar` stems, which would double-count).

Caveat: the reconstructed mix is not bit-identical to the original import's
`audio/mix.wav`, which was not retained. The unconditioned re-run is therefore
an imperfect control; the qualitative result (no guitar group at all) is well
outside what reconstruction error would explain, but it is not a clean A/B.
