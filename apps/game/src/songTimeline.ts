/**
 * The song's timeline (feedpak song_timeline.json), read once per song into
 * what the play surface needs: beats with their downbeats marked, named
 * sections, and the tempo map.
 *
 * Pure. Every list is sorted by time and anything malformed is skipped, since
 * a timeline only decorates a song that plays without it.
 */

export type TimelineBeat = { t: number; down: boolean; m: number };
export type TimelineSection = { t: number; name: string };
export type TimelineTempo = { t: number; bpm: number };

export type SongTimeline = {
  beats: TimelineBeat[];
  sections: TimelineSection[];
  tempos: TimelineTempo[];
};

function rows(raw: unknown, key: string): Record<string, unknown>[] {
  if (typeof raw !== "object" || raw === null) return [];
  const list = (raw as Record<string, unknown>)[key];
  return Array.isArray(list)
    ? list.filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null)
    : [];
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function readSongTimeline(raw: unknown): SongTimeline {
  // A beat is a downbeat when its bar number changes. Packs that mark only
  // downbeats carry -1 on the beats between, which keeps the bar before.
  const beats: TimelineBeat[] = [];
  const beatRows = rows(raw, "beats")
    .map((b) => ({ t: finite(b.time), measure: b.measure }))
    .filter((b): b is { t: number; measure: unknown } => b.t !== null)
    .sort((a, b) => a.t - b.t);
  let bar: number | null = null;
  for (const b of beatRows) {
    const m = Number.isInteger(b.measure) && b.measure !== -1 ? (b.measure as number) : null;
    const down = m !== null && m !== bar;
    if (m !== null) bar = m;
    beats.push({ t: b.t, down, m: bar ?? 0 });
  }

  const sections = rows(raw, "sections")
    .map((s) => {
      const t = finite(s.time);
      const name = String(s.name ?? "section").replace(/[_-]+/g, " ").trim();
      return t === null ? null : { t, name: name.charAt(0).toUpperCase() + name.slice(1) };
    })
    .filter((s): s is TimelineSection => s !== null)
    .sort((a, b) => a.t - b.t);

  const tempos = rows(raw, "tempos")
    .map((x) => ({ t: finite(x.time), bpm: finite(x.bpm) }))
    .filter((x): x is TimelineTempo => x.t !== null && x.bpm !== null && x.bpm > 0)
    .sort((a, b) => a.t - b.t);

  return { beats, sections, tempos };
}

/**
 * The song's tempo at `t`: the last tempo change at or before it, or the first
 * one before the song reaches it. Null when the song has no tempo map.
 */
export function tempoAt(tempos: ReadonlyArray<TimelineTempo>, t: number): number | null {
  if (tempos.length === 0) return null;
  let lo = 0;
  let hi = tempos.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tempos[mid].t <= t) lo = mid + 1;
    else hi = mid;
  }
  return tempos[Math.max(0, lo - 1)].bpm;
}
