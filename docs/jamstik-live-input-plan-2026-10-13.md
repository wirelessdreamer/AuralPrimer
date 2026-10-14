# Plan — Jamstik Studio live input, on screen and in the headset (2026-10-13)

What the player frets on a Jamstik Studio MIDI guitar shows up live on the
guitar views: on the desktop's 2D neck and on the real neck in the headset,
against the chart. This is phase 2 of the
[Nashville fretboard plan](guitar-nashville-fretboard-plan-2026-09-30.md).

Supported mode: **Multi-Channel** (one channel per string, Jamstik's default
channels 1–6). MPE (strings on 2–7) is the same thing shifted by one channel
and comes almost for free. Single Channel cannot be supported, since it gives
no string, and the app says so instead of guessing.

## Where things stand

The input path is almost all there; nothing turns a channel into a string.

| Stage | Today | Gap |
|---|---|---|
| Port | `midir` with WinRT, so USB and paired BLE ports enumerate; selection persisted (`lib.rs:2658`), reopened at start | none |
| Events | pushed per message as `midi_input_message`, channel and 14-bit pitch bend included (`midi_clock_input.rs:42`) | none |
| Held notes | `MidiInputStateTracker` keys notes by `channel:pitch` (`midiInput.ts:63`) | pitch bend ignored |
| 2D views | `liveInputNotes` reaches every renderer each frame (`main.ts:1409`) | `PianoLiveInputNote` has no channel; `GuitarRenderer` ignores live notes |
| MR link | NOTES datagram `{pitch, vel}` × n, sent on change plus 250 ms keepalive (`protocol.rs:195`) | no string or fret; publishing rate-limited to 33 ms on the frontend tick |
| Unity | `MrLinkBehaviour.HeldNotes`, read by the keyboard overlay | `GuitarViews` never reads them |

## Design

### 1. One mapping, in one place, tested

A pure function `liveFrets(activeNotes, open, cfg)` in `viz-tab` (beside
`fretted.ts`) returns `{ s, f, midi, velocity, startedAtUs }[]`, with:

- **String:** `s = (stringCount − 1) − (channel − cfg.firstChannel)`. Jamstik
  numbers string 1 as the high E; the charts number string 0 as the low E.
  This line is the only place the flip happens. Its test plucks each open
  string and checks for low-E-is-0. A mirrored neck looks plausible, which is
  why it needs a test rather than a glance.
- **Fret:** `f = midi − open[s]`. Outside 0–24 means the channels or the
  tuning disagree with the guitar; the note is dropped and counted toward the
  warning below.
- **Config:** `firstChannel` 1 (Multi-Channel) or 2 (MPE), plus the guitar's
  tuning. Defaults: Multi-Channel, standard tuning.

Everything downstream (2D views, the MR link) consumes this output. The
headset never re-derives strings.

### 2. Knowing the setup is right: "pluck the open strings"

A one-time check in the MIDI panel, kept for later runs:

1. "Pluck each open string, low to high."
2. Six notes arrive. Their channels give `firstChannel` and confirm the
   order. Their pitches give the tuning, so drop D or an alternate tuning set
   in the Jamstik app is read, not assumed.
3. Problems are said plainly:
   - all six on one channel: "Jamstik is in Single Channel mode, switch it to
     Multi-Channel in the Jamstik app";
   - order reversed: fixed by flipping the mapping, not by asking the player
     to change anything.

Without this check, a running detector also catches Single Channel mode: two
held notes on one channel more than a few times means the strings can't be
told apart, and a banner says so.

### 3. 2D: what the player frets, against what the chart wants

- `PianoLiveInputNote` gains an optional `channel`. The tracker already has
  it, so this is plumbing.
- `GuitarRenderer` reads `liveInputNotes`, maps them once per frame, and
  draws a **live marker** (a bright ring with a short glow on attack) at
  `(s, f)` on every neck it draws: the Nashville neck, the coach neck, the
  highway's hit line and the lanes' strike line.
