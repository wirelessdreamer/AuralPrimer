/**
 * Refine workspace audition engine — plays a candidate's notes back
 * through Web Audio so the user can HEAR what each candidate sounds
 * like before they accept it.
 *
 * v1 design choice: synthesise the notes with simple oscillators +
 * AD envelopes rather than bundling a SoundFont. Rationale:
 *   - No 5-10 MB asset to ship in the portable
 *   - No parser to write / debug
 *   - The audition's job is "is this note real" not "does it sound
 *     beautiful" -- a sine + saw blend with quick attack/release is
 *     legible enough to make a-vs-b judgments
 * SoundFont upgrade path: swap the per-note voice in
 * `scheduleNoteVoice` for a sample-player and keep the scheduler.
 *
 * Loop scheduler: notes are scheduled in a small look-ahead window
 * (250 ms) every frame so a long region can loop indefinitely without
 * the underlying audio context's clock drifting from the UI's wall
 * clock. The scheduler stops itself when the engine is stopped or
 * when no region is focused.
 */

import type { RefinementNote } from "./refineCandidatesIo";

const ROOT_GAIN = 0.5;
const VOICE_ATTACK_SEC = 0.005;
const VOICE_RELEASE_SEC = 0.08;
const LOOP_GAP_SEC = 0.2;
const LOOK_AHEAD_SEC = 0.25;
const SCHEDULE_INTERVAL_MS = 50;

