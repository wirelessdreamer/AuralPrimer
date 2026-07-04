/**
 * A/V sync calibration — a Rock Band-style two-pass latency calibrator,
 * shared by AuralPrimer (game) and AuralStudio.
 *
 * Buffer-based estimates can't see downstream delay (Bluetooth headphones add
 * 150-250ms past the whole audio stack; a TV not in game mode adds display
 * lag). The reliable way to measure each is empirically — play a steady beat,
 * have the user tap to the stimulus, and take the median delay:
 *
 *   PASS 1 (audio): metronome BEEPS. Tap to what you HEAR  -> audio latency.
 *   PASS 2 (video): the circle FLASHES, silent. Tap to what you SEE -> video latency.
 *
 * The effective visual offset combines them (see offsets.ts:effectiveAvOffsetMs).
 *
 * Backend-agnostic: uses its own Web Audio context for the clock + clicks, so
 * it measures the real output path regardless of which playback backend is
 * active.
 */

export type AvCalibrationResult = {
  /** Measured audio latency (ms). */
  audioMs: number;
  /** Measured video latency (ms). */
  videoMs: number;
};

export type AvCalibrationDeps = {
  /** Called with both measured offsets when the user applies. */
  onApply: (result: AvCalibrationResult) => void;
  /** Current offsets (ms) to seed each pass / keep when a pass is skipped. */
  getInitial: () => AvCalibrationResult;
  log?: (message: string, details?: unknown) => void;
};

export type AvCalibrationHandle = {
  open: () => void;
};

type Phase = "audio" | "video";

const PERIOD_SEC = 0.6; // 100 BPM
const FIRST_BEAT_MS = 200; // lead-in before the first beat
// Small, fixed lead for scheduling the beep cleanly on the audio clock. It is
// folded into the audio stimulus reference, so the tap delta still captures the
// real output latency (the point of the audio pass) without a scheduling glitch.
const AUDIO_LEAD_SEC = 0.05;
const MIN_TAPS_TO_APPLY = 8;
const MAX_CLICK_HISTORY = 256;

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

