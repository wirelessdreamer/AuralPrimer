/**
 * A/V sync calibration — a Rock Band-style latency calibrator.
 *
 * Buffer-based latency estimates can't see downstream delay (Bluetooth
 * headphones add 150-250ms past the whole audio stack). The only reliable
 * way to measure true end-to-end audio latency is empirically: play a steady
 * metronome, have the user tap to the beat they HEAR, and measure the median
 * delay between each click and the tap. That delay IS the audio latency, and
 * it becomes the A/V sync offset (push falling notes that much later).
 *
 * Backend-agnostic: uses its own Web Audio context for the clicks, so it
 * measures the real output path including Bluetooth regardless of which song
 * playback backend is active.
 */

export type AvCalibrationDeps = {
  /** Called with the measured offset (ms, >=0 means audio lags the visual). */
  onApply: (offsetMs: number) => void;
  /** Current offset (ms) to show as the starting value. */
  getInitialOffsetMs: () => number;
  log?: (message: string, details?: unknown) => void;
};

export type AvCalibrationHandle = {
  open: () => void;
};

const PERIOD_SEC = 0.6; // 100 BPM
const LOOKAHEAD_SEC = 0.12;
const SCHEDULER_INTERVAL_MS = 25;
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
  let applyBtn: HTMLButtonElement | null = null;
  let startBtn: HTMLButtonElement | null = null;

  let audioCtx: AudioContext | null = null;
  let schedulerTimer: number | null = null;
  let pulseTimers: number[] = [];
  let running = false;

  // performance.now() timestamps at which each scheduled click is expected to
  // begin playing (i.e. enters the output — the user hears it `latency` later).
  let clickPlayPerf: number[] = [];
  let nextClickAudioTime = 0;
  let ctxAnchor = 0;
  let perfAnchor = 0;
  const taps: number[] = [];

  function clickPlayPerfToAudible(audioTime: number): number {
    return perfAnchor + (audioTime - ctxAnchor) * 1000;
  }

  function scheduleClick(audioTime: number): void {
    if (!audioCtx) return;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.frequency.value = 1000;
    osc.type = "square";
    gain.gain.setValueAtTime(0.0001, audioTime);
    gain.gain.exponentialRampToValueAtTime(0.5, audioTime + 0.002);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioTime + 0.05);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(audioTime);
    osc.stop(audioTime + 0.06);

    const playPerf = clickPlayPerfToAudible(audioTime);
    clickPlayPerf.push(playPerf);
    if (clickPlayPerf.length > MAX_CLICK_HISTORY) clickPlayPerf.shift();

    // Visual pulse at the scheduled play time (also lets the user sanity-check
    // by eye). Delay in perf time from now.
    const delayMs = Math.max(0, playPerf - performance.now());
    const timer = window.setTimeout(() => {
      if (!pulseEl) return;
      pulseEl.classList.remove("pulse");
      // Force reflow so the animation restarts on rapid pulses.
      void pulseEl.offsetWidth;
      pulseEl.classList.add("pulse");
    }, delayMs);
    pulseTimers.push(timer);
    if (pulseTimers.length > MAX_CLICK_HISTORY) {
      const old = pulseTimers.shift();
      if (old != null) window.clearTimeout(old);
    }
  }

  function schedulerTick(): void {
    if (!audioCtx || !running) return;
    const horizon = audioCtx.currentTime + LOOKAHEAD_SEC;
    while (nextClickAudioTime < horizon) {
      scheduleClick(nextClickAudioTime);
      nextClickAudioTime += PERIOD_SEC;
    }
  }

  function registerTap(tapPerf: number): void {
    // Match to the most recent click that began playing before the tap, within
    // one period — the click the user was reacting to. delta = audio latency.
    let best: number | null = null;
    for (let i = clickPlayPerf.length - 1; i >= 0; i -= 1) {
      const dt = tapPerf - clickPlayPerf[i];
      if (dt < 0) continue;
      if (dt >= PERIOD_SEC * 1000) break;
      best = dt;
      break;
    }
    if (best == null) return;
    // Reject obvious mistaps (negative or absurdly large).
    if (best < -50 || best > 600) return;
    taps.push(best);
    refreshStats();
  }

  function refreshStats(): void {
    if (countEl) countEl.textContent = `Taps: ${taps.length}`;
    if (estimateEl) {
      estimateEl.textContent = taps.length
        ? `Estimated latency: ${Math.round(median(taps))} ms`
        : "Estimated latency: —";
    }
    if (applyBtn) applyBtn.disabled = taps.length < MIN_TAPS_TO_APPLY;
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
    clickPlayPerf = [];
    ctxAnchor = audioCtx.currentTime;
    perfAnchor = performance.now();
    nextClickAudioTime = ctxAnchor + 0.2;
    running = true;
    refreshStats();
    if (startBtn) startBtn.textContent = "Restart";
    schedulerTick();
    schedulerTimer = window.setInterval(schedulerTick, SCHEDULER_INTERVAL_MS);
  }

  function stopMetronome(): void {
    running = false;
    if (schedulerTimer != null) {
      window.clearInterval(schedulerTimer);
      schedulerTimer = null;
    }
    for (const t of pulseTimers) window.clearTimeout(t);
    pulseTimers = [];
    if (audioCtx) {
      void audioCtx.close().catch(() => undefined);
      audioCtx = null;
    }
  }

  function onKeydown(ev: KeyboardEvent): void {
    if (!overlay || overlay.style.display === "none") return;
    if (ev.code === "Space" || ev.key === " ") {
      ev.preventDefault();
      if (running) registerTap(ev.timeStamp);
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      close();
    }
  }

  function close(): void {
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
        <h2 style="margin:0 0 6px;font-size:18px;">Calibrate A/V Sync</h2>
        <p style="margin:0 0 16px;font-size:13px;color:#9fb2d0;line-height:1.5;">
          Put on the headphones you'll play with. Press <b>SPACE</b> (or click the circle)
          in time with the beeps you <b>hear</b>. We'll measure your audio delay and align
          the falling notes to it.
        </p>
        <div id="avCalPulse" style="width:120px;height:120px;border-radius:50%;margin:6px auto 14px;
             background:radial-gradient(circle at 50% 40%, #6ea8ff 0%, #2a4a86 60%, #182a4a 100%);
             cursor:pointer;transition:transform 0.05s ease;"></div>
        <div id="avCalCount" style="font-size:13px;color:#cdddff;">Taps: 0</div>
        <div id="avCalEstimate" style="font-size:15px;font-weight:700;margin:4px 0 18px;">Estimated latency: —</div>
        <div style="display:flex;gap:10px;justify-content:center;">
          <button id="avCalStart" class="ghostBtn">Start</button>
          <button id="avCalApply" disabled>Apply</button>
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
    applyBtn = overlay.querySelector<HTMLButtonElement>("#avCalApply");
    startBtn = overlay.querySelector<HTMLButtonElement>("#avCalStart");
    const closeBtn = overlay.querySelector<HTMLButtonElement>("#avCalClose");

    // Inject the pulse keyframe once.
    if (!document.getElementById("avCalStyle")) {
      const style = document.createElement("style");
      style.id = "avCalStyle";
      style.textContent =
        "@keyframes avCalPulse{0%{transform:scale(1);filter:brightness(1)}" +
        "20%{transform:scale(1.18);filter:brightness(1.8)}100%{transform:scale(1);filter:brightness(1)}}" +
        ".avCalOverlay #avCalPulse.pulse{animation:avCalPulse 0.18s ease-out;}";
      document.head.appendChild(style);
    }

    startBtn?.addEventListener("click", () => void startMetronome());
    pulseEl?.addEventListener("click", () => {
      if (running) registerTap(performance.now());
    });
    applyBtn?.addEventListener("click", () => {
      const ms = Math.round(median(taps));
      deps.onApply(ms);
      deps.log?.("av-calibration: applied", { offsetMs: ms, taps: taps.length });
      close();
    });
    closeBtn?.addEventListener("click", () => close());
  }

  function open(): void {
    build();
    if (!overlay) return;
    overlay.style.display = "flex";
    taps.length = 0;
    refreshStats();
    if (startBtn) startBtn.textContent = "Start";
    window.addEventListener("keydown", onKeydown, true);
  }

  return { open };
}
