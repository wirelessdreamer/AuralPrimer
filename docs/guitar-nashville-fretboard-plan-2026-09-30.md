# Plan — Nashville fretboard, tracked in mixed reality (2026-09-30)

A note highway that lands on the frets of a real guitar, labelled by scale
degree rather than fret number, organised into the five pentatonic positions.
Tracked by a controller mounted on the instrument; played through a Jamstik
over Bluetooth MIDI.

The concept is the deliverable here, not the hardware. The fastest way to find
out whether a degree-labelled fretboard actually reads while playing is to draw
it on a monitor, so that is phase 1 and the headset comes after.

## The musical model

Two layers that are easy to conflate and must not be.

**Zones come from geometry.** The five pentatonic boxes are a fact about the
neck: where the hand sits, which frets it spans. A zone is "frets 5–8, all six
strings". Nothing about a zone depends on the key.

**Numbers come from the key.** The Nashville degree on any given dot is a fact
about the music. The same physical box is A-minor-pentatonic position 1 and C
major pentatonic material at the same time; what changes is whether the root
dot reads `1` or `6`. This is why the same five shapes serve both tonalities —
the shapes are geometry, the numbers are not.

### Three tiers inside a zone

Position 1 in A minor, frets 5–8, is the worked example:

| tier | degrees | where |
|---|---|---|
| **the box** | `1 b3 4 5 b7` | two per string: E `1 b3`, A `4 5`, D `b7 1`, G `b3 4`, B `5 b7`, e `1 b3` |
| **completing the mode** | `2` `b6` | inside the same frets, off the box: `2` on low E fret 7, `b6` on A fret 8 and B fret 6 |
| **chromatic** | `#4`, `b2`, … | only drawn when the song actually plays one |

That tiering is what makes it a *visual pattern* rather than a wall of numbers.
The box reads as a shape, the mode notes as additions to it, accidentals as
exceptions. A view that drew all twelve semitones equally would be unreadable,
and a view that drew only the pentatonic five could not show the song.

### Position numbering

Not standardised in the wild — the same shape is called minor position 1 by one
teacher and major position 5 by another. **This plan uses the blues/rock
convention: minor pentatonic position 1 is the E-shape box with its root on the
low E string.** One convention, stated once, used everywhere. It is a constant
to change, not a thing to infer.

### What the degree engine already does

`pitchToNashville` and `diatonicDegree` in `viz-tab` are correct and tested,
including the minor-key spelling this needs (`1 2 b3 4 5 b6 b7`, tonic-relative
rather than relative-major). Two gaps for this feature:

- `inferKeySignature` is only called in piano-roll mode; on a guitar track the
  key analysis is permanently `null`, so no degree label can be produced today.
  The fix is not to infer — `harmony.json` already carries the pack's key *and*
  a roman numeral per chord event, which `viz-nashville` already prefers over
  inference. Read it.
- There is no pentatonic or position concept anywhere in the repo. Confirmed by
  search: the single hit is a dead `caged` enum in `arrangement.schema.json`
  with no producer and no consumer.

## The blocker: the fingerings we ship cannot show position

This is the finding that decides whether the feature is worth building yet.

Packs carry per-note `string` and `fret` in `aural/fingering.*.json`, which
sounds like exactly what a fretboard view needs. Measured on
`fire_in_my_bones`:

| | notes | fret histogram | max fret |
|---|---|---|---|
| rhythm guitar | 1453 | 42% open strings; 0–5 holds almost everything | 16 |
| lead guitar | 764 | essentially nothing above fret 5 | **5** |

**Real lead guitar does not live in frets 0–5.** A position-based view built on
this data would show a hand that never leaves position 1 — which is precisely
the thing the feature exists to teach movement between.

The cause is in the cost model at `cli.py:999-1014`:

```python
cost = float(fret) * 0.25          # pull toward the nut
if fret == 0: cost -= 0.4          # open-string bonus
cost += abs(fret - prev_fret) * 0.15   # continuity
```

A flat 0.25-per-fret pull toward the nut against a 0.15-per-fret continuity
term. The bias wins, every time, so every note slides down the neck. There is
no fret-span limit, no hand window, no reach constraint anywhere — the only
span term is `(max_fret - min_fret) * 0.1` on chords, which makes a twelve-fret
stretch cost less than moving five frets.

