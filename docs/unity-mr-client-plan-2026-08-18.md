# Unity Mixed-Reality client — implementation plan

Date: 2026-08-18
Status: **draft for review** — several requirements still open (see [Questions](#questions-i-need-answered))

**Target: Meta Quest 3 / 3S / Pro** (Horizon OS). Multiplatform where it is free;
Meta SDK only where a capability is otherwise unavailable — see
[SDK strategy](#12-sdk-strategy-multiplatform-by-default).

**Architecture: host-served.** The desktop app stays the host and keeps all
MIDI; the headset is a renderer joined over the LAN by UDP multicast discovery
modelled on the existing AugmentedDefense implementation — see
[§5 Host ↔ headset link](#5-host--headset-link).

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

**Decided: the desktop app stays the host and keeps all MIDI.** That single
choice closes the largest risk in this plan. The earlier draft treated MIDI on
Horizon OS as make-or-break — whether `android.media.midi` exists, whether a raw
USB-MIDI class parser would be needed, whether the Quest's only USB-C port could
carry a bus-powered controller and charge at once. **All of that is now moot.**
The host already reads the Axiom reliably, already has learned transport
bindings, and already persists them.

What each side owns:

| Host (desktop, existing app) | Headset (Unity) |
|---|---|
| MIDI input, transport bindings, wait-mode gating | Rendering the note lane in the real room |
| Song position — the single source of truth for time | Keyboard calibration + spatial anchors |
| Chart + tempo map, served to the headset | Local clock disciplined to the host's |
| Audio playback (already A/V calibrated) | Live key highlight from host-relayed note state |
| Ingest, transcription, pack authoring | Nothing that must survive a lost link |

The headset becomes a **thin, stateless-by-design renderer**: it holds no pack
storage, no MIDI stack, and no authority over time. That is a much smaller
Phase 1 than the standalone route, and it removes "how do packs get to the
headset" as a problem entirely — the host serves the chart on session start.

**Ingest never moves.** Demucs, the transcription models and the frozen sidecar
stay on desktop in AuralStudio.

### 1.2 SDK strategy: multiplatform by default

The project is already on the right footing: `com.unity.xr.meta-openxr` exposes
Quest's passthrough, planes, meshing, bounding boxes and anchors **through
ARFoundation**, so the app codes against the vendor-neutral API and Quest
becomes a build target rather than a fork.

The rule: **ARFoundation / OpenXR is the default; Meta XR SDK is opt-in per
capability, behind an interface.** Reach for it only when something genuinely is
not exposed — and when that happens, isolate it so a later Android XR or SteamVR
build swaps one implementation instead of unpicking gameplay code.

Consequences worth planning around now:

- **The performance floor is Quest Pro / 3S, not Quest 3.** Quest Pro is XR2
  Gen 1, with a weaker GPU and lower-resolution colour passthrough. Budget the
  note lane, glow and transparency for the floor, or Pro users get a materially
  worse read of the notes.
- **Depth sensing differs across the three.** Quest 3 has a depth projector;
  3S and Pro do not. Anything leaning on depth or plane quality must degrade to
  the manual point-and-pinch path — which is exactly why the flow in §4 is built
  on user-placed points rather than automatic surface detection.
- **Passthrough colour fidelity varies.** The note palette was chosen against a
  black canvas; it must be validated on Pro as well as 3 (Phase 0 spike 4).

---

## 2. Parity inventory

What "feature parity" actually covers, from the current client:

| Capability | Desktop today | MR notes |
|---|---|---|
| Song library | feedpak containers, folder scan | Needs a **C# feedpak reader** (manifest.yaml, notes.mid, stems, features) |
| Chart parse | Hand-rolled SMF parser in TS | Port to C# — pure logic, testable |
| Piano roll | `viz-tab` canvas renderer | **Rebuild in 3D** — the centrepiece; anchored to the real keyboard |
| Sheet music | `viz-tab/sheetMusic.ts` | **Deferred past v1**; world-space quad when it lands |
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
| Multi-player stages | up to 4 canvases | **Out of scope for v1** |
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

1. **Discovery across the real network.** Rust host beacons, Quest finds it and
   completes the handshake — on the actual Wi-Fi, not a wired LAN. Confirm the
   Android multicast lock is doing its job (without it Android silently drops
   multicast, which looks exactly like a host that is not there), and check
   behaviour when the PC has several NICs.
2. **Clock discipline and end-to-end latency.** The number that decides whether
   this is playable: key press on the Axiom → host reads it → headset renders
   the response. Measure it under load, and measure playhead jitter with the
   client running a disciplined local clock rather than rendering raw packets.
   *If the total is too high for the visual to feel attached to the sound, that
   changes the design, not just a constant.*
3. **A/V sync with audio on the host, Ableton included.** Audio leaves the
   interface at the desk; notes render on the headset. Measure the offset
   between audible onset and the note crossing the hit line, in passthrough, at
   target framerate — **with Ableton in the path**, since its buffer is part of
   what the player hears and the desktop's existing calibration was not measured
   through it.
4. **Passthrough legibility.** Render a note lane over a real keyboard in a
   normally-lit room, on **Quest Pro as well as 3**. The palette was chosen
   against a black canvas and will likely need rework, especially the black-key
   colours.
5. **Spatial anchor persistence.** Save an anchor, quit, relaunch, confirm the
   keyboard alignment survives.
6. **Link loss behaviour.** Pull the Wi-Fi mid-song. The headset should coast on
   its local clock and recover, never freeze or jump.

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

### Input: hand tracking, and why play mode should need none

Hands are the right choice at a keyboard — controllers would have to be put down
to play — and XR Hands 1.8.1 is already in the project.

Hand tracking degrades badly in one specific pose: fingers on keys, hands
self-occluding, palms flat near a surface, below the headset's downward view.
**Menu placement resolves this rather than working around it**: the panel lives
up in the air, so using it means raising the hands into clear, well-tracked
space, and the fingers-on-keys pose is never asked to produce a gesture. Stated
as a rule so it survives later UI work: **nothing at keyboard height should
require a gesture.**

- **The menu is a grabbable world-space panel** floating above and in front of
  the player (XRI 3.5 grab interactables). Positioned once, it stays put.
- **Summoned by the standard palm-up gesture**, matching platform convention so
  it feels native. Two cautions: confirm it does not collide with the gesture
  Horizon OS reserves for its own menu, and **always keep a non-gesture
  fallback** — hands on keys will produce false negatives, and a player who
  cannot summon the menu is stuck. The Axiom's buttons are the natural fallback,
  since the host already reads them.
- **Calibration uses pinch**, deliberately: hands are up, clear of the keys and
  moving slowly, which is when tracking is most dependable, and pinch-to-place
  gives precision at the key edges that a ray would not.
- **Play mode needs no gesture at all.** Transport lives on the Axiom's buttons
  via the learned CCs, so the player never lifts their hands to control
  playback — a direct dividend of the host-owns-MIDI design.

Seated at the instrument is the initial target. The panel being grabbable is
what makes a later standing or room-scale mode a question of where it is placed
rather than a redesign.

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

## 5. Host ↔ headset link

Discovery follows the implementation already proven in **AugmentedDefense**
(`Assets/Core/NGO/UdpMulticastServer.cs` / `UdpMulticastClient.cs`), because it
already handles the parts that make LAN discovery flaky in practice.

### The pattern, as built

1. Both ends join multicast group **239.0.0.222** on port **47777**.
2. The host beacons `GameServer|{hostIP}|{port}` once a second to the group.
3. A client that hears a beacon replies **unicast** `ConnectRequest`.
4. The host answers `UTPAck|{sessionPort}` and records the client, ageing it out
   after 30 s of silence.
5. The client retargets to `{hostIP}:{sessionPort}` and opens the real session.

Details worth copying verbatim rather than rediscovering:

- **Android multicast lock.** `WifiManager.createMulticastLock` acquired for the
  app's lifetime. Without it Android silently discards multicast, which presents
  as "the host isn't there" with no error anywhere.
- **Per-platform bind.** Android binds `IPAddress.Any` and disables
  `MulticastLoopback`; desktop binds the specific local address and sets
  `MulticastInterface` explicitly — which is what makes it behave on a machine
  with several NICs (Ethernet + Wi-Fi + virtual adapters).
- `ReuseAddress` / `ExclusiveAddressUse = false`, background threads for beacon
  and listen, and a main-thread action queue to marshal the connect back into
  Unity.
- Beacon on a timer, ack unicast: the host is discoverable continuously, so a
  headset joining late or rejoining after a drop needs no user action.

### The one required deviation

In AugmentedDefense both ends are Unity, so the ack advertises an **NGO/UTP**
port and Netcode takes over. **Here the host is the Rust/Tauri app — there is no
NGO on that side.** So:

- The **discovery layer is reproduced faithfully** (same group, port, message
  grammar, timing, and platform handling) in Rust on the host and C# on the
  headset.
- The **ack's port points at our own session socket**, and the session speaks a
  protocol we define rather than Netcode.

### Our socket and grammar (distinct from AugmentedDefense)

Same mechanism, different address space and a self-identifying beacon, so the
two protocols cannot mistake each other on a network running both:

| | AugmentedDefense | AuralPrimer |
|---|---|---|
| Group | `239.0.0.222` | **`239.255.61.88`** |
| Port | `47777` | **`47761`** |
| Beacon | `GameServer\|ip\|port` | **`AURALPRIMER\|1\|{hostIP}\|{sessionPort}\|{hostName}`** |
| Request | `ConnectRequest` | **`AP-CONNECT\|1\|{clientName}`** |
| Ack | `UTPAck\|{ngoPort}` | **`AP-ACK\|1\|{sessionPort}`** |

`239.255/16` is the IPv4 Local Scope (RFC 2365), which is explicitly meant for
local use and is not forwarded beyond it — a better fit for LAN discovery than
`239.0.0/8`, whose organisation-local scope routers may carry further.

Both ends **must ignore any datagram that does not open with their own magic and
version**. The version field is there so a future change can be detected rather
than mis-parsed. `hostName` is included because more than one PC on the network
may be running the host, and the headset should be able to say which it found.

### Timebase: the desktop is authoritative, and the headset needs no calibration

The desktop owns song position; the headset only draws. The problem that creates
is real but solvable **without adding a user-facing calibration step**: sound
leaves the audio interface at the desk while the notes light up inside a headset
that has its own render and compositor latency. Left uncompensated the notes drift
visibly late against what you hear.

The fix is to compensate from values that are **measured or predicted
automatically**, never dialled in by hand:

1. **Clock offset — measured.** The session runs an NTP-style exchange: the
   client timestamps a request, the host stamps receipt and reply, the client
   stamps arrival. That yields `offset = hostClock − localClock` plus a
   round-trip time. Keep the sample with the **lowest RTT** over a rolling window
   rather than averaging — on Wi-Fi the fast samples are the honest ones, and
   averaging lets a single stalled packet skew the clock.
2. **Display latency — predicted by the runtime, not guessed.** OpenXR already
   reports the predicted display time for the frame being submitted: when *this
   frame's photons* will actually reach the eye. Render each frame for that
   instant instead of for "now".
3. **Audio latency — already measured, on the host.** The desktop client has
   calibrated `av_audio_offset_ms` in `settings.json`. The host sends it; note
   that its *video* offset no longer applies, because the video is no longer the
   monitor.

Per frame the client therefore renders:

```
hostClockAtPhotons = predictedDisplayTime + clockOffset
songTimeToRender   = lastSongTime
                   + (hostClockAtPhotons - lastSampleHostClock)
                   + hostAudioOffset
```

Everything on the right is measured or supplied. **No headset-latency
calibration screen, and none of it changes per user.**

Being straight about the limit: this removes the *user-facing* calibration, not
the physics. If a runtime does not expose a usable predicted display time, the
fallback is a single constant we characterise once during development — a build
value, still not something a player is asked to tune. Phase 0 spike 3 is what
confirms the compensated result actually lands.

### Ableton shares the keyboard, and the game does not know about it

The host runs **Ableton Live**, which voices the keyboard — the sound of the
player's own playing is Ableton's. AuralPrimer is unaware of it: **both
applications read the Axiom concurrently**, and AuralPrimer plays the song audio
while Ableton plays the performance.

```
                 ┌──▶ Ableton ──────┐
Axiom ──USB──────┤                  ├──▶ audio interface ──▶ what the player hears
                 └──▶ AuralPrimer ──┘
                          │
                          └──LAN──▶ headset (note lane + key highlight)
```

That this works at all is worth recording, because it is a property we must not
break. On Windows the legacy **WinMM** MIDI API hands an input port to one
process exclusively, while **WinRT** (`Windows.Devices.Midi`) permits multiple
clients — and AuralPrimer's `midir` is built with the `winrt` feature precisely
because of it. The evidence is already in hand from this project: the Python
helper written earlier uses `python-rtmidi` over WinMM and *cannot* open the
Axiom while AuralPrimer holds it, whereas Ableton and AuralPrimer coexist
happily. macOS CoreMIDI and ALSA are multi-client too, so the constraint is
Windows-specific.

**Consequence for the plan: no MIDI routing work, no virtual ports, no
loopback.** The existing setup already delivers what the headset needs. Two
notes only:

- **Keep the WinRT backend.** Dropping to WinMM would silently make AuralPrimer
  and Ableton mutually exclusive, and the symptom — one of them stops seeing the
  keyboard — would look like a hardware fault rather than a backend change.
- The learned transport CCs (20-25) reach Ableton too. If Ableton has MIDI
  mappings on those numbers it will act on them; worth a glance at its mapping
  if a transport press ever does something unexpected in Live.

**Timebase stays simple.** Ableton is not in AuralPrimer's clock or audio path,
so AuralPrimer is transport master outright and the headset follows it directly.
No external-clock hop is needed, even though the client supports one.

### Session traffic

Three streams, each with different requirements:

| Stream | Direction | Transport | Notes |
|---|---|---|---|
| Chart + tempo map | host → headset | reliable (TCP) | Sent once on session start; removes any need for pack storage on the headset |
| Song position | host → headset | unreliable (UDP), ~30–60 Hz | `(songTime, hostClock)` samples; the client **disciplines a local clock** to them and renders from that, never straight from packets, or every dropped datagram becomes a visible stutter |
| Live MIDI note state | host → headset | unreliable (UDP), on change + periodic | Send the **full set of held pitches**, not deltas. It fits in a handful of bytes and a lost packet then self-heals on the next one, where a lost note-off would leave a key lit forever |

Transport control travels the same link: the host already owns the bindings, so
the headset sends intent (`play`, `seek`) and the host remains the authority.

## 6. Phased delivery

**Phase 1 — Vertical slice (keys only).** Discovery + session link, host serves
chart and position, disciplined client clock, calibration flow, 3D piano-roll
lane anchored to the real keyboard, live key highlight from host-relayed MIDI.
Audio plays on the host. One song, end to end. This is where the concept is
proven or killed — and note it needs **no feedpak reader and no MIDI stack on
the headset**, which is most of what made the standalone route expensive.

**Phase 2 — Practice loop.** Wait mode, Nashville labels, note spacing, seek /
jog. All of these are host-side logic already built; the headset renders their
state, so this phase is mostly protocol and presentation.

**Phase 3 — Library and comfort.** Song choice from inside MR, instrument
profiles, seated ergonomics, link-loss polish.

**Phase 4 — Remaining surfaces.** Sheet music, drum highway (needs its own
anchor, on the kit rather than the keyboard), lyrics, guitar/bass fretboard —
**all deferred**: v1 is the piano roll on keys.

**Later — scoring.** Explicitly out of scope for now; the tool is for learning.
Worth noting what that buys: with no hit-window judgement, the input-latency
budget is comfortable, because the live key highlight is feedback rather than
adjudication. Adding scoring later tightens it considerably and is the one
future feature that could force a rethink of the network design — so the
protocol should carry note timestamps from the host even now, when nothing
consumes them yet.

---

## 7. Repo and licensing concerns (flagging early — cheap now, expensive later)

1. **Ignores — done.** The parent `.gitignore` now covers Unity's generated
   output (`Library/`, `Temp/`, `Logs/`, `UserSettings/`, IDE project files,
   build products), unanchored so the patterns apply at whatever depth the
   project sits. Verified: `Assets/`, `ProjectSettings/` and `Packages/` are
   tracked; `Library/` and the `.csproj` files are not.
2. **Nested `.git` — still blocking.** `UnityClient/Aural Primer` carries its own
   repository (one commit, `Initial check-in`). While it exists, `git add` from
   the parent records a **gitlink** — a bare commit hash, none of the files. To
   keep the client in this repo that nested `.git` has to go, or become a proper
   submodule. Left alone pending a decision: it is history, however small.
3. **Licence — GPL-3.0-or-later plus a §7 linking exception.** Matching the
   parent is right for our source, but on its own it is not sufficient once
   builds are distributed. The Unity runtime is proprietary and cannot be
   sublicensed under GPL, so a compiled binary is a combined work we could not
   convey under GPLv3 in full. **GPLv3 §7 exists precisely for this**: an
   additional-permission exception allowing linkage with the Unity runtime
   resolves it, and it is a standard, well-understood addition. Source-only
   distribution is largely unaffected either way.

   The harder case is **storefronts**: their terms typically impose
   redistribution and usage restrictions that GPL forbids, which is the
   long-running friction between GPL software and app stores. Sideloading and
   source releases avoid it entirely. Not legal advice — but the exception is
   cheap, and belongs in place before the first build leaves the machine.
3. **Model/asset licensing** still applies: anything bundled keeps the same
   attribution obligations as the desktop client (README rules).

---

## Requirements — settled and outstanding

### Settled

| | Decision |
|---|---|
| Target | Quest 3 / 3S / Pro; multiplatform where free (OpenXR/ARFoundation first) |
| Architecture | Host-served — desktop is the app, headset is a display |
| MIDI | Host keeps all of it; risk of on-headset MIDI closed |
| MIDI sharing | Ableton and AuralPrimer read the Axiom concurrently (WinRT multi-client) |
| Transport master | AuralPrimer, outright; Ableton is unaware and voices the performance |
| Timebase | Desktop authoritative; headset compensates automatically, no calibration screen |
| Audio | Host, through the existing interface |
| Discovery | UDP multicast on `239.255.61.88:47761`, AugmentedDefense pattern, own magic |
| v1 surface | Piano roll on keys — sheet music, drums, fretboard, multi-player deferred |
| Input | Hand tracking; grabbable menu in the air, calibration by pinch, play mode gesture-free |
| Scoring | Later — v1 is a learning tool |
| Ingest | Stays on desktop in AuralStudio |
| Licence | GPL-3.0-or-later as parent, **plus a §7 linking exception for the Unity runtime** |
| Repo | Tracked in the parent; Unity ignores added (nested `.git` still to remove) |
| Pack delivery | Moot — the host serves the chart, the headset stores nothing |

### Still outstanding

1. **May I remove the nested `.git` under `UnityClient/Aural Primer`?** One commit
   of the untouched template, and it is the only thing stopping the parent from
   tracking the project's files instead of a bare gitlink.
2. **Do you intend to distribute builds** — sideload, or a storefront? It decides
   how carefully the §7 exception needs wording, and whether the app-store
   friction in §7 applies to you at all.