/** Lazy AudioContext factory -- browsers refuse to create one until a user gesture. */
function makeAudioContext(): AudioContext | null {
  const Ctor = (window.AudioContext || (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
  if (!Ctor) return null;
  try {
    return new Ctor();
  } catch {
    return null;
  }
}

function midiPitchToFreqHz(midi: number): number {
  return 440 * Math.pow(2, (midi - 69) / 12);
}

/** Pure helper -- the scheduler uses this to decide when the next loop iteration starts. */
export function nextLoopStartTime(
  currentAudioTime: number,
  loopStartedAt: number,
  loopDurationSec: number,
): number {
  if (loopDurationSec <= 0) return currentAudioTime + LOOP_GAP_SEC;
  const elapsed = currentAudioTime - loopStartedAt;
  const cycles = Math.floor(elapsed / (loopDurationSec + LOOP_GAP_SEC));
  return loopStartedAt + (cycles + 1) * (loopDurationSec + LOOP_GAP_SEC);
}

export type AuditionPlayingState = {
  /** Source audio-context time when the CURRENT loop iteration started. */
  loopStartedAt: number;
  /** Region t_start in seconds — origin for scheduling. */
  regionStart: number;
  /** Region t_end in seconds. */
  regionEnd: number;
  /** Notes scheduled for THIS loop iteration. */
  notes: RefinementNote[];
};

export type RefineAuditionHandle = {
  /** Begin / continue looping the given region. Idempotent. */
  playRegion: (notes: RefinementNote[], regionStart: number, regionEnd: number) => void;
  /** Stop playback immediately. */
  stop: () => void;
  /**
   * Update the notes for the currently-playing region (e.g. user
   * switched candidate). Keeps the loop running; next iteration uses
   * the new notes.
   */
  updateNotes: (notes: RefinementNote[]) => void;
  /** Is the engine currently playing? */
  isPlaying: () => boolean;
};

export function initRefineAudition(): RefineAuditionHandle {
  let ctx: AudioContext | null = null;
  let masterGain: GainNode | null = null;
  let playing: AuditionPlayingState | null = null;
  let scheduledLoopEndTime = 0;
  let timerId: number | null = null;

  function ensureContext(): boolean {
    if (ctx) return true;
    ctx = makeAudioContext();
    if (!ctx) return false;
    masterGain = ctx.createGain();
    masterGain.gain.value = ROOT_GAIN;
    masterGain.connect(ctx.destination);
    return true;
  }

  /**
   * Schedule one note as a brief two-oscillator voice with an AD
   * envelope. The voice auto-disconnects when its envelope finishes so
   * we don't accumulate dead nodes across loop iterations.
   */
  function scheduleNoteVoice(note: RefinementNote, startAt: number): void {
    if (!ctx || !masterGain) return;
    const freq = midiPitchToFreqHz(note.pitch);
    const dur = Math.max(0.05, note.t_off - note.t_on);
    const stopAt = startAt + dur;

    // Two oscillators (sine + slightly-detuned saw) give a hint of
    // body without being overbearing. The saw is mixed in low so it
    // doesn't dominate the perceived pitch.
    const osc1 = ctx.createOscillator();
    osc1.type = "sine";
    osc1.frequency.value = freq;

    const osc2 = ctx.createOscillator();
    osc2.type = "sawtooth";
    osc2.frequency.value = freq;
    osc2.detune.value = -7;

    const sawGain = ctx.createGain();
    sawGain.gain.value = 0.15;

    const voiceGain = ctx.createGain();
    const peak = Math.max(0.05, Math.min(1, note.velocity / 127));
    voiceGain.gain.setValueAtTime(0, startAt);
    voiceGain.gain.linearRampToValueAtTime(peak, startAt + VOICE_ATTACK_SEC);
    voiceGain.gain.setValueAtTime(peak, Math.max(stopAt - VOICE_RELEASE_SEC, startAt + VOICE_ATTACK_SEC));
    voiceGain.gain.linearRampToValueAtTime(0, stopAt + VOICE_RELEASE_SEC);

    osc1.connect(voiceGain);
    osc2.connect(sawGain).connect(voiceGain);
    voiceGain.connect(masterGain);

    osc1.start(startAt);
    osc2.start(startAt);
    osc1.stop(stopAt + VOICE_RELEASE_SEC + 0.02);
    osc2.stop(stopAt + VOICE_RELEASE_SEC + 0.02);

    // Best-effort cleanup -- the GC will collect via setTimeout if the
    // disconnect never fires. Web Audio nodes are cheap; this keeps the
    // graph from holding refs to thousands of finished voices over a
    // long session.
    osc1.onended = () => {
      try { osc1.disconnect(); } catch { /* ignore */ }
      try { osc2.disconnect(); } catch { /* ignore */ }
      try { sawGain.disconnect(); } catch { /* ignore */ }
      try { voiceGain.disconnect(); } catch { /* ignore */ }
    };
  }

  /**
   * Schedule one full pass of the region's notes starting at audio
   * time `loopStart`. Returns the time the loop ends (= loopStart +
   * region duration).
   */
  function scheduleOneLoop(state: AuditionPlayingState, loopStart: number): number {
    if (!ctx) return loopStart;
    const regionDuration = Math.max(0, state.regionEnd - state.regionStart);
    for (const note of state.notes) {
      // Notes whose onset is INSIDE the region get shifted into the
      // current loop iteration. Out-of-region notes (the chartLoader
      // already gives us in-region notes only for the active region,
      // but be defensive) get skipped.
      if (note.t_on < state.regionStart || note.t_on >= state.regionEnd) continue;
      const offsetSec = note.t_on - state.regionStart;
      scheduleNoteVoice(note, loopStart + offsetSec);
    }
    return loopStart + regionDuration;
  }

  function tickScheduler(): void {
    if (!ctx || !playing) return;
    const now = ctx.currentTime;
    while (scheduledLoopEndTime - now < LOOK_AHEAD_SEC) {
      const next = nextLoopStartTime(
        scheduledLoopEndTime,
        playing.loopStartedAt,
        playing.regionEnd - playing.regionStart,
      );
      const end = scheduleOneLoop(playing, next);
      scheduledLoopEndTime = end;
    }
  }

  function startTimer(): void {
    if (timerId !== null) return;
    timerId = window.setInterval(() => tickScheduler(), SCHEDULE_INTERVAL_MS);
  }

  function stopTimer(): void {
    if (timerId !== null) {
      clearInterval(timerId);
      timerId = null;
    }
  }

  function playRegion(notes: RefinementNote[], regionStart: number, regionEnd: number): void {
    if (!ensureContext() || !ctx) return;
    // Browsers suspend the context until a user gesture -- resume here
    // because playRegion is always called from a click / keyboard event.
    if (ctx.state === "suspended") {
      void ctx.resume();
    }
    const now = ctx.currentTime;
    const startAt = now + 0.05; // small lead so the first note's attack envelope renders cleanly
    playing = {
      loopStartedAt: startAt,
      regionStart,
      regionEnd,
      notes: [...notes],
    };
    scheduledLoopEndTime = startAt;
    tickScheduler();
    startTimer();
  }

  function stop(): void {
    stopTimer();
    playing = null;
    if (masterGain && ctx) {
      // Quick ramp to silence; un-ramp anything still ringing.
      const now = ctx.currentTime;
      masterGain.gain.cancelScheduledValues(now);
      masterGain.gain.setValueAtTime(masterGain.gain.value, now);
      masterGain.gain.linearRampToValueAtTime(0, now + 0.05);
      masterGain.gain.linearRampToValueAtTime(ROOT_GAIN, now + 0.1);
    }
  }

  function updateNotes(notes: RefinementNote[]): void {
    if (!playing) return;
    playing.notes = [...notes];
  }

  return {
    playRegion,
    stop,
    updateNotes,
    isPlaying: () => playing !== null,
  };
}