- **Judged against the chart** (notes sounding now ± 80 ms):
  - **hit**: right string and fret. Ring in the note's colour, chart note
    flashes.
  - **right note, other place**: right pitch, different string. Amber ring
    with a thin line to where the chart has it. This is what string data makes
    possible and pitch-only MIDI can't: "right note, wrong position".
  - **off**: not in the chart. Plain grey ring, no scolding.
- Nashville neck: the live position also drives the "you are here" box when
  the player has drifted from the chart's position (the open question in the
  phase plan). Proposal: show both, chart box solid, player box dashed.

### 4. Headset: the same markers on the real neck

- **Protocol:** a new optional datagram, `FRETTED_NOTES 0x42`:
  `hostClock u64 | count u8 | n × {pitch u8, vel u8, string u8, fret u8}`,
  with string `0xFF` for a note with no string. It is gated by a new WELCOME
  feature, `fretted_notes`, so an older headset build ignores it, per the
  rule in `docs/mr-link-protocol.md` §8. No version bump. The desktop sends
  it alongside NOTES (the keyboard overlay keeps using NOTES).
- **Latency:** today a press waits for the next 33 ms publish tick, then the
  4 ms stream loop. Live input publishes on the MIDI event itself (the
  `auralprimer:midi-input` DOM event) instead of the render tick, which saves
  up to 33 ms. Budget: Jamstik pitch detection (fixed by the hardware) +
  IPC (~1 ms) + Wi-Fi UDP (~5–15 ms) + one headset frame (~14 ms at 72 Hz).
- **Unity:**
  - `MrProtocol.TryDecodeFrettedNotes`;
  - `MrLinkBehaviour.HeldFrets`;
  - a `GuitarViews.Live` partial that draws the same three marker kinds at
    `NeckPoint(s, f)` on the anchored neck, plus the highway hit line.
  
  Strings are already 0 = lowest on both sides (`FrettedChart.cs:22`,
  `FretboardGeometry.StringZ`).
- **Tests:** Rust encode/decode and truncation for 0x42 (beside
  `protocol.rs:482`), a C# decode test in `MrProtocolTests`, and a feature-gate
  test (not sent unless the headset asked for it).

### 5. Later, not in this pass

- **Bends.** Multi-Channel sends pitch bend per string, which can draw live
  bends and score bend targets. The tracker ignores bend today; adding it is
  small, but judging bends is its own design.
- **Scoring and stats** (accuracy per section) belong with the existing
  performance capture (`PerformanceCapture.cs`), not this pass.

## Phases

| # | Delivers | Verified by |
|---|---|---|
| 1 | `liveFrets` mapping + config + Single Channel detector | unit tests: open-string pluck, MPE offset, out-of-range fret, single-channel detection |
| 2 | Pluck-the-open-strings check in the MIDI panel | test with recorded event sequences; then the real Jamstik |
| 3 | 2D live markers + hit / other place / off judging on all guitar views | renderer tests on the judging; harness page fed synthetic MIDI; then the Jamstik in the portable |
| 4 | `FRETTED_NOTES` datagram, feature-gated, published on the MIDI event | Rust + C# protocol tests; desktop to headset over the network |
| 5 | Unity live markers on the anchored neck and highway | Quest build (6000.3.15f1), played with the Jamstik on its mount |

Phases 1–3 are desktop only and useful alone. 4–5 need 1 but not 2–3.

## Open questions

1. **Judging on or off by default?** Showing what you play is neutral;
   marking it against the chart turns practice into a test. Proposal: on,
   with a toggle.
2. **Tuning source:** the pluck check (proposed), the chart's declared
   tuning, or both, with a warning when they differ?
3. **Which views first?** Proposal: the Nashville neck and the highway, since
   those are the ones on screen when playing; tab and coach after.
