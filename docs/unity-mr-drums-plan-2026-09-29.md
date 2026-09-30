# Plan — Drums in the mixed-reality client (2026-09-29)

Extends [unity-mr-client-plan-2026-08-18.md](unity-mr-client-plan-2026-08-18.md),
which deferred drums with one line of reasoning that still holds: they need
*their own anchor — kit, not keyboard*.

**Target chosen: cues drawn on the player's real pads, hits arriving as MIDI
from an electronic kit.** Not a floating chart read beside the kit, and not
virtual pads struck with hand tracking. That choice is what makes per-pad
calibration core work rather than a later nicety.

## Where we are starting from

Two facts set the scope, and neither is what you would guess from the desktop
app looking finished.

**There is no drum gameplay to port.** On the desktop, drums are chart and
render only. `viz-drum-highway` draws eight lanes and flashes a receptor
within ±0.16 s of each charted hit — but that flash is driven by the chart,
never by the player. `update()` is a no-op. There is no drum input path, no
hit detection, no scoring, and wait mode is melodic-only: `buildLearnGroups`
reads `selectedMelodicTracks` and never looks at `selectedDrumChartSelection`,
so a drums-only pack produces zero learn groups. Anything interactive here is
new on both ends.

**The MR client has no drum concept at all**, and three of its foundations
assume a keyboard:

| Foundation | What it assumes | Consequence |
|---|---|---|
| `CalibrationProfile` | One line: `leftEdge`→`rightEdge` plus an up vector. Every position is `KeyPosition(layout, pitch)` lerped along it. | A kit is 5–8 discrete pads at arbitrary positions and angles. New geometry, not a new parameter. |
| `NoteHighway` | `FoldPitch`, `KeyPosition`, `IsBlack`, `NormalisedWidth` | Cannot be reused. A sibling script, not a refactor. |
| `MrLinkBehaviour.HeldNotes` | A *held* set, polled per frame | A drum hit is a note-on with an immediate note-off. It can appear and vanish between two frames and never be seen. |

Two things are cheaper than they look. The protocol extends without a version
bump — an optional frame plus a name in `features` is the documented mechanism
(§8). And the MR scene is built procedurally: `WizardPanel` creates its entire
UI in code across 19 `AddComponent` calls, and the highway builds its geometry
from materials with no prefabs. New surfaces need scripts and materials, not
authored assets or hand-edited scene YAML.

## Three defects to fix rather than inherit

1. **`drum_tab.json` is the source, not `notes.mid`.** It carries the Studio
   cleanup edits and the import-time onset alignment, and it already wins on
   the desktop (`songChartLoader.ts`: `tabDrumSelection ?? midiDrumSelection`).
   A client that parses the MIDI silently discards every edit the user made.

2. **Do not round-trip through GM pitches.** The desktop maps tab lanes to GM
   numbers and back into eight lanes, collapsing `hihat_closed`,
   `hihat_open` and `hihat_pedal` into one `HH`. In `fire_in_my_bones` that is
   650 open against 8 closed hits — the articulation is in the pack and thrown
   away at load. Sending lane ids straight from `drum_tab.json` keeps it.

3. **Three lane vocabularies already exist** — `BD/SD/HH/CY/RD/HT/LT/FT` in
   `chartLoader.ts`, `kick/snare/hat/crash/ride/tom1/tom2/tom3` in the
   highway, and `kick/snare/hihat_*/...` in the tab — with two duplicate pitch
   tables that have drifted (the highway's lane order is not the Studio's
   canonical order). **The tab's vocabulary is the wire format.** Adding a
   fourth would be the worst available outcome.

## Protocol

A new optional frame and a new datagram. `protocol` stays `1`; `WELCOME`
gains `"drums"` in `features`, and a host without it simply has no drum mode
in the headset.

### `0x18 DRUM CHART` — host → headset

Lane ids exactly as `drum_tab.json` spells them, so articulation survives.

```json
{
  "songId": "...",
  "title": "Fire In My Bones",
  "durationSec": 414.639,
  "tempoMap": [{ "tSec": 0, "bpm": 136.4, "beatsPerBar": 4 }],
  "kit": ["kick", "snare", "hihat_closed", "hihat_open"],
  "hits": [{ "t": 75.134, "p": "kick", "v": 104 }]
}
```

`kit` is the set of lanes the song actually uses, which is what the tab
already stores — the headset draws only those, so a song with no toms does not
show empty tom lanes.

### `0x42 DRUM HITS` — host → headset, UDP