**This is the same defect as the piano hand split, in a different instrument.**
There, a fixed middle-C pivot dominated a stateless per-chord rule and every
single note fell on the wrong side of it; the cure was a boundary with memory,
damped over time. Here a fixed nut bias dominates a first-order continuity term.
The shape of the bug and the shape of the fix are the same: the hand has a
position, that position persists, and a per-note cost that does not model it
will collapse.

Unlike the hand split, this one can be measured properly: `guitarset.py` already
adapts GuitarSet, which carries true per-string fingerings from real players.
There is a schema validator for it but **no accuracy benchmark comparing our
assigner against the ground truth** — that benchmark is the first thing to build,
because it turns "the fingerings look wrong" into a number.

### The fix already exists, unmerged

`bass_tab_optimizer.py` — commit `0422136`, 2026-05-20, branch
`claude/vigorous-beaver-ed24e1`, never merged. A Viterbi optimiser over the
whole note chain, and it already carries both of the things the shipped
assigner lacks:

```python
position_preference_fret: float = 3.0   # an anchor, not an unbounded pull
position_weight: float = 0.4
finger_span_threshold: int = 3
finger_span_penalty_per_fret: float = 1.2
```

```python
span = abs(cur_f - prev_f)
if span > weights.finger_span_threshold and (cur_s == prev_s):
    cost += (span - weights.finger_span_threshold) * weights.finger_span_penalty_per_fret
```

A hand *anchor* rather than a nut bias is exactly the difference between a hand
that has a position and a hand that slides to fret 0 — and the docstring names
the cure for our defect outright: *"For lead-style transcription that prefers
staying at the 5th-fret box, raise position_preference_fret and weaken
open_string_bonus."* It also solves the tuning duplication, carrying
`TUNING_EADG_4`, `TUNING_BEADG_5`, `TUNING_BEADGC_6`, `TUNING_DROP_D_4` and a
`tuning_for_name()` lookup.

Written for four strings, but the cost structure generalises to six by swapping
the tuning tuple. **Port it rather than re-deriving it**, and treat the GuitarSet
benchmark as the thing that proves the port — the weights are tuned for bass and
almost certainly want different values for lead guitar, which is a measurement,
not a guess.

This was missed on the first pass for a reason worth recording: ripgrep honours
`.gitignore`, `.claude/worktrees/` is ignored, and so an unmerged branch's
working copy is invisible to every working-tree search. Branch-complete coverage
needs `git log --all` or `git grep` over `git rev-list --all`.

## Tracking

**`XR_META_detached_controllers`** does exactly what is wanted: tracks a
controller that is not held, via `/user/detached_controller_meta/left|right`.
It is already present in the project's OpenXR settings as
`DetachedOculusTouchControllerProfile Android` with `m_enabled: 0` — enabling it
is a flag, not an integration.

It must be paired with `XR_META_simultaneous_hands_and_controllers`: the
player's hands are on the guitar while the controller is strapped to it, so the
runtime has to deliver hand tracking and a detached controller at once. That
combination is Meta's documented use case, not a workaround.

### Calibration is three pinches, not twenty-four

Fret positions are not measured — they are derived. Fret *n* sits at

```
d(n) = L · (1 − 2^(−n/12))
```

from the nut, where `L` is the scale length. So capturing the nut and the bridge
gives every fret on the instrument. Three captures give the whole fretboard:

| capture | gives |
|---|---|
| nut, low-E side | origin, and one corner of the board |
| nut, high-E side | string spread, and the board's width axis |
| bridge (or 12th fret) | scale length, neck axis, and with the above, the plane |

Everything is then stored *relative to the controller's pose*, which is the
entire point of mounting it: the guitar moves, the fretboard model moves with
it, and nothing needs recalibrating when the player shifts in their seat.

Stored as a `FretboardProfile` beside the existing keyboard and kit profiles —
additive fields on `CalibrationProfile`, migrated forward the way the kit was,
never a bump that un-anchors an instrument the player already measured.

## Input: the Jamstik, and why it changes the design

