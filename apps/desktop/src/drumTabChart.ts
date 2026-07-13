import { invoke } from "@tauri-apps/api/core";
import { selectDrumChart, type DrumChartSelection, type MidiTrackLike } from "./chartLoader";

type DrumTabHit = { t: number; p: string; v?: number };

const LANE_TO_GM_MIDI: Record<string, number> = {
  kick: 36,
  snare: 38,
  clap: 39,
  hihat_closed: 42,
  hihat_pedal: 44,
  hihat_open: 46,
  crash: 49,
  ride: 51,
  tom_high: 48,
  tom_mid: 45,
  tom_low: 41,
};

const FALLBACK_GM_MIDI = 38;

function laneToMidi(lane: string): number {
  return LANE_TO_GM_MIDI[lane] ?? FALLBACK_GM_MIDI;
}

export function drumChartFromTab(doc: unknown): DrumChartSelection | null {
  if (typeof doc !== "object" || doc === null) return null;
  const hits = (doc as { hits?: unknown }).hits;
  if (!Array.isArray(hits) || hits.length === 0) return null;

  const notes: MidiTrackLike["notes"] = [];
  for (const h of hits as DrumTabHit[]) {
    if (typeof h?.t !== "number" || !Number.isFinite(h.t)) continue;
    if (typeof h?.p !== "string" || h.p.length === 0) continue;
    notes.push({
      t: h.t,
      midi: laneToMidi(h.p),
      channel: 9,
      velocity: typeof h.v === "number" ? h.v : undefined,
    });
  }
  if (notes.length === 0) return null;
  notes.sort((a, b) => a.t - b.t);

  const track: MidiTrackLike = { index: 0, name: "Drums", notes };
  const selection = selectDrumChart([track]);
  if (selection.events.length === 0) return null;
  return { ...selection, reason: "drum_tab" };
}

export async function loadDrumChartFromTab(
  containerPath: string,
  relPath = "drum_tab.json",
): Promise<DrumChartSelection | null> {
  let raw: unknown;
  try {
    raw = await invoke<unknown>("read_auralsong_json", {
      containerPath,
      relPath,
    });
  } catch {
    return null;
  }
  return drumChartFromTab(raw);
}