The reason this cannot ride `HeldNotes`. Sent alongside the position datagram
at the same rate, carrying the note-ons seen since the last packet:

```
u8  count
repeated count times:
  u8  note        (MIDI note number, as the kit sent it)
  u8  velocity
  u64 hostClockUs (when the host saw it)
```

Raw note numbers, not lane ids: the mapping from note to pad is the headset's,
because the headset is where the kit was calibrated. The host forwards
note-ons on channel 9 and does not need to know what kit is attached.

## Calibration

`CalibrationProfile` version 3. The existing keyboard fields are untouched; a
profile gains an instrument and, for drums, a list of pieces.

```csharp
public enum Instrument { Keyboard, Drums }
public Instrument instrument = Instrument.Keyboard;

[Serializable]
public sealed class KitPiece
{
    public string id;          // "kick", "snare", "hihat_open", ... (tab vocabulary)
    public int midiNote;       // learned by striking the pad
    public Vector3 centre;
    public Vector3 rim;        // a point on the edge; radius = |rim - centre|
    public float tiltDegrees;  // adjustable in fine tuning
}
public List<KitPiece> kitPieces = new();
```

**Two captures per pad, not three.** Centre and rim give the position and the
radius but only one in-plane vector, so the pad's facing is seeded as pointing
at the player's head at capture time — which is how a kit is set up — and then
adjusted per piece in fine tuning. Three captures would pin the plane exactly
and cost 24 pinches across a seven-piece kit; this costs 14 and a slider.

**The note is learned by hitting the pad**, not typed. Kits do not agree on
note numbers and the player should never have to look them up. The wizard asks
for a strike, takes the note from the next channel-9 note-on, and moves on.

**Calibrate with the sticks down.** Hand tracking is unreliable through a
closed fist holding a stick, and this is the one part of the flow where a
mis-read pinch puts a pad in the wrong place for the whole session. Say so in
the wizard copy rather than discovering it in testing.

New wizard steps: `PickInstrument` → per piece `PlacePiece` (centre, rim) and
`LearnNote` (strike) → `VerifyKit` → the existing `Menu`.

## Rendering

Both surfaces, for the same reason the keyboard has both: the pads say *now*
and cannot say *soon*.

- **Pad cue** — a ring on the real pad that fills as its hit approaches, the
  same geometry idea as the key preview. Green to play, per the keys: the MR
  key bed's colours are state, not identity, and drums should not invent a
  third convention.
- **Per-pad column** — a short highway rising from each pad, so a note falls
  onto the drum it is asking for. This is the thing a flat screen cannot do,
  and it is the reason to build drums in MR at all.

Colour by piece is available but should wait: the lane colours in
`viz-drum-highway` were chosen for a monitor, and passthrough washes chroma
out. Decide it after seeing a kit in the headset.

## Phases

| Phase | Delivers | Depends on |
|---|---|---|
| **1a** | `DRUM CHART` frame; host sends the tab; headset parses and holds it | nothing |
| **1b** | Kit calibration: place pads, learn notes, profile v3, fine tuning | 1a for the lane set |
| **1c** | Pad cues and per-pad columns | 1b |
| **2** | `DRUM HITS` datagram; hits land visibly, with timing error | 1c |
| **3** | Wait mode for drums — new on the desktop first, then the headset | 2 |

Phase 1 is the whole of "put the headset on and play along"; 2 is the whole of
"and know whether you were on time".

## Risks worth naming now

- **Hand tracking with sticks.** Affects calibration, not play — but if the
  pinch is unreliable even with sticks down, the capture flow needs rethinking
  before anything else is built. Test this first; it is the cheapest thing to
  falsify and the most expensive to discover late.
- **Occlusion.** A kit sits low and forward; the hands are often outside the
  tracking volume. Relevant to any future hand-tracked variant, not to this
  one, since hits arrive over MIDI.
- **Latency budget.** Kit → USB → host → UDP → headset → render. The existing
  clock sync handles song position; a hit landing late is a different number
  and needs measuring before phase 2 promises timing feedback.
- **Passthrough contrast.** A black kit in a dim room is where the cue rings
  have to stay readable. The key bed's opacity lesson applies: translucent
  cues stopped occluding anything and had to go solid.

## Open questions

- Which pieces does the kit have, and does it send distinct notes for open,
  closed and pedal hi-hat? That articulation is preserved through the wire
  format on purpose, and it is wasted if the kit does not send it.
- Positional sensing (rim shots, bell vs bow)? Out of scope here; it would add
  a second note per piece rather than a new concept.
