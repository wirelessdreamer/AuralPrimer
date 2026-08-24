# MR link protocol v1 — host ↔ headset

The desktop AuralPrimer app (**host**) serves the Unity mixed-reality client
(**headset**). The host owns MIDI, song position and audio; the headset renders.

This document is the contract. It exists so the Rust and C# sides can be written
independently and still meet in the middle, and it is deliberately specific
about framing and units — the failures that cost the most time in a protocol
like this are the boring ones: a length prefix that disagrees, a timestamp in
the wrong unit, a byte order nobody wrote down.

Conventions used throughout:

- All integers are **little-endian**.
- All timestamps are **microseconds** (`u64`) from a monotonic clock.
- All song positions are **seconds** (`f64`).
- Text is UTF-8, never null-terminated.

---

## 1. Discovery — UDP multicast *and* broadcast

| | |
| --- | --- |
| Group | `239.255.61.88` |
| Broadcast | `255.255.255.255` |
| Port | `47761` |
| Beacon interval | 1 s |
| Client timeout | 30 s without traffic |

`239.255/16` is the IPv4 Local Scope (RFC 2365): meant for local use and not
forwarded beyond it.

**Each beacon is sent twice — once to the group, once as a broadcast.** Access
points routinely treat the two differently: IGMP snooping drops multicast for a
group nothing has joined on that segment, so a desktop on Ethernet can beacon
correctly while a headset on Wi-Fi hears nothing, with no error reported
anywhere. Broadcast is not subject to that. Both were measured reaching a Quest 3
on Wi-Fi from a host on Ethernet. The client binds `INADDR_ANY` on the port and
receives either, so it needs no knowledge of which arrived; duplicates are
already handled by the once-only handshake.

Discovery messages are UTF-8 text, pipe-delimited, no trailing newline. Every
message begins with a magic and a protocol version, and **both ends must ignore
any datagram that does not open with `AURALPRIMER|1`**. That rule is what keeps
this protocol from colliding with anything else on the same group, and what lets
a future version be detected rather than mis-parsed.

### Beacon — host → group, every second

```
AURALPRIMER|1|BEACON|<hostIp>|<sessionPort>|<hostName>
```

`hostName` is free text without `|`; it exists because more than one machine on
the network may be hosting, and the headset should be able to say which it found.

### Connect request — headset → host, unicast

```
AURALPRIMER|1|CONNECT|<clientName>
```

### Acknowledgement — host → headset, unicast

```
AURALPRIMER|1|ACK|<sessionPort>
```

The headset then opens the session on `<hostIp>:<sessionPort>`. The beacon
continues regardless, so a headset that joins late, or rejoins after a drop,
needs no action from the user.

### The client must send `CONNECT` from a separate ephemeral socket

The beacon listener binds the group port (`47761`). The `CONNECT`/`ACK` exchange
must **not** reuse that socket — it needs its own, on an ephemeral port.

This is not stylistic. With two sockets sharing port `47761` on one machine, a
unicast to that port is delivered to the more specifically bound socket, which
is the host's own. The ack is then swallowed and discovery hangs with no error
on either side. That is precisely the shape of running the Unity Editor on the
host PC, which is the normal development setup — and it was caught by the live
socket test rather than by reading the code, which is the argument for having
one.

Across separate machines either arrangement works; the ephemeral socket is
simply the one that also works on a single machine.

### Platform requirements (not optional)

- **Android must hold a multicast lock** (`WifiManager.createMulticastLock`) for
  as long as discovery runs. Without it Android silently discards multicast, and
  the symptom is indistinguishable from a host that is not running.
- **The host must bind to a specific interface** and set `IP_MULTICAST_IF`.
  A desktop with Ethernet, Wi-Fi and virtual adapters will otherwise beacon out
  of the wrong one.
- Set `SO_REUSEADDR` on both ends.

---

## 2. Session — TCP

One TCP connection carries everything that must not be lost: the chart, control,
and the clock exchange. Frames are length-prefixed:

```
┌────────────┬──────────┬────────────────────┐
│ len: u32   │ type: u8 │ payload (len bytes)│
└────────────┴──────────┴────────────────────┘
```

`len` counts the payload only, and excludes the type byte. Maximum payload is
16 MiB; a larger prefix is a protocol error and the connection is dropped rather
than trusted.

