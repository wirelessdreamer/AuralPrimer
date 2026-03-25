export type DrumLane = "BD" | "SD" | "HH" | "CY" | "RD" | "HT" | "LT" | "FT";

export type MidiNoteLike = {
  t: number;
  midi: number;
  channel?: number;
};

export type MidiTrackLike = {
  index: number;
  name?: string;
  notes: MidiNoteLike[];
};

type TempoEvent = {
  tick: number;
  usPerQuarter: number;
  order: number;
};

type ParsedMidiNote = {
  tick: number;
  midi: number;
  channel?: number;
};

type ParsedMidiTrack = {
  index: number;
  name?: string;
  notes: ParsedMidiNote[];
};

type StrictSource = "named" | "channel9";

type DrumEvent = {
  t: number;
  midi: number;
  lane: DrumLane;
  trackIndex: number;
  trackName?: string;
  strictSource?: StrictSource;
};

export type DrumChartSelection = {
  mode: "strict" | "relaxed";
  reason: "strict_empty" | "strict_preferred" | "relaxed_richer" | "dedicated_drum_track_guard";
  events: DrumEvent[];
  strictCount: number;
  relaxedCount: number;
  strictUniqueLanes: DrumLane[];
  relaxedUniqueLanes: DrumLane[];
};

const DRUM_TRACK_NAME_RE = /\b(drum|kit|percussion)\b/i;

function mapMidiToLane(midi: number): DrumLane | null {
  if (midi === 35 || midi === 36) return "BD";
  if (midi === 37 || midi === 38 || midi === 39 || midi === 40) return "SD";
  if (midi === 42 || midi === 44 || midi === 46) return "HH";
  if (midi === 49 || midi === 52 || midi === 55 || midi === 57) return "CY";
  if (midi === 51 || midi === 53 || midi === 59) return "RD";
  if (midi === 48 || midi === 50) return "HT";
  if (midi === 45 || midi === 47) return "LT";
  if (midi === 41 || midi === 43) return "FT";
  return null;
}

function isDrumNamedTrack(trackName?: string): boolean {
  return Boolean(trackName && DRUM_TRACK_NAME_RE.test(trackName));
}

