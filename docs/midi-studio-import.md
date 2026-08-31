# Studio import: MIDI scores rendered through Ableton

## What this path is for

Some material is not a recording we are trying to transcribe — it is a MIDI
score, and the audio is something we produce *from* it. The classical set is
the case in point: ten piano-midi.de performances, each rendered through a
Live instrument.

When the audio comes from the MIDI, the MIDI is not an estimate of the
recording. It is what the recording was made from, and every note time in it
is exact by construction. Any step that re-derives those times can only lose
information. So this path never re-derives them.

Use `import` (audio transcription) when a recording is the source of truth.
Use `import-musicxml` when a *score* is, and the audio is incidental. Use this
when the audio was rendered from the MIDI and the two must agree to the sample.

## The problem this path exists to solve

**Ableton does not import MIDI tempo maps.** Load a piano-midi.de file into a
clip and Live plays every note on a rigid grid at the set tempo. On Clair de
Lune, which moves between 8 and 132 BPM, the result is unrecognisable.

The fix is to move the rubato out of the tempo map and into the note
positions: rewrite every event at its true wall-clock position under one fixed
tempo. Live then plays the performance correctly, because the notes are where
they always were.

That flattened file is **render prep and nothing else**:

```
piano-midi.de/x.mid  ──prep-render──>  render_ready/x.mid  ──Ableton──>  renders/x.wav
        │                                                                     │
        └────────────────────── import-midi ──────────────────────────────────┘
                        (chart from the ORIGINAL, audio from the render)
```

**The chart is always imported from the original.** Both files carry the same
note times; only the original also carries the tempo map, and the tempo map is
what puts bar lines on bars. Building the chart from the flattened file
instead is what put a flat 120 BPM into the first classical packs, with
measures falling wherever two seconds of wall clock happened to land.

## Step 1 — prepare the render source

```bash
aural_ingest prep-render "D:/Music/Classical/piano-midi.de/schumann__scn15_7.mid" --out "D:/Music/Classical/render_ready"
```

This writes the flattened MIDI plus a `<stem>.render.json` recording which
file is the chart source and which is the render source, so the pairing is not
left to memory.

It also verifies itself. The gate measures **note onsets**, not
`MidiFile.length` — an earlier version compared file lengths and reported 71
seconds of drift on a Bach file whose notes had not moved at all, because the
original has a long trailing silence. Note span is what has to survive; a tail
of nothing afterwards is not a defect. Anything past 5 ms is a real error, and
`prep-render` exits non-zero rather than handing on a bad render source.

Across the ten-piece classical set every file flattens at 0.52 ms, which is
tick quantisation at 480 PPQ and 120 BPM — the floor, not a tolerance.

## Step 2 — render it in Live (Ableton MCP)

The MCP writes into the user's open Live set, so this leg is interactive by
design: it needs a set with an instrument on the target track, and it takes
wall-clock time.

1. **Check the set.** `live_ping`, then `track_list` and
   `arrangement_clips_list` — confirm the target track has the instrument you
   want and that the arrangement region you are about to use is empty.
   `arrangement_insert_midi_clip` fails on overlap rather than overwriting,
   but knowing what is there beats finding out.
2. **Set the tempo to match the flatten.** `live_set_tempo(120)`. This has to
   equal `FIXED_BPM` in `midi_render_prep.py`, or every note lands at the
   wrong beat and the render is uniformly stretched — a failure that sounds
   plausible, which is the dangerous kind.
3. **Place the notes at beat 0.** `arrangement_insert_midi_clip(track, 0,
   length_beats)` then `clip_add_notes`. Starting anywhere but beat 0 puts an
   offset into the render that has to be trimmed back out by exactly the same
   amount later.
4. **Bounce the arrangement.** `bounce_region(output_dir, start_beats=0,
   end_beats=…, background=True)`, then poll `bounce_job_status(job_id)`.
   End the region **past the last note** — a piano release plus reverb runs
   ten to fifteen seconds beyond the final onset in this set, and cutting it
   at the last note chops the ending off.

   Bouncing is **real time**: a five-minute piece costs five minutes.
   `background=True` is what makes a bulk run tolerable — start the bounce,
   poll every ten seconds or so, and on `done` move to the next piece.
   `bounce_tracks(mode="freeze")` is the fast alternative in principle, but
   `Track.freeze` is not exposed in this Live version and the call fails.

Live's Complex Pro engine is why this leg is worth its wall clock; nothing
offline matches it.

## Step 3 — import the pack from the original

```bash
aural_ingest import-midi "D:/Music/Classical/piano-midi.de/schumann__scn15_7.mid" \
  --audio "D:/Music/Classical/renders/schumann__scn15_7.wav" \
  --out "D:/AuralPrimer/AuralPrimerPortable/data/songs" \
  --title "Kinderszenen No. 7" --artist "Robert Schumann" --genre Classical
```

Note the input: the **original**, with the render attached. `import-midi`
reads the notes and the tempo map straight off it and derives the beat grid
from that map rather than inventing one, so the bar lines bend where the
performance bends.

Before writing anything it checks that the render actually covers the chart.
A studio bounce is done by hand and the ways it goes wrong are mundane — a
loop brace left on an eight-bar region, a bounce that ended at the last clip
edge, the wrong track soloed. The result is audio shorter than the notes it is
meant to carry, and a pack that stalls in Wait mode on a note the audio never
plays. Reading the wav header costs nothing and turns that into an import-time
error. The tolerance is 0.75 s, so a final note decaying past the bounce edge
passes and a truncated render does not.

## Bulk runs

The shape for a larger set, given that step 2 is the only slow part:

1. `prep-render` every source up front and check `ok` on each — it is cheap
   and a failure there means the render would have been wrong.
2. For each piece: place notes → `bounce_region(background=True)` → poll →
   verify `result` → next. One Live set, one instrument, reused throughout.
3. `import-midi` every original against its render. The coverage check is the
   backstop for the leg that had a human in it.

Total wall clock is dominated by step 2 and is roughly the total playing time
of the set.

## Verified state

All ten classical renders pass the coverage check, with 9.9–15.3 s of tail
past the last onset:

| piece | notes | last onset | render | slack |
|---|---:|---:|---:|---:|
| bach__bach_846 | 1284 | 221.03 s | 234.98 s | +13.95 s |
| beethoven__elise | 1041 | 164.98 s | 175.07 s | +10.09 s |
| chopin__chpn-p15 | 1518 | 265.96 s | 279.66 s | +13.70 s |
| debussy__deb_clai | 1491 | 244.04 s | 257.18 s | +13.14 s |
| grieg__grieg_wedding | 3842 | 328.88 s | 338.81 s | +9.93 s |
| liszt__liz_liebestraum | 1888 | 240.98 s | 254.82 s | +13.84 s |
| mozart__mz_331_3 | 2819 | 189.08 s | 199.07 s | +9.99 s |
| schubert__schuim-3 | 2601 | 336.78 s | 352.04 s | +15.25 s |
| schumann__scn15_7 | 456 | 123.19 s | 137.78 s | +14.59 s |
| tchaikovsky__ty_juni | 1502 | 228.54 s | 241.11 s | +12.57 s |
