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
import type { MidiActiveNote } from "@auralprimer/midi-input";

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
  playRegion: (notes: RefinementNote[], regionStart: number, regionEnd: number,
    transport?: { getTimeSec: () => number; playbackRate: number }) => void;
  /** Stop playback immediately. */
  stop: () => void;
  /**
   * Update the notes for the currently-playing region (e.g. user
   * switched candidate). Keeps the loop running; next iteration uses
   * the new notes.
   */
  updateNotes: (notes: RefinementNote[]) => void;
  /**
   * Play a single note immediately — used to audition a note as the user
   * places or edits it in the spectrogram editor. Independent of the loop.
   */
  playNote: (pitch: number, velocity?: number, durationSec?: number) => void;
  /** Live keyboard voices follow key/pedal releases independently of transport. */
  setLiveNotes: (notes: readonly MidiActiveNote[]) => void;
  setLiveGain: (gain: number) => void;
  connectSource: (source: HTMLMediaElement) => void;
  resume: () => Promise<void>;
  setOutput: (output: { sourceGain: number; renderedGain: number; sourcePan: number; renderedPan: number; renderedEnabled: boolean }) => void;
  getOutputLatencySec: () => number;
  /** Is the engine currently playing? */
  isPlaying: () => boolean;
};

export function initRefineAudition(): RefineAuditionHandle {
  let ctx: AudioContext | null = null;
  let masterGain: GainNode | null = null;
  let previewGain: GainNode | null = null;
  let sourceGain: GainNode | null = null;
  let sourcePan: StereoPannerNode | null = null;
  let renderedPan: StereoPannerNode | null = null;
  let liveGain: GainNode | null = null;
  let liveLevel = 0.7;
  const liveVoices = new Map<string, { startedAtUs: number; release: () => void }>();
  const sources = new WeakSet<HTMLMediaElement>();
  const voices = new Set<OscillatorNode>();
  let output = { sourceGain: 0.8, renderedGain: 0.7, sourcePan: 0, renderedPan: 0, renderedEnabled: true };
  let transport: { getTimeSec: () => number; playbackRate: number } | null = null;
  let scheduledThrough = 0;
  let lastTransportTime = 0;
  let includeActiveNotes = false;
  let playing: AuditionPlayingState | null = null;
  let scheduledLoopEndTime = 0;
  let timerId: number | null = null;

  function ensureContext(): boolean {
    if (ctx) return true;
    ctx = makeAudioContext();
    if (!ctx) return false;
    masterGain = ctx.createGain();
    previewGain = ctx.createGain();
    sourceGain = ctx.createGain();
    liveGain = ctx.createGain();
    sourcePan = ctx.createStereoPanner();
    renderedPan = ctx.createStereoPanner();
    masterGain.connect(renderedPan);
    previewGain.connect(renderedPan);
    renderedPan.connect(ctx.destination);
    sourceGain.connect(sourcePan).connect(ctx.destination);
    // The played keyboard is centered, with its own fader; the A/B routing
    // and transport stop only affect the recording and rendered chart.
    liveGain.gain.value = ROOT_GAIN * 0.5 * liveLevel;
    liveGain.connect(ctx.destination);
    applyOutput();
    return true;
  }

  function applyOutput(): void {
    if (!ctx || !masterGain || !previewGain || !sourceGain || !sourcePan || !renderedPan) return;
    sourceGain.gain.value = output.sourceGain;
    // Fold both original stereo channels down before routing into one ear.
    sourceGain.channelCount = output.sourcePan === 0 ? 2 : 1;
    sourceGain.channelCountMode = output.sourcePan === 0 ? "max" : "explicit";
    sourcePan.pan.value = output.sourcePan;
    renderedPan.pan.value = output.renderedPan;
    masterGain.gain.value = ROOT_GAIN * output.renderedGain * Number(output.renderedEnabled);
    // Explicit note preview remains audible even while monitoring source only.
    previewGain.gain.value = ROOT_GAIN * output.renderedGain;
  }

  function silenceVoices(): void {
    for (const oscillator of voices) { try { oscillator.stop(); } catch { /* already ended */ } }
    voices.clear();
  }

  /**
   * Schedule one note as a brief two-oscillator voice with an AD
   * envelope. The voice auto-disconnects when its envelope finishes so
   * we don't accumulate dead nodes across loop iterations.
   */
  function scheduleNoteVoice(note: RefinementNote, startAt: number, preview = false): void {
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
    voiceGain.connect(preview && previewGain ? previewGain : masterGain);
    voices.add(osc1);
    voices.add(osc2);

    osc1.start(startAt);
    osc2.start(startAt);
    osc1.stop(stopAt + VOICE_RELEASE_SEC + 0.02);
    osc2.stop(stopAt + VOICE_RELEASE_SEC + 0.02);

    // Best-effort cleanup -- the GC will collect via setTimeout if the
    // disconnect never fires. Web Audio nodes are cheap; this keeps the
    // graph from holding refs to thousands of finished voices over a
    // long session.
    osc1.onended = () => {
      voices.delete(osc1);
      voices.delete(osc2);
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
    if (transport) {
      const songTime = transport.getTimeSec();
      const rate = transport.playbackRate;
      if (songTime < lastTransportTime - 0.05 || songTime > scheduledThrough + LOOK_AHEAD_SEC * rate) {
        silenceVoices();
        scheduledThrough = songTime;
        includeActiveNotes = true;
      }
      const through = Math.min(playing.regionEnd, songTime + LOOK_AHEAD_SEC * rate);
      for (const note of playing.notes) {
        const active = includeActiveNotes && note.t_on < songTime && note.t_off > songTime;
        if (!active && (note.t_on < scheduledThrough || note.t_on >= through)) continue;
        const remaining = Math.min(note.t_off, playing.regionEnd) - Math.max(note.t_on, songTime);
        if (remaining <= 0) continue;
        scheduleNoteVoice({ ...note, t_off: note.t_on + remaining / rate },
          ctx.currentTime + Math.max(0, (note.t_on - songTime) / rate));
      }
      includeActiveNotes = false;
      scheduledThrough = through;
      lastTransportTime = songTime;
      return;
    }
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

  function playRegion(notes: RefinementNote[], regionStart: number, regionEnd: number,
    clock?: { getTimeSec: () => number; playbackRate: number }): void {
    stop();
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
    transport = clock ?? null;
    scheduledThrough = regionStart;
    lastTransportTime = regionStart;
    includeActiveNotes = true;
    scheduledLoopEndTime = startAt;
    tickScheduler();
    startTimer();
  }

  function stop(): void {
    stopTimer();
    playing = null;
    transport = null;
    silenceVoices();
  }

  function updateNotes(notes: RefinementNote[]): void {
    if (!playing) return;
    playing.notes = [...notes];
    if (transport) {
      silenceVoices();
      scheduledThrough = transport.getTimeSec();
      includeActiveNotes = true;
      tickScheduler();
    }
  }

  function playNote(pitch: number, velocity = 100, durationSec = 0.32): void {
    if (!ensureContext() || !ctx) return;
    // Always called from a click / pointer gesture, so resume is allowed.
    if (ctx.state === "suspended") {
      void ctx.resume();
    }
    const dur = Math.max(0.1, Math.min(1.5, durationSec));
    scheduleNoteVoice(
      { t_on: 0, t_off: dur, pitch, velocity, instrument: "melodic" } as RefinementNote,
      ctx.currentTime + 0.01,
      true,
    );
  }

  function setLiveNotes(notes: readonly MidiActiveNote[]): void {
    const wanted = new Map(notes.map((note) => [`${note.channel}:${note.pitch}`, note]));
    for (const [key, voice] of liveVoices) {
      if (wanted.get(key)?.startedAtUs === voice.startedAtUs) continue;
      voice.release();
      liveVoices.delete(key);
    }
    if (!notes.length || !ensureContext() || !ctx || !liveGain) return;
    for (const [key, note] of wanted) {
      if (liveVoices.has(key)) continue;
      const audio = ctx;
      const oscillators = [audio.createOscillator(), audio.createOscillator()];
      const body = audio.createGain();
      const envelope = audio.createGain();
      body.gain.value = 0.15;
      oscillators[0].type = "sine";
      oscillators[1].type = "sawtooth";
      oscillators[1].detune.value = -7;
      for (const oscillator of oscillators) oscillator.frequency.value = midiPitchToFreqHz(note.pitch);
      oscillators[0].connect(envelope);
      oscillators[1].connect(body).connect(envelope);
      envelope.connect(liveGain);
      const now = audio.currentTime;
      envelope.gain.setValueAtTime(0, now);
      envelope.gain.linearRampToValueAtTime(note.velocity / 127, now + VOICE_ATTACK_SEC);
      for (const oscillator of oscillators) oscillator.start(now);
      oscillators[0].onended = () => {
        for (const oscillator of oscillators) oscillator.disconnect();
        body.disconnect();
        envelope.disconnect();
      };
      liveVoices.set(key, { startedAtUs: note.startedAtUs, release: () => {
        const at = audio.currentTime;
        envelope.gain.cancelScheduledValues(at);
        envelope.gain.setValueAtTime(envelope.gain.value, at);
        envelope.gain.linearRampToValueAtTime(0, at + VOICE_RELEASE_SEC);
        for (const oscillator of oscillators) oscillator.stop(at + VOICE_RELEASE_SEC + 0.02);
      } });
    }
  }

  return {
    playRegion,
    stop,
    updateNotes,
    playNote,
    setLiveNotes,
    setLiveGain: (gain) => {
      liveLevel = Math.max(0, Math.min(1, gain));
      if (liveGain) liveGain.gain.value = ROOT_GAIN * 0.5 * liveLevel;
    },
    connectSource: (source) => {
      if (!ensureContext() || !ctx || !sourceGain) throw new Error("Audio output unavailable");
      if (!sources.has(source)) {
        ctx.createMediaElementSource(source).connect(sourceGain);
        sources.add(source);
      }
    },
    resume: async () => {
      if (!ensureContext() || !ctx) throw new Error("Audio output unavailable");
      if (ctx.state === "suspended") await ctx.resume();
    },
    setOutput: (settings) => {
      output = { ...settings,
        sourceGain: Math.max(0, Math.min(1, settings.sourceGain)),
        renderedGain: Math.max(0, Math.min(1, settings.renderedGain)),
        sourcePan: Math.max(-1, Math.min(1, settings.sourcePan)),
        renderedPan: Math.max(-1, Math.min(1, settings.renderedPan)),
      };
      applyOutput();
    },
    getOutputLatencySec: () => ctx ? (ctx.baseLatency || 0) + (ctx.outputLatency || 0) : 0,
    isPlaying: () => playing !== null,
  };
}
