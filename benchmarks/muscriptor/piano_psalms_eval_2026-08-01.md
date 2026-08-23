# MuScriptor whole-mix vs authored MIDI — Piano Psalms eval

**Date:** 2026-08-01
**Engine:** MuScriptor `large` (whole-mix), frozen sidecar `0b1fa57f…`
**Songs:** 9 Piano Psalms (`D:\Psalms\Piano Psalms`)
**Ground truth:** the authored Suno per-part stem MIDI shipped alongside each mix
**Metric:** mir_eval note F1, onset + pitch, 50 ms tolerance, offsets/velocity ignored
(the standard automatic-music-transcription onset metric). MuScriptor's
`rhythm_guitar` / `lead_guitar` catch-all is folded into **keys** so a labelling
quirk isn't scored as a miss.

## Result — weighted by authored note count

| Instrument | Songs | Covered | Authored notes | **wF1** | wRecall |
|---|---|---|---|---|---|
| **keys** (piano) | 9 | 9/9 | 9,016 | **0.242** | 0.273 |
| bass | 5 | 5/5 | 149 | 0.140 | 0.195 |
| drums | 5 | 3/5 | 323 | 0.045 | 0.090 |
| vocals | 7 | 6/7 | 1,974 | 0.075 | 0.053 |

## Per-song keys (the dominant instrument)

| Song | Authored keys notes | keys F1 |
|---|---|---|
| psalm_121_my_help | 479 | **0.628** |
| psalm_130…_instrumental | 882 | 0.543 |
| psalm_5_every_morning | 1606 | 0.419 |
| psalm_130_please_hear_me | 753 | 0.210 |
| psalm_5…_instrumental | 1055 | 0.168 |
| psalm_6_how_long | 1125 | 0.144 |
| psalm_10_why | 1287 | 0.102 |
| psalm_6…_instrumental | 826 | 0.074 |
| psalm_10_why_instrumental | 1003 | 0.037 |

## Reading it

- **MuScriptor does detect the piano** (contrary to the NEVER ENOUGH result,
  where its distorted-guitar mix produced no piano group). It emits a comparable
  volume of notes overall — 12,913 vs 11,462 authored across scored instruments.
- **But the timing is loose and it over-produces.** On psalm_10_why: 1,822
  emitted keys notes vs 1,287 authored, aligned in range and pitch, but with a
  **91 ms median onset error** — only 35 % within 50 ms, 55 % within 100 ms.
  That collapses F1 at rhythm-game tolerance even though the transcription is
  "roughly right".
- **Density predicts quality.** The one strong result (psalm_121, F1 0.63) is
  the sparsest song (479 notes). Dense arrangements (1,000–1,600 notes) fall to
  0.04–0.17.
- **Non-piano parts are near-useless here**: vocals wF1 0.075, drums 0.045,
  bass 0.140 — though bass/drums are very sparse in these arrangements
  (6–59 notes), so those numbers rest on little data.

## Bottom line

For piano-vocal material like this, MuScriptor whole-mix is **not** a
replacement for the authored MIDI: ~24 % keys F1 weighted, near-zero elsewhere,
with onset timing too loose for gameplay. It may still be useful as a *starting
draft* for songs that have **no** authored MIDI and no clean stems — but on this
catalog, the existing Suno-MIDI packs are strictly better.

*Caveat: n=9, one genre (piano worship), one ground-truth source (Suno stem
MIDI, whose own alignment to the mix is assumed). Not a general verdict on
MuScriptor — a verdict on MuScriptor for this kind of song.*