| Type | Name | Direction | Payload |
| --- | --- | --- | --- |
| `0x01` | `HELLO` | headset → host | JSON: `{ "client": string, "protocol": 1, "udpPort": u16 }` |
| `0x02` | `WELCOME` | host → headset | JSON: `{ "host": string, "protocol": 1, "udpPort": u16, "audioOffsetSec": f64, "features": [string] }` |
| `0x10` | `CHART` | host → headset | JSON, see §4 |
| `0x11` | `SONG_CHANGED` | host → headset | JSON: `{ "songId": string }` — headset requests a fresh `CHART` |
| `0x12` | `LIBRARY_REQUEST` | headset → host | JSON, see §6 |
| `0x13` | `LIBRARY` | host → headset | JSON, see §6 |
| `0x14` | `SELECT_SONG` | headset → host | JSON: `{ "songId": string }` |
| `0x15` | `VOICE_QUERY` | headset → host | WAV bytes, see §6 |
| `0x16` | `VOICE_RESULT` | host → headset | JSON: `{ "text": string, "error": string? }` |
| `0x20` | `PING` | headset → host | `u64` client send time |
| `0x21` | `PONG` | host → headset | `u64` echoed client time, `u64` host time at reply |
| `0x30` | `TRANSPORT` | headset → host | JSON: `{ "action": "play"\|"pause"\|"stop"\|"seek", "tSec": f64? }` |

`udpPort` in `HELLO` is where the headset listens for the streams in §3. The
headset binds it before connecting and names it, rather than the host choosing a
number both ends must have free — coupling two machines to one arbitrary port
means the streams silently never arrive if anything already holds it there, and
it makes running headset and host on one machine impossible, which is the normal
case when testing in the Editor. A `HELLO` without `udpPort` is an older client;
the host falls back to streaming to its own port number.

`udpPort` in `WELCOME` is the port the host sends *from*, kept for diagnostics.
The headset sends nothing on UDP, so the host learns the address from the TCP
peer.

`audioOffsetSec` is the host's measured audio latency (`av_audio_offset_ms`
÷ 1000). The host's *video* offset is deliberately **not** sent: the video is no
longer the desktop monitor, and the headset accounts for its own display latency
(§5).

---

## 3. Streams — UDP, host → headset

Loss-tolerant, high-rate, never retransmitted. Both messages are fixed-size
binary so a partial or oversized datagram can be discarded without parsing.

### `POSITION` — 25 bytes, ~60 Hz

```
┌──────────┬─────────────┬──────────────┬────────────┐
│ 0x40: u8 │ songTime f64│ hostClock u64│ flags u8   │
└──────────┴─────────────┴──────────────┴────────────┘
```

`flags` bit 0 = playing. `hostClock` is the host's monotonic clock at the moment
`songTime` was sampled — the pairing is what makes the client's clock discipline
possible, so they must be read together, never separately.

### `NOTES` — 12 + 2n bytes, on change and at least every 250 ms

```
┌──────────┬──────────────┬─────────┬──────────────────────────┐
│ 0x41: u8 │ hostClock u64│ count u8│ n × { pitch u8, vel u8 }  │
└──────────┴──────────────┴─────────┴──────────────────────────┘
```

**This carries the complete set of currently-held notes, not deltas.** That is a
deliberate choice: a lost note-off would leave a key lit forever, whereas a lost
full-state packet self-heals on the next one. The whole set fits in a datagram
even in the pathological case, so there is no reason to be cleverer.

The periodic resend exists for the same reason — if the last change was dropped,
the state corrects within 250 ms rather than never.

---

## 4. `CHART` payload

Sent once per song, on connect and after `SONG_CHANGED`.

```json
{
  "songId": "psalm-6-how-long",
  "title": "Psalm 6 — How Long",
  "durationSec": 214.6,
  "keySignature": { "tonic": 2, "mode": "major" },
  "tempoMap": [ { "tSec": 0.0, "bpm": 90.9, "beatsPerBar": 4 } ],
  "role": "keys",
  "notes": [ { "on": 12.34, "off": 12.9, "pitch": 62, "vel": 0.8 } ]
}
```

Notes are **sorted by `on`**, so the client can advance a cursor rather than
scanning. `vel` is normalised 0..1 to match `MelodicNote` on the desktop side.
`tonic` is a pitch class, 0 = C.

A four-minute piano piece is on the order of a few thousand notes — a few hundred
KB of JSON, sent once. Not worth a binary format until measurement says so.

---

## 5. Clock discipline and why the headset needs no calibration

The headset must render the song position **as it will be when the photons
arrive**, not as it was when the packet was parsed. Three quantities, all
measured or predicted, none configured by the user:

1. **`clockOffset` — measured.** From `PING`/`PONG`:

   ```
   rtt    = tClientRecv - tClientSend
   offset = tHostReply - (tClientSend + rtt/2)
   ```

   Keep the sample with the **lowest RTT** over a rolling window rather than
   averaging. On Wi-Fi the fast samples are the honest ones; averaging lets a
   single stalled packet drag the clock and it stays dragged.