function collectStrictEvents(tracks: MidiTrackLike[]): DrumEvent[] {
  const out: DrumEvent[] = [];
  for (const track of tracks) {
    const named = isDrumNamedTrack(track.name);
    for (const note of track.notes) {
      const lane = mapMidiToLane(note.midi);
      if (!lane) continue;

      const fromChannel9 = note.channel === 9;
      const strictSource: StrictSource | undefined = named ? "named" : fromChannel9 ? "channel9" : undefined;
      if (!strictSource) continue;

      out.push({
        t: note.t,
        midi: note.midi,
        lane,
        trackIndex: track.index,
        trackName: track.name,
        strictSource
      });
    }
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

function collectRelaxedEvents(tracks: MidiTrackLike[]): DrumEvent[] {
  const out: DrumEvent[] = [];
  for (const track of tracks) {
    for (const note of track.notes) {
      const lane = mapMidiToLane(note.midi);
      if (!lane) continue;
      out.push({
        t: note.t,
        midi: note.midi,
        lane,
        trackIndex: track.index,
        trackName: track.name
      });
    }
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

function uniqueLanes(events: DrumEvent[]): DrumLane[] {
  return Array.from(new Set(events.map((e) => e.lane)));
}

function readU32BE(bytes: Uint8Array, off: number): number {
  return (
    (bytes[off] << 24) |
    (bytes[off + 1] << 16) |
    (bytes[off + 2] << 8) |
    bytes[off + 3]
  ) >>> 0;
}

function readU16BE(bytes: Uint8Array, off: number): number {
  return ((bytes[off] << 8) | bytes[off + 1]) >>> 0;
}

function readVarLen(bytes: Uint8Array, start: number): { value: number; next: number } {
  let value = 0;
  let off = start;
  for (let i = 0; i < 4 && off < bytes.length; i += 1) {
    const b = bytes[off];
    off += 1;
    value = (value << 7) | (b & 0x7f);
    if ((b & 0x80) === 0) break;
  }
  return { value, next: off };
}

function channelDataLen(status: number): number {
  const high = status & 0xf0;
  if (high === 0xc0 || high === 0xd0) return 1;
  return 2;
}

function decodeText(bytes: Uint8Array): string {
  return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
}

function ppqnTicksToSeconds(ticks: number, usPerQuarter: number, ppqn: number): number {
  if (!Number.isFinite(ticks) || !Number.isFinite(usPerQuarter) || !Number.isFinite(ppqn) || ppqn <= 0) {
    return 0;
  }
  return (ticks * usPerQuarter) / (ppqn * 1_000_000);
}

function buildTickToSecondsConverter(division: number, tempoEvents: TempoEvent[]): (tick: number) => number {
  if ((division & 0x8000) !== 0) {
    const rawFps = ((division >>> 8) & 0xff) - 256;
    const ticksPerFrame = division & 0xff;
    const fpsAbs = Math.abs(rawFps);
    const fps = fpsAbs === 29 ? 29.97 : fpsAbs;
    const secPerTick = fps > 0 && ticksPerFrame > 0 ? 1 / (fps * ticksPerFrame) : 0;
    return (tick) => Math.max(0, tick) * secPerTick;
  }

  const ppqn = division & 0x7fff;
  const ordered = [...tempoEvents].sort((a, b) => a.tick - b.tick || a.order - b.order);
  const segments: Array<{ tick: number; sec: number; usPerQuarter: number }> = [
    { tick: 0, sec: 0, usPerQuarter: 500_000 }
  ];

  let currentTick = 0;
  let currentSec = 0;
  let currentTempo = 500_000;

  for (const event of ordered) {
    if (!Number.isFinite(event.tick) || !Number.isFinite(event.usPerQuarter) || event.usPerQuarter <= 0) continue;

    if (event.tick > currentTick) {
      currentSec += ppqnTicksToSeconds(event.tick - currentTick, currentTempo, ppqn);
      currentTick = event.tick;
      currentTempo = event.usPerQuarter;
      segments.push({ tick: currentTick, sec: currentSec, usPerQuarter: currentTempo });
      continue;
    }

    currentTempo = event.usPerQuarter;
    segments[segments.length - 1] = {
      tick: currentTick,
      sec: currentSec,
      usPerQuarter: currentTempo
    };
  }

  return (tick) => {
    const targetTick = Math.max(0, tick);
    let lo = 0;
    let hi = segments.length - 1;

    while (lo < hi) {
      const mid = Math.floor((lo + hi + 1) / 2);
      if (segments[mid].tick <= targetTick) {
        lo = mid;
      } else {
        hi = mid - 1;
      }
    }

    const seg = segments[lo];
    return seg.sec + ppqnTicksToSeconds(targetTick - seg.tick, seg.usPerQuarter, ppqn);
  };
}

export function parseMidiTracksFromBytes(bytes: Uint8Array): MidiTrackLike[] {
  if (bytes.length < 14) return [];
  if (
    bytes[0] !== 0x4d || // M
    bytes[1] !== 0x54 || // T
    bytes[2] !== 0x68 || // h
    bytes[3] !== 0x64 // d
  ) {
    return [];
  }

  const headerLen = readU32BE(bytes, 4);
  if (headerLen < 6 || 8 + headerLen > bytes.length) return [];
  const trackCount = readU16BE(bytes, 10);
  const division = readU16BE(bytes, 12);

  let off = 8 + headerLen;
  const tracks: ParsedMidiTrack[] = [];
  const tempoEvents: TempoEvent[] = [];

  for (let ti = 0; ti < trackCount; ti += 1) {
    if (off + 8 > bytes.length) break;
    if (
      bytes[off] !== 0x4d || // M
      bytes[off + 1] !== 0x54 || // T
      bytes[off + 2] !== 0x72 || // r
      bytes[off + 3] !== 0x6b // k
    ) {
      break;
    }

    const trkLen = readU32BE(bytes, off + 4);
    off += 8;
    const end = Math.min(bytes.length, off + trkLen);

    const notes: ParsedMidiNote[] = [];
    let name = "";
    let absTicks = 0;
    let runningStatus = 0;

    while (off < end) {
      const dv = readVarLen(bytes, off);
      absTicks += dv.value;
      off = dv.next;
      if (off >= end) break;

      let status = bytes[off];
      if (status < 0x80) {
        // Running status.
        if (runningStatus < 0x80) break;
        status = runningStatus;
      } else {
        off += 1;
        runningStatus = status;
      }

      if (status === 0xff) {
        if (off >= end) break;
        const metaType = bytes[off];
        off += 1;
        const lv = readVarLen(bytes, off);
        off = lv.next;
        if (off + lv.value > end) break;
        const data = bytes.slice(off, off + lv.value);
        if (metaType === 0x03 && !name) {
          name = decodeText(data);
        } else if (metaType === 0x51 && data.length === 3) {
          tempoEvents.push({
            tick: absTicks,
            usPerQuarter: (data[0] << 16) | (data[1] << 8) | data[2],
            order: tempoEvents.length
          });
        }
        off += lv.value;
        if (metaType === 0x2f) break;
        continue;
      }

      if (status === 0xf0 || status === 0xf7) {
        const lv = readVarLen(bytes, off);
        off = Math.min(end, lv.next + lv.value);
        continue;
      }

      const dataLen = channelDataLen(status);
      if (off + dataLen > end) break;
      const d1 = bytes[off];
      const d2 = dataLen > 1 ? bytes[off + 1] : 0;
      off += dataLen;

      const channel = status & 0x0f;
      const msg = status & 0xf0;
      if (msg === 0x90 && d2 > 0) {
        notes.push({
          tick: absTicks,
          midi: d1,
          channel
        });
      }
    }

    tracks.push({
      index: ti,
      name,
      notes
    });

    off = end;
  }

  const tickToSeconds = buildTickToSecondsConverter(division, tempoEvents);
  return tracks.map((track) => ({
    index: track.index,
    name: track.name,
    notes: track.notes.map((note) => ({
      t: tickToSeconds(note.tick),
      midi: note.midi,
      channel: note.channel
    }))
  }));
}

export function selectDrumChartFromMidiBytes(bytes: Uint8Array): DrumChartSelection {
  const tracks = parseMidiTracksFromBytes(bytes);
  return selectDrumChart(tracks);
}

export function selectDrumChart(tracks: MidiTrackLike[]): DrumChartSelection {
  const strictEvents = collectStrictEvents(tracks);
  const relaxedEvents = collectRelaxedEvents(tracks);
  const strictLanes = uniqueLanes(strictEvents);
  const relaxedLanes = uniqueLanes(relaxedEvents);

  if (strictEvents.length === 0) {
    return {
      mode: "relaxed",
      reason: "strict_empty",
      events: relaxedEvents,
      strictCount: 0,
      relaxedCount: relaxedEvents.length,
      strictUniqueLanes: strictLanes,
      relaxedUniqueLanes: relaxedLanes
    };
  }

  const strictHasNamedDrumTrack = strictEvents.some((e) => e.strictSource === "named");
  if (strictHasNamedDrumTrack) {
    return {
      mode: "strict",
      reason: "dedicated_drum_track_guard",
      events: strictEvents,
      strictCount: strictEvents.length,
      relaxedCount: relaxedEvents.length,
      strictUniqueLanes: strictLanes,
      relaxedUniqueLanes: relaxedLanes
    };
  }

  const relaxedRicher =
    relaxedEvents.length >= Math.ceil(strictEvents.length * 1.4) &&
    relaxedLanes.length > strictLanes.length;

  if (relaxedRicher) {
    return {
      mode: "relaxed",
      reason: "relaxed_richer",
      events: relaxedEvents,
      strictCount: strictEvents.length,
      relaxedCount: relaxedEvents.length,
      strictUniqueLanes: strictLanes,
      relaxedUniqueLanes: relaxedLanes
    };
  }

  return {
    mode: "strict",
    reason: "strict_preferred",
    events: strictEvents,
    strictCount: strictEvents.length,
    relaxedCount: relaxedEvents.length,
    strictUniqueLanes: strictLanes,
    relaxedUniqueLanes: relaxedLanes
  };
}

// ─── Melodic track extraction ─────────────────────────────────────────────────

export type InstrumentRole = "bass" | "rhythm_guitar" | "lead_guitar" | "keys" | "melodic";

export type MelodicNote = {
  t_on: number;
  t_off: number;
  pitch: number;
  velocity: number;
};

export type MelodicTrackSelection = {
  role: InstrumentRole;
  trackName: string;
  channel: number;
  notes: MelodicNote[];
};

/** Channel → instrument role mapping matching the ingest pipeline. */
const CHANNEL_TO_ROLE: Record<number, InstrumentRole> = {
  0: "bass",
  1: "rhythm_guitar",
  2: "lead_guitar",
  3: "keys",
  4: "melodic",
};

const MELODIC_TRACK_NAME_RE: Record<InstrumentRole, RegExp> = {
  bass: /\bbass\b/i,
  rhythm_guitar: /\brhythm\s*guitar\b/i,
  lead_guitar: /\blead\s*guitar\b/i,
  keys: /\bkeys?\b|\bsynth\b|\bpiano\b/i,
  melodic: /\bmelodic\b/i,
};

function inferRoleFromTrackName(name: string): InstrumentRole | null {
  for (const [role, re] of Object.entries(MELODIC_TRACK_NAME_RE) as Array<[InstrumentRole, RegExp]>) {
    if (re.test(name)) return role;
  }
  return null;
}

/**
 * Extract per-instrument melodic tracks from parsed MIDI data.
 *
 * The function uses two strategies:
 * 1) **Track name matching** — if a track is named "Bass", "Lead Guitar", etc.
 * 2) **MIDI channel mapping** — channels 0–4 map to specific instruments.
 *
 * Drum tracks (channel 9, or named "Drums") and the structure/conductor tracks are excluded.
 */
export function selectMelodicTracks(tracks: MidiTrackLike[]): MelodicTrackSelection[] {
  const out: MelodicTrackSelection[] = [];
  const seenRoles = new Set<InstrumentRole>();

  for (const track of tracks) {
    // Skip explicitly drum-identified tracks.
    if (isDrumNamedTrack(track.name)) continue;
    if (track.name === "Conductor" || track.name === "Structure") continue;

    // Determine the role.
    let role: InstrumentRole | null = null;
    let channel = -1;

    // Try track name first.
    if (track.name) {
      role = inferRoleFromTrackName(track.name);
    }

    // Fall back to channel-based inference from first note.
    if (!role && track.notes.length > 0) {
      const ch = track.notes[0].channel;
      if (ch !== undefined && ch !== 9 && ch !== 15) {
        role = CHANNEL_TO_ROLE[ch] ?? null;
        channel = ch;
      }
    }

    if (!role) continue;
    if (seenRoles.has(role)) continue;
    seenRoles.add(role);

    // Collect note-on pairs.  For now, we just use note-on times + a minimum duration.
    const melodicNotes: MelodicNote[] = track.notes
      .filter((n) => n.channel !== 9 && n.channel !== 15)
      .map((n) => ({
        t_on: n.t,
        t_off: n.t + 0.15, // minimum visible duration
        pitch: n.midi,
        velocity: 100,
      }));

    if (melodicNotes.length === 0) continue;

    out.push({
      role,
      trackName: track.name ?? role,
      channel: channel >= 0 ? channel : (track.notes[0]?.channel ?? -1),
      notes: melodicNotes,
    });
  }

  // Sort: bass, rhythm_guitar, lead_guitar, keys, melodic.
  const order: InstrumentRole[] = ["bass", "rhythm_guitar", "lead_guitar", "keys", "melodic"];
  out.sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role));
  return out;
}

/** Convenience: parse MIDI bytes and extract melodic tracks in one call. */
export function selectMelodicTracksFromMidiBytes(bytes: Uint8Array): MelodicTrackSelection[] {
  const tracks = parseMidiTracksFromBytes(bytes);
  return selectMelodicTracks(tracks);
}
