# Unity Mixed-Reality client — implementation plan

Date: 2026-08-18
Status: **draft for review** — several requirements still open (see [Questions](#questions-i-need-answered))

Target project: `UnityClient/Aural Primer` — Unity **6000.3.15f1**, URP 17.3,
OpenXR 1.17.1 + Meta OpenXR 2.5.1 + Android XR 1.3.1, ARFoundation 6.5,
XR Hands 1.8.1, XRI 3.5.1, Composition Layers 2.5. Android-first (MR Template,
`SampleScene`, template scripts only).

---

## 1. The constraint that shapes everything

The desktop client is a **Tauri app**: a Rust host (cpal audio, `midir` MIDI,
container I/O) under a TypeScript UI, plus a ~3 GB frozen Python sidecar for
ingest. On a standalone headset **none of that host survives**. Ported to
Android XR, every capability has to be re-provided in C# or an Android plugin.

So the first decision is not "which features" but **where the runtime lives**:

| | A. Standalone on-device | B. Desktop-served | C. PC VR |
|---|---|---|---|
| Runs on | Quest / Android XR, no PC | Headset renders, desktop streams audio+MIDI+charts | Unity app on PC, headset as display |
| Audio | Unity audio, stems decoded on device | Desktop keeps cpal; headset gets a mixed stream | Existing desktop stack |
| MIDI | Android MIDI API over USB-C — **unproven on Horizon OS** | Desktop `midir` already works; forward over the wire | Already works |
| Latency risk | Lowest (local) | Wi-Fi jitter on top of A/V sync | Low |
| Product feel | Sit at the piano, headset only | Piano must be near the PC | Tethered |
| Effort | Highest | Medium | Lowest |

**Recommendation: A, with B as the fallback the architecture keeps open.** The
whole point of the MR client is sitting at your instrument without a PC in the
loop. But MIDI input on the headset is the make-or-break unknown, so Phase 0
proves it before anything else is built, and the transport layer is written
behind an interface so a network source can be substituted without touching
gameplay.

**Ingest never moves.** Demucs, the transcription models and the frozen sidecar
stay on desktop in AuralStudio. The Unity client is a **player**; packs are
authored on the PC and synced to the headset. That is a scoping decision worth
confirming, not an assumption I should make silently.

---

## 2. Parity inventory

What "feature parity" actually covers, from the current client:

| Capability | Desktop today | MR notes |
|---|---|---|
| Song library | feedpak containers, folder scan | Needs a **C# feedpak reader** (manifest.yaml, notes.mid, stems, features) |
| Chart parse | Hand-rolled SMF parser in TS | Port to C# — pure logic, testable |
| Piano roll | `viz-tab` canvas renderer | **Rebuild in 3D** — the centrepiece; anchored to the real keyboard |
| Sheet music | `viz-tab/sheetMusic.ts` | Port later; world-space quad |
| Tab / fretboard | fret math in `viz-tab` | Only if guitar/bass is in scope |
| Drum highway | `viz-drum-highway` | Natural in 3D; needs its own anchor (kit, not keyboard) |
| Lyrics | `viz-lyrics` | Straightforward world-space text |
| Nashville numbers | degree labels on notes | Port `pitchToNashville` + key inference |
| Note spacing multiplier | `lookAheadSec / multiplier` | Same maths, now in metres |
| Wait mode | `learnGateTick` | Ports directly; input source changes |
| Transport | cpal play/pause/seek/loop/rate | Unity `AudioSource` + a timebase abstraction |
| A/V sync calibration | measured audio/video offset | **Harder in XR** — compositor adds its own latency |
| Metronome | scheduled clicks | Unity audio scheduling |
| Stem mixer | per-stem gain | Multiple `AudioSource`s, or pre-mixed |
| MIDI input | `midir` | The Phase 0 risk |
| MIDI transport learn | CC/note binding, persisted | Port the binding model; it is already pure logic |
| Multi-player stages | up to 4 canvases | Probably out of scope for v1 — confirm |
| Ingest / import | Python sidecar | **Stays on desktop** |

### Code that should be shared, not rewritten twice

These are pure functions with no DOM or Rust dependency, and they are exactly
the parts where a subtle divergence between clients would be invisible and
maddening:

- keyboard layout maths (`buildKeyboardLayout`: white/black placement)
- `pitchToNashville`, `inferKeySignature` (Krumhansl profiles)
- `midiToNoteName`, pitch-class tables
- MIDI transport binding match/capture (`midiTransportBindings.ts`)
- wait-mode grouping (`buildLearnGroups`)
- tempo-map / tick→seconds conversion

Port them to a small C# library with the **same unit tests ported alongside**,
so both clients are provably answering the same questions the same way.

---

## 3. Phase 0 — de-risking spikes (do these first)

Nothing below matters if these fail. Each is a throwaway scene, not product code.

1. **MIDI input on the target headset.** Plug the Axiom 61 into the headset via
   USB-C. Can Unity see note on/off through `android.media.midi`? Measure
   round-trip latency (key press → app callback). *If this fails*, fall back to a
   desktop→headset MIDI bridge and re-scope to topology B.
2. **Audio latency and A/V sync.** Play a stem, measure the offset between
   audible onset and the rendered note crossing the hit line, in passthrough,
   at target framerate. This decides whether the existing A/V calibration model
   transfers or needs an XR-specific one.
3. **feedpak on device.** Read a real pack from persistent storage: parse the
   manifest, decode a stem, parse `notes.mid`. Measure load time for a 4-minute
   song. Decide streaming vs preload.
4. **Passthrough legibility.** Render a note lane over a real keyboard in a
   normally-lit room. Are the colours from the 2D client readable against a real
   scene? (They were chosen against a dark canvas — they will likely need
   rework, especially the black-key palette.)
5. **Spatial anchor persistence.** Save an anchor, quit, relaunch, confirm the
   keyboard alignment survives.

---

## 4. Keyboard calibration flow (explicit requirement)

Goal: falling notes line up above the **real** keyboard, for 49/61/88-key
instruments. The design principle is that every step is **verifiable by MIDI**,
so alignment is confirmed by evidence rather than by eye.

### Step 1 — Identify the instrument (auto, not a menu)

Rather than asking "49, 61 or 88?", prompt: **"play your lowest key, then your
highest key."** That yields the exact MIDI range, which gives both the key count
and the starting note. It handles the real variance a size menu gets wrong:

| Keys | Typical range | White keys | Expected width @ 23.5 mm |
|---|---|---|---|
| 88 | A0–C8 (21–108) | 52 | ≈ 1.22 m |
| 76 | E1–G7 (28–103) | 45 | ≈ 1.06 m |
| 61 | C2–C7 (36–96) | 36 | ≈ 0.85 m |
| 49 | C2–C6 (36–84) | 29 | ≈ 0.68 m |

Offer the size menu as a manual override for anyone calibrating without MIDI
connected.

### Step 2 — Register the keyboard in space

Two points define the instrument axis: pinch (or controller-point) at the
**outer left edge of the lowest white key**, then the **outer right edge of the
highest white key**. That gives origin, direction and playable width in one
gesture pair. A third point on the near edge of the white keys sets depth and
lets the plane tilt with the instrument.

**Cross-check the measurement.** Measured span ÷ white-key count should land
near 23.5 mm. If it is 15 % out, the user probably mis-tapped — say so and offer
a retry rather than silently building a skewed lane. This is cheap and catches
the most common calibration error.

### Step 3 — Verify by playing (the important step)

Draw the virtual keys over the real ones and ask for a few notes: lowest, a
middle C, highest, and one black key. The app knows which pitch arrived, so it
can highlight the key it *believes* was pressed and ask "is the highlight on the
key you played?" Any offset or reversal shows up immediately, and a
one-key-off error — the classic failure — becomes obvious instead of subtly
wrong for a whole session.

Offer fine nudge (position, yaw, scale) with a live overlay before accepting.

### Step 4 — Shape the note lane

The falling-note plane rises from the keyboard toward the player. Adjustable:

- **height** — how far up the lane extends (this is the look-ahead window)
- **tilt** — vertical, or raked toward the face for a seated player
- **spacing multiplier** — same semantics as the desktop `[` / `]` control
- **near/far opacity** so the lane never obscures the real hands

### Step 5 — Persist

Save as a **named profile** (`Studio upright`, `Axiom 61 on the desk`) holding
the anchor, MIDI range, and lane settings. Multiple instruments and multiple
rooms are normal; re-calibrating every session is not acceptable. Re-verify
with one keypress on load, and offer re-calibration if the anchor is lost.

### Pitch → world position

Port `buildKeyboardLayout` and parameterise it by (lowest, highest) instead of
hard-coded 21–108. White keys tile evenly; black keys straddle at
`whiteIndex × whiteWidth − blackWidth/2`. Map that local X onto the calibrated
axis. **One layout function, shared with the 2D client**, so the two never
disagree about where C♯4 is.

---

## 5. Phased delivery

**Phase 1 — Vertical slice (keys only).** Feedpak reader, chart parse, audio
playback with a timebase, calibration flow, 3D piano-roll lane anchored to the
real keyboard, MIDI input lighting real-key positions. One song, one instrument,
end to end. This is where the concept is proven or killed.

**Phase 2 — Practice loop.** Wait mode, Nashville labels, note spacing, seek /
jog, MIDI transport bindings, A/V offset calibration.

**Phase 3 — Library and comfort.** Song browser in MR, pack sync from desktop,
instrument profiles, per-stem mixing, hand-vs-controller input, seated/standing.

**Phase 4 — Remaining surfaces.** Sheet music, drum highway (own anchor),
lyrics, guitar/bass fretboard — driven by which instruments actually matter.

---

## 6. Repo and licensing concerns (flagging early — cheap now, expensive later)

1. **Nested git repo.** `UnityClient/Aural Primer` has its own `.git` and is not
   ignored by the parent. The parent will treat it as an untracked directory and
   Unity's `Library/`, `Temp/`, `Logs/` are large and churn constantly. Decide:
   submodule, separate repo, or same repo with proper ignores.
2. **GPL-3.0 and Unity.** This project is GPL-3.0-or-later. The Unity runtime is
   proprietary, and GPLv3 requires the complete combined work to be
   GPL-compatible — shipping a GPL app linked against the Unity player is a real
   friction point, not a formality. Usual resolutions are an explicit linking
   exception on our code, or licensing the Unity client separately. **This needs
   a decision before the client is distributed**, and I am flagging it rather
   than deciding it.
3. **Model/asset licensing** still applies: anything bundled keeps the same
   attribution obligations as the desktop client (README rules).

---

## Questions I need answered

**Blocking the plan's shape**

1. **Target device**, precisely — Quest 3/3S only, Android XR (Samsung Moohan),
   both, or PC VR? It decides the MIDI story and the perf budget.
2. **How does the keyboard connect to the headset?** USB-C direct, or do you
   expect the PC to stay in the loop? If MIDI-over-USB does not work on Horizon
   OS, which fallback do you prefer — desktop bridge, or PC VR?
3. **Is the Unity client playback-only?** I have assumed authoring stays in
   AuralStudio. Confirm, or say if the headset needs to import too.

**Scope**

4. **Which instruments matter for v1?** Calibration implies keys first; do
   drums/guitar/vocals need parity in the first release or later?
5. **Multi-player stages** — needed in MR, or single player?
6. **Sheet music mode** — required for parity, or is the piano roll enough at first?

**Product**

7. **Seated at an acoustic/digital piano in passthrough** — is that the primary
   posture? Any standing or room-scale use?
8. **Hand tracking or controllers** for menus? Hands seem right at a keyboard
   (controllers must be put down to play), but that affects the UI toolkit choice.
9. **How do packs get to the headset?** adb sideload, Wi-Fi sync from AuralStudio,
   or cloud?
10. **Is there a scoring/judgement goal here** that the desktop client does not
    have? Today there is no hit-window scoring at all — MR might be where that
    finally matters, and it changes the input-latency budget considerably.