2. **`predictedDisplayTime` — from OpenXR.** When the frame being rendered will
   actually reach the eye. This is the term that would otherwise become a
   user-facing latency slider.

3. **`audioOffsetSec` — from `WELCOME`.** Already calibrated on the host.

Per frame:

```
hostClockAtPhotons = predictedDisplayTime + clockOffset
songTimeToRender   = lastSongTime
                   + (hostClockAtPhotons - lastPositionHostClock)
                   + audioOffsetSec
```

While `flags.playing` is clear, the position is held rather than extrapolated.

**On link loss the client coasts** on its local clock and keeps rendering. It
must never freeze or jump: a brief Wi-Fi stall during a passage should be
invisible, and a longer one should degrade to "the notes kept moving" rather
than a stutter. Reconnection re-runs discovery from scratch.

---

## 6. Library browsing and voice search

The headset can browse the host's song library and ask for one to be loaded.
Song selection is the one place the headset drives the host rather than
rendering what it is told, so the rules are worth stating plainly.

**The host filters, not the headset.** A library is hundreds of songs, most of
which the headset will never show; shipping all of it so the client can filter
locally means a large payload on connect, a second copy of the matching rules,
and two implementations free to disagree about what "matches" means. The host
already holds the folder and already answers the same question for its own UI.

**Results are paged**, because the panel is. The headset asks for a page and a
size and gets exactly that, plus a total — enough to render "page 2 of 7"
without holding the rest.

**The facet lists come back with the results.** `artists` and `genres` are the
distinct values across the *whole* library, not just the returned page, so the
headset can offer filter chips without a second round-trip and without knowing
anything about what is in the library.

### `LIBRARY_REQUEST` — headset → host

```json
{ "search": "bach", "artist": null, "genre": "classical", "page": 0, "pageSize": 8 }
```

`search` matches title or artist, case-insensitively, on substring. `artist` and
`genre` are exact-match filters; `null` or absent means unfiltered. Paging is
zero-based.

### `LIBRARY` — host → headset

```json
{
  "page": 0,
  "pageSize": 8,
  "total": 43,
  "artists": ["Bach", "Mozart", "Vivaldi"],
  "genres": ["classical", "worship"],
  "items": [
    { "songId": "bwv-846-prelude", "title": "Prelude in C", "artist": "Bach",
      "genre": "classical", "durationSec": 132.4 }
  ]
}
```

`artist`, `genre` and `durationSec` may be `null` — a song is not required to
carry them, and a missing genre must render as "no genre", never as a guess.

### `SELECT_SONG` — headset → host

```json
{ "songId": "bwv-846-prelude" }
```

The host loads it and the existing `SONG_CHANGED` → `CHART` flow does the rest.
There is deliberately no reply: "the song changed" is already a fact the host
broadcasts, and inventing a second acknowledgement would give the headset two
sources of truth about what is loaded.

### `VOICE_QUERY` — headset → host

Raw bytes: a **RIFF WAV, mono, 16 kHz, 16-bit PCM**, no more than 10 seconds.

The format is pinned rather than negotiated. 16 kHz is what the recogniser wants,
so resampling on the headset costs one pass over a few seconds of audio and saves
sending three times the bytes; anything else means the host guessing at a
sample rate it was never told, which is exactly the class of boring failure this
document exists to prevent.

**Transcription happens on the host.** The headset has no recogniser, and the
library it is searching lives on the host anyway — voice search that worked
while unlinked would have nothing to search.

### `VOICE_RESULT` — host → headset

```json
{ "text": "bach prelude", "error": null }
```

`error` set (with `text` empty) means transcription failed — no recogniser
installed, audio unreadable. The headset shows the reason and leaves the typed
query alone rather than clearing it.

### Feature negotiation

`WELCOME` carries a `features` array naming the optional frames the host
implements:

```json
{ "host": "STUDIO-PC", "protocol": 1, "features": ["library", "voice"] }
```

New frame types do not break an old peer — both ends already ignore frame types
they do not recognise — but *silence* is indistinguishable from a request that
was dropped. A headset that asked an old host for a library would simply wait
forever. `features` turns that into something the client can see: no `library`,
no Songs menu. An absent `features` means an older host and no optional frames,
which keeps the protocol version at `1`.

---

## 7. Versioning

`protocol` is `1`. Adding an optional frame does **not** increment it — that
is what the `features` array in §6 is for. Any *incompatible* change does. Both ends reject a
mismatch at `HELLO`/`WELCOME` with a clear message rather than attempting a
partial parse — a protocol that half-works across versions is harder to diagnose
than one that refuses.