**Bluetooth will work here where it does not in most DAWs.** Windows exposes BLE
MIDI only through WinRT, not the legacy WinMM API. AuralPrimer's `midir` is
built with `features = ["winrt"]` — a decision made for other reasons that
happens to be exactly what this needs. The device must be paired in Windows
Bluetooth settings first; after that it enumerates like any other port.

**A MIDI guitar reports the string, and that is the whole game.** A pitch is
playable in up to five places on the neck; a pitch *plus a string* is one place.
So the app can know which position the player is actually in, rather than
guessing — which makes "right note, wrong position" something it can see and
teach. No amount of controller tracking could recover that, and pitch-only MIDI
could not either.

Two things to establish on the hardware before building on it:

- **Mode.** The Jamstik does MPE (strings 1–6 on channels 2–7, channel 1 for
  MPE messages), multi-channel (one fixed channel per string), or single-channel
  (everything on channel 1, no per-string information at all). The first two are
  usable; the third is not, and the prototype should say so plainly rather than
  silently degrading.
- **String order.** Jamstik counts string 1 as the high E, by guitar convention.
  This codebase indexes string 0 as the low E. That inversion must be handled in
  one place with a test, or every position will be mirrored — and a mirrored
  fretboard looks plausible, which is what makes it dangerous.

Roland GK synths behave the same way in their per-string modes, so the same path
serves both.

## Phases

| phase | delivers | why here |
|---|---|---|
| **1** | Desktop Nashville fretboard view: zones, three tiers, position changes, reading from `harmony.json` | The concept is the risk. A monitor answers "does this read while playing?" in a day; a headset answers it in a fortnight. |
| **2** | Jamstik input: per-string MIDI → live position, shown against the chart's | Cheap once phase 1 exists, and it is what makes the view interactive rather than a diagram. |
| **3** | Port `bass_tab_optimizer` to six strings + GuitarSet accuracy benchmark | Until this lands, the chart side of the view is wrong — see the blocker. Benchmark first, so the port is measured rather than asserted. |
| **4** | MR: detached controller, three-pinch fretboard calibration, notes onto real frets | Everything above is instrument-agnostic and reusable; this is the part that needs hardware. |

Phase 3 can run in parallel with 1 and 2 — the view and the data are independent
problems, and the view can be developed against hand-written fingerings.

## Three things that will bite

1. **Nut-collapsed fingerings** (above). The view will look like it works and be
   musically wrong, which is the worst failure mode available.
2. **The two existing visualizers disagree on string order.** `viz-tab` puts
   string 0 (low E) at the top; `viz-fretboard` puts it at the bottom — and the
   comment at `viz-tab/index.ts:703-704` justifying the former is factually
   wrong about which string is which. Pick a convention and fix the comment
   before adding a third surface.
3. **Tuning is dropped at load.** `FingeringFile` has no `tuning` field, so the
   pack's declared tuning never reaches the renderer and `viz-tab` falls back to
   hardcoded standard tuning. `explicitFretPosition` also never checks that
   `tuning[string] + fret == pitch`. Drop-D or a capo will render confidently
   wrong. There are four duplicate copies of the standard-tuning array to
   consolidate while fixing this: `cli.py:972`, `feedpak_writer.py:79`,
   `viz-tab/index.ts:46`, `guitarset.py:45`.

## Open questions

- **Cherry-pick or re-derive `0422136`?** The commit brings a three-layer bass
  pipeline, of which only the optimiser is wanted here. Taking the file alone is
  cleaner than the merge; taking the branch drags in a technique classifier and
  a `notes.tab.json` sidecar that this feature does not need.
- **Position numbering** — the blues/rock convention above is a guess at what
  "first position" means to this player. It is one constant, but every label in
  the feature hangs off it.
- **What drives the zone**: the chart's fingerings, the player's actual position
  from the Jamstik, or a damped consensus of both? Once phase 2 exists the live
  position is the more trustworthy signal, which may make phase 3 less urgent
  for *playing* while remaining essential for *reading ahead*.
- **Where the controller mounts.** Headstock moves most and is where the fretting
  hand travels; the upper bout is steadier but further from the neck. A physical
  question, answered by trying it, not by reasoning.
- **Does the highway stay attached to the neck when the guitar moves?** Attached
  is honest and may be unreadable while the player shifts; floating is readable
  and may drift from the frets it is pointing at.
