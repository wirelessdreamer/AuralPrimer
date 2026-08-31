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
                 chart from the ORIGINAL, audio from the render,
                 lead-in measured and trimmed where they disagree
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

The MCP writes into the user's open Live set, so this leg is interactive and
takes wall-clock time. What follows is what the tools actually do on Live
11.3, not what their descriptions promise — three of the obvious calls do not
work, and finding that out by reading was not possible.

**What does not work**

- `arrangement_insert_midi_clip` → `Track.create_midi_clip not available in
  this Live version`. There is no way to put a MIDI clip on the arrangement
  timeline through the MCP, so arrangement-based bouncing is out.
- `render_clip` and `render_master` → `{"status": "not_implemented",
  "phase": 2}`. Both are stubs.
- `bounce_tracks(mode="freeze")` → `Track.freeze not available in this Live
  version`. The fast path does not exist; every render is real time.

**What does work**

1. **Check the set.** `live_ping`, `track_list`, `device_list` — confirm the
   target track has an instrument. `clip_list` and `arrangement_clips_list`
   confirm you are not about to overwrite the user's work.
2. **Match the tempo to the flatten.** `live_set_tempo(120)`, equal to
   `FIXED_BPM` in `midi_render_prep.py`. If they disagree, every note lands on
   the wrong beat and the render is uniformly stretched — a failure that
   sounds plausible, which is the dangerous kind.
3. **Load the MIDI into a Session clip.** `midi_file_load_into_clip(track,
   clip, path)` takes the file directly and reports the notes it added.
4. **Extend the loop past the last note** with `clip_set_loop`, or the clip
   restarts on top of its own decay. A piano release plus reverb runs ten to
   fifteen seconds past the final onset in this set.
5. **Bounce.** `bounce_song(duration_sec, output_path, background=True)`,
   polling `bounce_job_status(job_id)`. It builds a temporary resampling
   track, records, and removes it again without touching existing tracks.
6. **Fire the clip only once that temp track exists.** `background=True`
   returns a `job_id` immediately, several seconds before the bounce is ready
   to record, and firing into that window kills it:

   ```
   BounceError: create_audio_track timed out after 2.0s
   (num_tracks stayed at 4). Live may be busy or unresponsive.
   ```

   A clip launching is enough to make Live miss the 2-second window. Worse,
   the failure happens *after* the track is created, so cleanup never runs and
   an empty audio track is left in the set for someone to delete by hand.

   Poll `bounce_job_status` until the job is actually recording, then fire.
   The wait shows up as extra lead-in, which is measured out at import, so it
   costs nothing to be generous with it -- and set `duration_sec` to the clip
   length plus that wait plus the tail, or the ending gets cut.

**The catch, and why step 3 has to correct for it**

`bounce_song` records the transport while a *Session* clip supplies the audio,
and those two are started by separate round-trips. Whatever time passes
between them is captured as silence at the head of the render.

That is not a hypothesis. Every render in the classical set carries a lead-in
of between 5.04 s and 5.50 s, and the half-second of spread is what proves it
is latency rather than a count-in — a count-in would be constant. The audio
looks fine, because the gap is silent. What breaks is downstream: the chart
says play at 0.9 s and the recording plays at 6.0 s.

So do not try to eliminate it at render time. Measure it at import time, where
both sides are available.

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

It then measures how far the render sits *behind* the score and trims that
much off the head. The measurement correlates an onset envelope against the
score's own note times, so it needs no knowledge of how the render was made —
it works on a bounce somebody produced by hand months ago just as well as on
one made through this path. On the classical set the correlation peak runs 14×
to 70× the median, and the result lands within 18 ms.

Two limits worth knowing. The estimator is biased late by a fixed 23.6 ms,
because a spectral flux registers only once energy has risen; that is measured
on synthetic renders with known lead-ins and corrected as a constant. And
perfectly periodic material cannot be aligned at all — if every note is evenly
spaced, a shift of exactly one note explains the audio just as well as no
shift, and the information simply is not there. Real scores are irregular
enough; a metronomic sequence would need its offset recorded at render time.

`--no-align` attaches the render untouched.

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

All ten renders were re-imported through this path. The lead-in each one
carried, and the sync error remaining afterwards, measured against where the
first audible sample sits relative to the first scored note:

| piece | notes | tempo segments | lead-in removed | residual |
|---|---:|---:|---:|---:|
| bach__bach_846 | 1284 | 358 | 5.015 s | −2.3 ms |
| beethoven__elise | 1041 | 923 | 5.117 s | −15.0 ms |
| chopin__chpn-p15 | 1518 | 997 | 5.168 s | 0.0 ms |
| debussy__deb_clai | 1491 | 733 | 5.144 s | −13.8 ms |
| grieg__grieg_wedding | 3842 | 918 | 5.141 s | 0.0 ms |
| liszt__liz_liebestraum | 1888 | 970 | 5.480 s | −14.2 ms |
| mozart__mz_331_3 | 2819 | 947 | 5.156 s | −18.3 ms |
| schubert__schuim-3 | 2601 | 2708 | 5.167 s | 0.0 ms |
| schumann__scn15_7 | 456 | 144 | 5.108 s | −12.1 ms |
| tchaikovsky__ty_juni | 1502 | 671 | 5.148 s | 0.0 ms |

Worst residual 18.3 ms, against roughly 5170 ms before. Much of what remains
is the verification method's own threshold lag rather than real error.