export function initAvCalibration(deps: AvCalibrationDeps): AvCalibrationHandle {
  let overlay: HTMLElement | null = null;
  let pulseEl: HTMLElement | null = null;
  let countEl: HTMLElement | null = null;
  let estimateEl: HTMLElement | null = null;
  let titleEl: HTMLElement | null = null;
  let instructionEl: HTMLElement | null = null;
  let stepEl: HTMLElement | null = null;
  let nextBtn: HTMLButtonElement | null = null;
  let startBtn: HTMLButtonElement | null = null;
  let skipBtn: HTMLButtonElement | null = null;

  let audioCtx: AudioContext | null = null;
  let schedulerTimer: number | null = null;
  let running = false;

  let phase: Phase = "audio";
  // Measured values for this session; seeded from the current settings so a
  // skipped pass keeps its existing value.
  let result: AvCalibrationResult = { audioMs: 0, videoMs: 0 };

  // performance.now() timestamps at which each stimulus was actually PRESENTED
  // to the user (they perceive it `latency` later). Recorded from the wall clock
  // at fire time — NEVER predicted from the audio clock, which can stall when
  // silent (the video pass) and drag the estimate upward over time.
  let stimulusPerf: number[] = [];
  let nextBeatPerf = 0;
  const taps: number[] = [];

  function flashPulse(): void {
    if (!pulseEl) return;
    pulseEl.classList.remove("pulse");
    void pulseEl.offsetWidth; // force reflow so rapid pulses restart the animation
    pulseEl.classList.add("pulse");
  }

  function fireBeat(): void {
    if (!audioCtx || !running) return;
    const beatPerf = performance.now();
    // The visual flash is the video-pass stimulus; it renders ~now, so its
    // reference is the wall clock directly.
    let stimulusAt = beatPerf;
    if (phase === "audio") {
      // Emit the audible beep a small, fixed lead ahead for a clean attack; the
      // beep (not the flash) is what the user reacts to, so reference its play
      // time — the tap delta then includes the real audio output latency.
      const startAt = audioCtx.currentTime + AUDIO_LEAD_SEC;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.frequency.value = 1000;
      osc.type = "square";
      gain.gain.setValueAtTime(0.0001, startAt);
      gain.gain.exponentialRampToValueAtTime(0.5, startAt + 0.002);
      gain.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.05);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(startAt);
      osc.stop(startAt + 0.06);
      stimulusAt = beatPerf + AUDIO_LEAD_SEC * 1000;
    }
    stimulusPerf.push(stimulusAt);
    if (stimulusPerf.length > MAX_CLICK_HISTORY) stimulusPerf.shift();
    flashPulse();
  }

  // Self-correcting wall-clock beat loop: each beat re-targets the next off a
  // fixed grid, so setTimeout jitter never accumulates and the reference times
  // stay independent of the (possibly stalled) audio clock.
  function tickBeat(): void {
    if (!running) return;
    fireBeat();
    nextBeatPerf += PERIOD_SEC * 1000;
    schedulerTimer = window.setTimeout(tickBeat, Math.max(0, nextBeatPerf - performance.now()));
  }

  function registerTap(tapPerf: number): void {
    // Match to the most recent stimulus before the tap, within one period —
    // the one the user reacted to. delta = perceptual latency.
    let best: number | null = null;
    for (let i = stimulusPerf.length - 1; i >= 0; i -= 1) {
      const dt = tapPerf - stimulusPerf[i];
      if (dt < 0) continue;
      if (dt >= PERIOD_SEC * 1000) break;
      best = dt;
      break;
    }
    if (best == null) return;
    if (best < -50 || best > 600) return; // reject obvious mistaps
    taps.push(best);
    refreshStats();
  }

  function refreshStats(): void {
    if (countEl) countEl.textContent = `Taps: ${taps.length}`;
    if (estimateEl) {
      const label = phase === "audio" ? "audio" : "video";
      estimateEl.textContent = taps.length
        ? `Estimated ${label} latency: ${Math.round(median(taps))} ms`
        : `Estimated ${label} latency: —`;
    }
    if (nextBtn) nextBtn.disabled = taps.length < MIN_TAPS_TO_APPLY;
  }

  async function startMetronome(): Promise<void> {
    if (running) return;
    try {
      const Ctor: typeof AudioContext =
        window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      audioCtx = new Ctor();
      if (audioCtx.state === "suspended") await audioCtx.resume();
    } catch (e) {
      deps.log?.("av-calibration: failed to create AudioContext", e);
      if (estimateEl) estimateEl.textContent = "Audio unavailable on this device";
      return;
    }
    taps.length = 0;
    stimulusPerf = [];
    running = true;
    refreshStats();
    if (startBtn) startBtn.textContent = "Restart";
    // Drive beats off the wall clock (first after a short lead), not the audio
    // clock, so the video pass is immune to an idle/stalled AudioContext.
    nextBeatPerf = performance.now() + FIRST_BEAT_MS;
    schedulerTimer = window.setTimeout(tickBeat, FIRST_BEAT_MS);
  }

  function stopMetronome(): void {
    running = false;
    if (schedulerTimer != null) {
      window.clearTimeout(schedulerTimer);
      schedulerTimer = null;
    }
    if (audioCtx) {
      void audioCtx.close().catch(() => undefined);
      audioCtx = null;
    }
  }

  /** Capture the median for the current phase (if enough taps were recorded). */
  function captureCurrentPhase(): void {
    if (taps.length >= MIN_TAPS_TO_APPLY) {
      const ms = Math.round(median(taps));
      if (phase === "audio") result.audioMs = ms;
      else result.videoMs = ms;
    }
  }

  function renderPhase(): void {
    stopMetronome();
    taps.length = 0;
    if (stepEl) stepEl.textContent = phase === "audio" ? "Step 1 of 2 · Audio" : "Step 2 of 2 · Video";
    if (titleEl) titleEl.textContent = phase === "audio" ? "Calibrate audio latency" : "Calibrate video latency";
    if (instructionEl) {
      instructionEl.innerHTML =
        phase === "audio"
          ? `Put on the headphones / speakers you'll use. Press <b>SPACE</b> (or click the circle)
             in time with the beeps you <b>hear</b>.`
          : `Now the circle flashes silently. Press <b>SPACE</b> (or click the circle)
             in time with the flashes you <b>see</b> — this measures your display lag.`;
    }
    if (startBtn) startBtn.textContent = "Start";
    if (nextBtn) {
      nextBtn.textContent = phase === "audio" ? "Next ▸" : "Apply";
      nextBtn.disabled = true;
    }
    if (skipBtn) skipBtn.textContent = phase === "audio" ? "Skip audio" : "Skip video";
    refreshStats();
  }

  function advance(): void {
    captureCurrentPhase();
    if (phase === "audio") {
      phase = "video";
      renderPhase();
    } else {
      stopMetronome();
      deps.onApply({ ...result });
      deps.log?.("av-calibration: applied", result);
      hide();
    }
  }

  function skip(): void {
    // Keep the existing value for this phase; move on.
    if (phase === "audio") {
      phase = "video";
      renderPhase();
    } else {
      stopMetronome();
      deps.onApply({ ...result });
      deps.log?.("av-calibration: applied (video skipped)", result);
      hide();
    }
  }

  function onKeydown(ev: KeyboardEvent): void {
    if (!overlay || overlay.style.display === "none") return;
    if (ev.code === "Space" || ev.key === " ") {
      ev.preventDefault();
      // Use performance.now() (NOT ev.timeStamp): some webviews report
      // KeyboardEvent.timeStamp on a different epoch than performance.now(),
      // which is what the scheduled stimulus times use — the mismatch makes
      // every tap fail to match a click and get silently dropped. Handler
      // latency is sub-ms, so now() is an accurate tap time and matches the
      // pulse-click path.
      if (running) registerTap(performance.now());
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      hide();
    }
  }

  function hide(): void {
    stopMetronome();
    if (overlay) overlay.style.display = "none";
    window.removeEventListener("keydown", onKeydown, true);
  }

  function build(): void {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.className = "avCalOverlay";
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:9999;display:none;align-items:center;justify-content:center;" +
      "background:rgba(6,10,18,0.86);backdrop-filter:blur(2px);";
    overlay.innerHTML = `
      <div style="width:min(560px,92vw);background:#121a26;border:1px solid rgba(255,255,255,0.08);
                  border-radius:14px;padding:22px 24px;color:#dfeaff;font-family:system-ui,sans-serif;
                  box-shadow:0 18px 60px rgba(0,0,0,0.5);text-align:center;">
        <div id="avCalStep" style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#7d90b0;margin-bottom:4px;">Step 1 of 2 · Audio</div>
        <h2 id="avCalTitle" style="margin:0 0 6px;font-size:18px;">Calibrate audio latency</h2>
        <p id="avCalInstruction" style="margin:0 0 16px;font-size:13px;color:#9fb2d0;line-height:1.5;"></p>
        <div id="avCalPulse" style="width:120px;height:120px;border-radius:50%;margin:6px auto 14px;
             background:radial-gradient(circle at 50% 40%, #6ea8ff 0%, #2a4a86 60%, #182a4a 100%);
             cursor:pointer;transition:transform 0.05s ease;"></div>
        <div id="avCalCount" style="font-size:13px;color:#cdddff;">Taps: 0</div>
        <div id="avCalEstimate" style="font-size:15px;font-weight:700;margin:4px 0 18px;">Estimated audio latency: —</div>
        <div style="display:flex;gap:10px;justify-content:center;align-items:center;">
          <button id="avCalStart" class="ghostBtn">Start</button>
          <button id="avCalNext" disabled>Next ▸</button>
          <button id="avCalSkip" class="ghostBtn">Skip audio</button>
          <button id="avCalClose" class="ghostBtn">Cancel</button>
        </div>
        <div style="margin-top:12px;font-size:11px;color:#7d90b0;">
          Tip: tap at least ${MIN_TAPS_TO_APPLY} beats. You can fine-tune later with Ctrl+[ / Ctrl+].
        </div>
      </div>`;
    document.body.appendChild(overlay);

    pulseEl = overlay.querySelector<HTMLElement>("#avCalPulse");
    countEl = overlay.querySelector<HTMLElement>("#avCalCount");
    estimateEl = overlay.querySelector<HTMLElement>("#avCalEstimate");
    titleEl = overlay.querySelector<HTMLElement>("#avCalTitle");
    instructionEl = overlay.querySelector<HTMLElement>("#avCalInstruction");
    stepEl = overlay.querySelector<HTMLElement>("#avCalStep");
    nextBtn = overlay.querySelector<HTMLButtonElement>("#avCalNext");
    startBtn = overlay.querySelector<HTMLButtonElement>("#avCalStart");
    skipBtn = overlay.querySelector<HTMLButtonElement>("#avCalSkip");
    const closeBtn = overlay.querySelector<HTMLButtonElement>("#avCalClose");

    if (!document.getElementById("avCalStyle")) {
      const style = document.createElement("style");
      style.id = "avCalStyle";
      style.textContent =
        "@keyframes avCalPulse{0%{transform:scale(1);filter:brightness(1)}" +
        "20%{transform:scale(1.18);filter:brightness(1.8)}100%{transform:scale(1);filter:brightness(1)}}" +
        ".avCalOverlay #avCalPulse.pulse{animation:avCalPulse 0.18s ease-out;}";
      document.head.appendChild(style);
    }

    startBtn?.addEventListener("click", () => {
      // The button becomes "Restart" once running; restarting must reset the
      // tap count + re-anchor the clock, but startMetronome() bails while
      // running, so stop first (a no-op when not yet started).
      stopMetronome();
      void startMetronome();
    });
    pulseEl?.addEventListener("click", () => {
      if (running) registerTap(performance.now());
    });
    nextBtn?.addEventListener("click", () => advance());
    skipBtn?.addEventListener("click", () => skip());
    closeBtn?.addEventListener("click", () => hide());
  }

  function open(): void {
    build();
    if (!overlay) return;
    phase = "audio";
    result = { ...deps.getInitial() };
    renderPhase();
    overlay.style.display = "flex";
    window.addEventListener("keydown", onKeydown, true);
  }

  return { open };
}
