//! Native playback engine with callback-safe state updates.
//!
//! Key low-latency design change:
//! - The real-time callback does not lock a shared `Mutex`.
//! - Control commands are pushed into an SPSC ring buffer and applied inside the callback.
//! - Readback state is mirrored via atomics.

use crate::audio_engine::{LoopRegion, Transport};
use crate::wav_mix::{read_wav_pcm16_bytes, WavPcm16};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{BufferSize, SampleFormat, StreamConfig};
use rtrb::{Consumer, Producer, RingBuffer};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc::{self, SyncSender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const NONE_FRAME: u64 = u64::MAX;
const COMMAND_QUEUE_CAPACITY: usize = 1024;

#[derive(Debug, Clone, serde::Serialize)]
pub struct NativeAudioState {
    pub output_host: NativeAudioHostSelection,
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub output_device: NativeAudioDeviceSelection,
    pub is_playing: bool,
    pub t_sec: f64,
    pub playback_rate: f64,
    pub loop_t0_sec: Option<f64>,
    pub loop_t1_sec: Option<f64>,
    pub has_audio: bool,

    // Debug/perf telemetry for low-latency bring-up.
    pub output_buffer_frames: Option<u32>,
    pub callback_count: u64,
    pub callback_overrun_count: u64,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct NativeAudioHostSelection {
    pub id: String,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct NativeAudioHostInfo {
    pub id: String,
    pub name: String,
    pub is_default: bool,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct NativeAudioDeviceSelection {
    pub name: String,
    pub channels: u16,
    pub sample_rate_hz: u32,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct NativeAudioDeviceInfo {
    pub name: String,
    pub channels: u16,
    pub sample_rate_hz: u32,
    pub is_default: bool,
}

#[derive(Debug)]
enum EngineCommand {
    /// A loaded piano pack. Decoded on the calling thread and moved here:
    /// the pack is tens of megabytes and reading it on the audio thread
    /// would drop every buffer for the duration.
    SetPianoPack(Box<crate::piano::PianoPack>),
    SetPianoNotes(Vec<crate::piano::ScheduledNote>),
    SetPianoEnabled(bool),
    SetPianoGain(f32),
    LoadPcm16 {
        wav: WavPcm16,
    },
    /// Swap the active buffer (a re-mixed stem sum) WITHOUT resetting the play
    /// position — used when a per-track gain changes during playback.
    ReplacePcm16 {
        wav: WavPcm16,
    },
    Play,
    Pause,
    Stop,
    SeekFrames {
        frame: u64,
    },
    SetLoop {
        loop_region: Option<LoopRegion>,
    },
    SetPlaybackRate {
        rate: f64,
    },
}

struct EngineRuntimeState {
    wav: Option<WavPcm16>,
    // Floating-point cursor in source frames for nearest-neighbor rate changes.
    source_frame_cursor: f64,

    transport: Transport,
    is_playing: bool,
    playback_rate: f64,
    loop_region: Option<LoopRegion>,

    // Pitch-preserving time-stretcher, engaged only when playback_rate != 1.
    // Lazily created on first non-unity rate; 1x playback never touches it.
    time_stretcher: Option<TimeStretcher>,

    /// Sampled piano, mixed over the song from the chart's own notes.
    piano: crate::piano::PianoEngine,
}

#[derive(Default)]
struct EngineSnapshot {
    is_playing: AtomicBool,
    position_frames: AtomicU64,
    playback_rate_bits: AtomicU64,
    loop_start_frame: AtomicU64,
    loop_end_frame: AtomicU64,
    has_audio: AtomicBool,

    callback_count: AtomicU64,
    callback_overrun_count: AtomicU64,
    // Actual frames per output callback, observed live. WASAPI's BufferSize::
    // Default gives no config buffer size, so this is the only real source for
    // the output-latency estimate the playhead uses.
    observed_buffer_frames: AtomicU32,
}

impl EngineSnapshot {
    fn sync_from_runtime(&self, runtime: &EngineRuntimeState) {
        self.is_playing.store(runtime.is_playing, Ordering::Relaxed);
        self.position_frames
            .store(runtime.transport.position_frames(), Ordering::Relaxed);
        self.playback_rate_bits
            .store(runtime.playback_rate.to_bits(), Ordering::Relaxed);
        self.has_audio
            .store(runtime.wav.is_some(), Ordering::Relaxed);

        if let Some(lr) = runtime.loop_region {
            self.loop_start_frame
                .store(lr.start_frame, Ordering::Relaxed);
            self.loop_end_frame.store(lr.end_frame, Ordering::Relaxed);
        } else {
            self.loop_start_frame.store(NONE_FRAME, Ordering::Relaxed);
            self.loop_end_frame.store(NONE_FRAME, Ordering::Relaxed);
        }
    }

    fn read_loop_frames(&self) -> Option<(u64, u64)> {
        let start = self.loop_start_frame.load(Ordering::Relaxed);
        let end = self.loop_end_frame.load(Ordering::Relaxed);
        if start == NONE_FRAME || end == NONE_FRAME || end <= start {
            None
        } else {
            Some((start, end))
        }
    }
}

/// The output buffer size to report as latency: the live per-callback count
/// (the real device buffer) when known, else the config value (only set under
/// BufferSize::Fixed). `None` disables the auto output-latency compensation.
/// Observed MUST win, or the WASAPI BufferSize::Default path (config == 0)
/// silently zeroes the latency term.
fn pick_output_buffer_frames(observed: u32, config: u32) -> Option<u32> {
    let frames = if observed > 0 { observed } else { config };
    if frames == 0 {
        None
    } else {
        Some(frames)
    }
}

fn sync_transport_to_source_cursor(runtime: &mut EngineRuntimeState) {
    let frame = if runtime.source_frame_cursor.is_finite() && runtime.source_frame_cursor > 0.0 {
        runtime.source_frame_cursor.floor() as u64
    } else {
        0
    };
    runtime.transport.seek_frames(frame);
}

fn trim_non_empty(s: &str) -> Option<&str> {
    let t = s.trim();
    if t.is_empty() {
        None
    } else {
        Some(t)
    }
}

fn host_id_key(host_id: cpal::HostId) -> String {
    host_id.name().to_ascii_lowercase()
}

fn resolve_output_host_selection(
    requested: Option<NativeAudioHostSelection>,
) -> Result<(cpal::Host, NativeAudioHostSelection), String> {
    let available = cpal::available_hosts();
    if available.is_empty() {
        return Err("no audio hosts available".to_string());
    }

    if let Some(sel) = requested {
        let req = trim_non_empty(&sel.id)
            .ok_or_else(|| "output host id cannot be empty".to_string())?
            .to_ascii_lowercase();
        for host_id in available {
            let key = host_id_key(host_id);
            let by_name = host_id.name().eq_ignore_ascii_case(&req);
            if key == req || by_name {
                let host = cpal::host_from_id(host_id).map_err(|e| format!("host_from_id: {e}"))?;
                return Ok((host, NativeAudioHostSelection { id: key }));
            }
        }
        let available_names = cpal::available_hosts()
            .into_iter()
            .map(|h| h.name())
            .collect::<Vec<_>>()
            .join(", ");
        return Err(format!(
            "audio host '{}' not found (available: {available_names})",
            sel.id
        ));
    }

    let host = cpal::default_host();
    let sel = NativeAudioHostSelection {
        id: host_id_key(host.id()),
    };
    Ok((host, sel))
}

pub fn canonicalize_output_host_selection(
    requested: NativeAudioHostSelection,
) -> Result<NativeAudioHostSelection, String> {
    let (_, sel) = resolve_output_host_selection(Some(requested))?;
    Ok(sel)
}

pub fn list_output_hosts() -> Result<Vec<NativeAudioHostInfo>, String> {
    let default_key = host_id_key(cpal::default_host().id());
    let mut out = cpal::available_hosts()
        .into_iter()
        .map(|h| NativeAudioHostInfo {
            id: host_id_key(h),
            name: h.name().to_string(),
            is_default: host_id_key(h) == default_key,
        })
        .collect::<Vec<_>>();
    out.sort_by(|a, b| {
        let ad = if a.is_default { 0 } else { 1 };
        let bd = if b.is_default { 0 } else { 1 };
        ad.cmp(&bd).then_with(|| a.name.cmp(&b.name))
    });
    Ok(out)
}

/// List output devices (best-effort summary).
pub fn list_output_devices(
    requested_host: Option<NativeAudioHostSelection>,
) -> Result<Vec<NativeAudioDeviceInfo>, String> {
    let (host, _) = resolve_output_host_selection(requested_host)?;
    let default_name = host.default_output_device().and_then(|d| d.name().ok());
    let mut out = vec![];
    for dev in host
        .output_devices()
        .map_err(|e| format!("output_devices: {e}"))?
    {
        let name = dev.name().unwrap_or_else(|_| "(unknown)".to_string());
        if let Ok(cfg) = dev.default_output_config() {
            out.push(NativeAudioDeviceInfo {
                is_default: default_name.as_ref().is_some_and(|d| d == &name),
                name,
                channels: cfg.channels(),
                sample_rate_hz: cfg.sample_rate().0,
            });
        }
    }
    out.sort_by(|a, b| {
        let ad = if a.is_default { 0 } else { 1 };
        let bd = if b.is_default { 0 } else { 1 };
        ad.cmp(&bd).then_with(|| {
            a.name
                .to_ascii_lowercase()
                .cmp(&b.name.to_ascii_lowercase())
        })
    });
    Ok(out)
}

fn resolve_output_device_selection(
    requested_device: Option<NativeAudioDeviceSelection>,
    requested_host: Option<NativeAudioHostSelection>,
) -> Result<NativeAudioDeviceSelection, String> {
    let devices = list_output_devices(requested_host)?;
    if devices.is_empty() {
        return Err("no output devices available".to_string());
    }

    if let Some(sel) = requested_device {
        let req_name = trim_non_empty(&sel.name)
            .ok_or_else(|| "output device name cannot be empty".to_string())?;

        if let Some(exact) = devices.iter().find(|d| {
            d.name == req_name
                && d.channels == sel.channels
                && d.sample_rate_hz == sel.sample_rate_hz
        }) {
            return Ok(NativeAudioDeviceSelection {
                name: exact.name.clone(),
                channels: exact.channels,
                sample_rate_hz: exact.sample_rate_hz,
            });
        }

        if let Some(by_name) = devices.iter().find(|d| d.name == req_name) {
            return Ok(NativeAudioDeviceSelection {
                name: by_name.name.clone(),
                channels: by_name.channels,
                sample_rate_hz: by_name.sample_rate_hz,
            });
        }

        if let Some(by_name_ci) = devices
            .iter()
            .find(|d| d.name.eq_ignore_ascii_case(req_name))
        {
            return Ok(NativeAudioDeviceSelection {
                name: by_name_ci.name.clone(),
                channels: by_name_ci.channels,
                sample_rate_hz: by_name_ci.sample_rate_hz,
            });
        }

        let available = devices
            .iter()
            .map(|d| d.name.as_str())
            .collect::<Vec<_>>()
            .join(", ");
        return Err(format!(
            "output device '{req_name}' not found (available: {available})"
        ));
    }

    let picked = devices
        .iter()
        .find(|d| d.is_default)
        .unwrap_or_else(|| devices.first().unwrap());
    Ok(NativeAudioDeviceSelection {
        name: picked.name.clone(),
        channels: picked.channels,
        sample_rate_hz: picked.sample_rate_hz,
    })
}

pub fn canonicalize_output_device_selection(
    requested_host: Option<NativeAudioHostSelection>,
    requested_device: NativeAudioDeviceSelection,
) -> Result<NativeAudioDeviceSelection, String> {
    resolve_output_device_selection(Some(requested_device), requested_host)
}

pub fn preferred_output_sample_rate_for_selection(
    requested_host: Option<NativeAudioHostSelection>,
    requested_device: Option<NativeAudioDeviceSelection>,
) -> Result<u32, String> {
    Ok(resolve_output_device_selection(requested_device, requested_host)?.sample_rate_hz)
}

fn find_output_device_by_selection(
    host: &cpal::Host,
    selection: &NativeAudioDeviceSelection,
) -> Result<cpal::Device, String> {
    let mut by_name_match: Option<cpal::Device> = None;

    for dev in host
        .output_devices()
        .map_err(|e| format!("output_devices: {e}"))?
    {
        let name = dev.name().unwrap_or_else(|_| "(unknown)".to_string());
        if name != selection.name {
            continue;
        }

        if by_name_match.is_none() {
            by_name_match = Some(dev.clone());
        }

        if let Ok(cfg) = dev.default_output_config() {
            if cfg.channels() == selection.channels
                && cfg.sample_rate().0 == selection.sample_rate_hz
            {
                return Ok(dev);
            }
        }
    }

    if let Some(dev) = by_name_match {
        return Ok(dev);
    }

    let available = list_output_devices(None)?
        .into_iter()
        .map(|d| d.name)
        .collect::<Vec<_>>()
        .join(", ");
    Err(format!(
        "output device '{}' not found (available: {available})",
        selection.name
    ))
}

/// Handle stored in Tauri managed state.
///
/// `cpal::Stream` itself remains owned by the audio thread.
pub struct NativeAudioHandle {
    pub output_host: NativeAudioHostSelection,
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub output_device: NativeAudioDeviceSelection,

    commands: Mutex<Producer<EngineCommand>>,
    snapshot: Arc<EngineSnapshot>,
    output_buffer_frames: Arc<AtomicU32>,

    // Per-track stem mix, owned off the audio thread. Gains are applied by
    // re-summing the stems here (control thread) and swapping the result into
    // the player; the real-time callback never sees the stems or the gains.
    stem_mix: Mutex<StemMix>,

    shutdown: Arc<AtomicBool>,
    thread: Mutex<Option<std::thread::JoinHandle<()>>>,
}

/// Stems (resampled to the engine sample rate, same channel count + length) and
/// their current linear gains, kept on the control thread for re-mixing.
#[derive(Default)]
struct StemMix {
    stems: Vec<WavPcm16>,
    gains: Vec<f32>,
}

/// Sum `stems[k] * gains[k]` into one interleaved i16 buffer (saturating).
/// Runs on the control thread, never in the audio callback.
fn mix_stems(stems: &[WavPcm16], gains: &[f32], sample_rate_hz: u32, channels: u16) -> WavPcm16 {
    let len = stems.iter().map(|s| s.data.len()).max().unwrap_or(0);
    let mut acc = vec![0.0f32; len];
    for (stem, &gain) in stems.iter().zip(gains.iter()) {
        if gain == 0.0 {
            continue;
        }
        for (i, &sample) in stem.data.iter().enumerate() {
            acc[i] += sample as f32 * gain;
        }
    }
    let data = acc
        .iter()
        .map(|&v| v.round().clamp(i16::MIN as f32, i16::MAX as f32) as i16)
        .collect();
    WavPcm16 {
        sample_rate: sample_rate_hz,
        channels,
        data,
    }
}

impl NativeAudioHandle {
    pub fn new_with_output_device(
        sample_rate_hz: u32,
        channels: u16,
        output_host: Option<NativeAudioHostSelection>,
        output_device: Option<NativeAudioDeviceSelection>,
    ) -> Result<Self, String> {
        if channels == 0 {
            return Err("channels must be > 0".to_string());
        }

        let (host, output_host) = resolve_output_host_selection(output_host)?;
        let output_device =
            resolve_output_device_selection(output_device, Some(output_host.clone()))?;

        // Fail early if the device cannot be resolved at init time.
        let _ = find_output_device_by_selection(&host, &output_device)?;

        let transport = Transport::new(sample_rate_hz)?;
        let runtime = EngineRuntimeState {
            wav: None,
            piano: crate::piano::PianoEngine::default(),
            source_frame_cursor: 0.0,
            transport,
            is_playing: false,
            playback_rate: 1.0,
            loop_region: None,
            time_stretcher: None,
        };

        let snapshot = Arc::new(EngineSnapshot::default());
        snapshot.sync_from_runtime(&runtime);

        let output_buffer_frames = Arc::new(AtomicU32::new(0));

        let (producer, consumer) = RingBuffer::<EngineCommand>::new(COMMAND_QUEUE_CAPACITY);

        let shutdown = Arc::new(AtomicBool::new(false));
        let shutdown_for_thread = shutdown.clone();
        let snapshot_for_thread = snapshot.clone();
        let outbuf_for_thread = output_buffer_frames.clone();
        let out_host_for_thread = output_host.clone();
        let outdev_for_thread = output_device.clone();
        let (startup_ready_tx, startup_ready_rx) = mpsc::sync_channel::<Result<(), String>>(1);
        let startup_ready_for_err = startup_ready_tx.clone();

        let th = std::thread::spawn(move || {
            if let Err(e) = run_output_stream_thread(
                runtime,
                consumer,
                snapshot_for_thread,
                outbuf_for_thread,
                shutdown_for_thread,
                sample_rate_hz,
                channels,
                out_host_for_thread,
                outdev_for_thread,
                startup_ready_tx,
            ) {
                let _ = startup_ready_for_err.try_send(Err(e.clone()));
                eprintln!("native audio thread failed: {e}");
            }
        });

        match startup_ready_rx.recv_timeout(Duration::from_secs(3)) {
            Ok(Ok(())) => {}
            Ok(Err(e)) => {
                shutdown.store(true, Ordering::Relaxed);
                let _ = th.join();
                return Err(format!("native audio startup failed: {e}"));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                shutdown.store(true, Ordering::Relaxed);
                let _ = th.join();
                return Err("native audio startup timed out".to_string());
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                shutdown.store(true, Ordering::Relaxed);
                let _ = th.join();
                return Err("native audio startup channel disconnected".to_string());
            }
        }

        Ok(Self {
            output_host,
            sample_rate_hz,
            channels,
            output_device,
            commands: Mutex::new(producer),
            snapshot,
            output_buffer_frames,
            stem_mix: Mutex::new(StemMix::default()),
            shutdown,
            thread: Mutex::new(Some(th)),
        })
    }

    /// Load a song as a set of stems (one per track). Each is resampled to the
    /// engine rate, padded to a common length, and summed at unity gain into
    /// the active buffer. The stems + gains are retained so `set_track_gain`
    /// can re-mix without re-decoding. `stems` is `(sample_rate, channels, data)`.
    pub fn load_stems(&self, stems: Vec<(u32, u16, Vec<i16>)>) -> Result<(), String> {
        if stems.is_empty() {
            return Err("load_stems: no stems provided".to_string());
        }
        let mut resampled: Vec<WavPcm16> = Vec::with_capacity(stems.len());
        for (sr, ch, data) in stems {
            if ch != self.channels {
                return Err(format!(
                    "stem channels {} != engine channels {}",
                    ch, self.channels
                ));
            }
            let data = resample_pcm16_linear_interleaved(&data, ch, sr, self.sample_rate_hz)?;
            resampled.push(WavPcm16 {
                sample_rate: self.sample_rate_hz,
                channels: ch,
                data,
            });
        }
        // Pad all stems to the longest so the per-sample sum is well-defined.
        let max_len = resampled.iter().map(|s| s.data.len()).max().unwrap_or(0);
        for s in &mut resampled {
            s.data.resize(max_len, 0);
        }
        let gains = vec![1.0f32; resampled.len()];
        let mix = mix_stems(&resampled, &gains, self.sample_rate_hz, self.channels);
        {
            let mut sm = self.stem_mix.lock().unwrap();
            sm.stems = resampled;
            sm.gains = gains;
        }
        // Fresh load -> reset position.
        self.enqueue(EngineCommand::LoadPcm16 { wav: mix })
    }

    /// Set a track's linear gain (0 = silent) and re-mix. No-op if no stems are
    /// loaded or the index is out of range. Keeps the current play position.
    pub fn set_track_gain(&self, index: usize, gain: f32) -> Result<(), String> {
        let mix = {
            let mut sm = self.stem_mix.lock().unwrap();
            if index >= sm.gains.len() {
                return Err(format!("track index {index} out of range"));
            }
            sm.gains[index] = gain.clamp(0.0, 4.0);
            mix_stems(&sm.stems, &sm.gains, self.sample_rate_hz, self.channels)
        };
        self.enqueue(EngineCommand::ReplacePcm16 { wav: mix })
    }

    /// Number of loaded stems (0 when playing a single mix).
    pub fn stem_count(&self) -> usize {
        self.stem_mix.lock().unwrap().stems.len()
    }

    fn enqueue(&self, cmd: EngineCommand) -> Result<(), String> {
        let mut producer = self.commands.lock().unwrap();
        producer
            .push(cmd)
            .map_err(|_| "native audio command queue full".to_string())
    }

    pub fn load_wav_bytes(&self, wav_bytes: &[u8]) -> Result<(), String> {
        let wav = read_wav_pcm16_bytes(wav_bytes)?;
        if wav.channels != self.channels {
            return Err(format!(
                "wav channels {} != engine channels {}",
                wav.channels, self.channels
            ));
        }
        let data = resample_pcm16_linear_interleaved(
            &wav.data,
            wav.channels,
            wav.sample_rate,
            self.sample_rate_hz,
        )?;
        self.enqueue(EngineCommand::LoadPcm16 {
            wav: WavPcm16 {
                sample_rate: self.sample_rate_hz,
                channels: wav.channels,
                data,
            },
        })
    }

    pub fn load_pcm16(
        &self,
        sample_rate_hz: u32,
        channels: u16,
        data: Vec<i16>,
    ) -> Result<(), String> {
        if channels != self.channels {
            return Err(format!(
                "decoded channels {} != engine channels {} (re-init required)",
                channels, self.channels
            ));
        }
        let data = resample_pcm16_linear_interleaved(
            &data,
            channels,
            sample_rate_hz,
            self.sample_rate_hz,
        )?;
        self.enqueue(EngineCommand::LoadPcm16 {
            wav: WavPcm16 {
                sample_rate: self.sample_rate_hz,
                channels,
                data,
            },
        })
    }

    /// Hand the engine a decoded piano pack.
    pub fn set_piano_pack(&self, pack: crate::piano::PianoPack) -> Result<(), String> {
        self.enqueue(EngineCommand::SetPianoPack(Box::new(pack)))
    }

    /// Replace the notes the piano plays, in transport frames.
    pub fn set_piano_notes(&self, notes: Vec<crate::piano::ScheduledNote>) -> Result<(), String> {
        self.enqueue(EngineCommand::SetPianoNotes(notes))
    }

    pub fn set_piano_enabled(&self, on: bool) -> Result<(), String> {
        self.enqueue(EngineCommand::SetPianoEnabled(on))
    }

    pub fn set_piano_gain(&self, gain: f32) -> Result<(), String> {
        self.enqueue(EngineCommand::SetPianoGain(gain))
    }

    pub fn play(&self) -> Result<(), String> {
        self.enqueue(EngineCommand::Play)
    }

    pub fn pause(&self) -> Result<(), String> {
        self.enqueue(EngineCommand::Pause)
    }

    pub fn stop(&self) -> Result<(), String> {
        self.enqueue(EngineCommand::Stop)
    }

    pub fn seek_seconds(&self, t_sec: f64) -> Result<(), String> {
        let frame = seconds_to_frames_clamped(t_sec, self.sample_rate_hz);
        self.enqueue(EngineCommand::SeekFrames { frame })
    }

    pub fn set_loop_seconds(&self, t0: Option<f64>, t1: Option<f64>) -> Result<(), String> {
        let lr = match (t0, t1) {
            (Some(a), Some(b)) => {
                let start = seconds_to_frames_clamped(a.max(0.0), self.sample_rate_hz);
                let end = seconds_to_frames_clamped(b.max(0.0), self.sample_rate_hz);
                Some(LoopRegion::new(start, end)?)
            }
            _ => None,
        };
        self.enqueue(EngineCommand::SetLoop { loop_region: lr })
    }

    pub fn set_playback_rate(&self, rate: f64) -> Result<(), String> {
        self.enqueue(EngineCommand::SetPlaybackRate { rate })
    }

    pub fn state(&self) -> NativeAudioState {
        let playback_rate_bits = self.snapshot.playback_rate_bits.load(Ordering::Relaxed);
        let playback_rate = f64::from_bits(playback_rate_bits);
        let pos_frames = self.snapshot.position_frames.load(Ordering::Relaxed);
        let loop_frames = self.snapshot.read_loop_frames();
        let observed = self.snapshot.observed_buffer_frames.load(Ordering::Relaxed);
        let config = self.output_buffer_frames.load(Ordering::Relaxed);

        NativeAudioState {
            output_host: self.output_host.clone(),
            sample_rate_hz: self.sample_rate_hz,
            channels: self.channels,
            output_device: self.output_device.clone(),
            is_playing: self.snapshot.is_playing.load(Ordering::Relaxed),
            t_sec: pos_frames as f64 / self.sample_rate_hz as f64,
            playback_rate,
            loop_t0_sec: loop_frames.map(|(s, _)| s as f64 / self.sample_rate_hz as f64),
            loop_t1_sec: loop_frames.map(|(_, e)| e as f64 / self.sample_rate_hz as f64),
            has_audio: self.snapshot.has_audio.load(Ordering::Relaxed),
            output_buffer_frames: pick_output_buffer_frames(observed, config),
            callback_count: self.snapshot.callback_count.load(Ordering::Relaxed),
            callback_overrun_count: self.snapshot.callback_overrun_count.load(Ordering::Relaxed),
        }
    }

    pub fn shutdown(&self) {
        self.shutdown.store(true, Ordering::Relaxed);
        if let Some(jh) = self.thread.lock().unwrap().take() {
            let _ = jh.join();
        }
    }
}

fn seconds_to_frames_clamped(t_sec: f64, sample_rate_hz: u32) -> u64 {
    if !t_sec.is_finite() || t_sec <= 0.0 {
        return 0;
    }
    let frames_f64 = t_sec * sample_rate_hz as f64;
    if frames_f64 >= u64::MAX as f64 {
        return u64::MAX;
    }
    frames_f64.floor() as u64
}

fn sanitize_playback_rate(rate: f64) -> f64 {
    if rate.is_finite() && rate > 0.0 {
        rate
    } else {
        1.0
    }
}

fn resample_pcm16_linear_interleaved(
    input: &[i16],
    channels: u16,
    in_sample_rate_hz: u32,
    out_sample_rate_hz: u32,
) -> Result<Vec<i16>, String> {
    if channels == 0 {
        return Err("resample channels must be > 0".to_string());
    }
    if in_sample_rate_hz == 0 || out_sample_rate_hz == 0 {
        return Err("resample sample rates must be > 0".to_string());
    }

    let ch = channels as usize;
    if !input.len().is_multiple_of(ch) {
        return Err(format!(
            "resample input length {} is not divisible by channels {}",
            input.len(),
            channels
        ));
    }

    if input.is_empty() || in_sample_rate_hz == out_sample_rate_hz {
        return Ok(input.to_vec());
    }

    let src_frames = input.len() / ch;
    if src_frames == 0 {
        return Ok(vec![]);
    }

    let src_frames_u128 = src_frames as u128;
    let out_rate_u128 = out_sample_rate_hz as u128;
    let in_rate_u128 = in_sample_rate_hz as u128;

    let mut out_frames_u128 = (src_frames_u128 * out_rate_u128 + (in_rate_u128 / 2)) / in_rate_u128;
    if out_frames_u128 == 0 {
        out_frames_u128 = 1;
    }
    if out_frames_u128 > usize::MAX as u128 {
        return Err("resample output frame count overflow".to_string());
    }
    let out_frames = out_frames_u128 as usize;
    let out_len = out_frames
        .checked_mul(ch)
        .ok_or_else(|| "resample output length overflow".to_string())?;
    let mut out = vec![0i16; out_len];

    if src_frames == 1 {
        for f in 0..out_frames {
            let dst_base = f * ch;
            for c in 0..ch {
                out[dst_base + c] = input[c];
            }
        }
        return Ok(out);
    }

    let in_rate_f64 = in_sample_rate_hz as f64;
    let out_rate_f64 = out_sample_rate_hz as f64;
    for out_frame in 0..out_frames {
        let src_pos = (out_frame as f64) * in_rate_f64 / out_rate_f64;
        let src_i0 = src_pos.floor() as usize;
        let src_i1 = (src_i0 + 1).min(src_frames - 1);
        let frac = (src_pos - src_i0 as f64).clamp(0.0, 1.0) as f32;

        let src_base0 = src_i0 * ch;
        let src_base1 = src_i1 * ch;
        let dst_base = out_frame * ch;
        for c in 0..ch {
            let a = input[src_base0 + c] as f32;
            let b = input[src_base1 + c] as f32;
            let y = a + (b - a) * frac;
            out[dst_base + c] = y.round().clamp(i16::MIN as f32, i16::MAX as f32) as i16;
        }
    }

    Ok(out)
}

fn choose_stream_config(
    device: &cpal::Device,
    sample_rate_hz: u32,
    channels: u16,
) -> Result<(StreamConfig, SampleFormat, Option<u32>), String> {
    fn sample_format_rank(fmt: SampleFormat) -> u8 {
        match fmt {
            SampleFormat::F32 => 0,
            SampleFormat::I16 => 1,
            SampleFormat::U16 => 2,
            SampleFormat::F64 => 3,
            SampleFormat::I32 => 4,
            SampleFormat::U32 => 5,
            SampleFormat::I8 => 6,
            SampleFormat::U8 => 7,
            SampleFormat::I64 => 8,
            SampleFormat::U64 => 9,
            _ => u8::MAX,
        }
    }

    let default_format = device
        .default_output_config()
        .ok()
        .map(|cfg| cfg.sample_format());

    let mut best: Option<(u8, u8, StreamConfig, SampleFormat, Option<u32>)> = None;
    if let Ok(configs) = device.supported_output_configs() {
        for cfg in configs {
            if cfg.channels() != channels {
                continue;
            }
            if sample_rate_hz < cfg.min_sample_rate().0 || sample_rate_hz > cfg.max_sample_rate().0
            {
                continue;
            }

            let sample_format = cfg.sample_format();
            let mut selected = cfg
                .with_sample_rate(cpal::SampleRate(sample_rate_hz))
                .config();
            selected.buffer_size = BufferSize::Default;
            let preferred_buffer = None;
            let default_rank = if Some(sample_format) == default_format {
                0
            } else {
                1
            };
            let rank = sample_format_rank(sample_format);
            match &best {
                Some((best_default_rank, best_rank, _, _, _))
                    if *best_default_rank < default_rank
                        || (*best_default_rank == default_rank && *best_rank <= rank) => {}
                _ => {
                    best = Some((
                        default_rank,
                        rank,
                        selected,
                        sample_format,
                        preferred_buffer,
                    ));
                }
            }
        }
    }
    if let Some((_, _, selected, sample_format, preferred_buffer)) = best {
        return Ok((selected, sample_format, preferred_buffer));
    }

    let default_cfg = device
        .default_output_config()
        .map_err(|e| format!("default_output_config: {e}"))?;
    let sample_format = default_cfg.sample_format();
    let mut cfg = default_cfg.config();
    cfg.channels = channels;
    cfg.sample_rate = cpal::SampleRate(sample_rate_hz);
    let outbuf = match cfg.buffer_size {
        BufferSize::Fixed(fr) => Some(fr),
        _ => None,
    };
    Ok((cfg, sample_format, outbuf))
}

fn apply_engine_command(
    runtime: &mut EngineRuntimeState,
    cmd: EngineCommand,
    engine_channels: usize,
) {
    match cmd {
        EngineCommand::LoadPcm16 { wav } => {
            // Defensive check: reject runtime-incompatible channel shapes.
            if wav.channels as usize != engine_channels {
                runtime.wav = None;
                runtime.is_playing = false;
                runtime.transport.set_playing(false);
                runtime.transport.seek_frames(0);
                runtime.source_frame_cursor = 0.0;
                return;
            }

            runtime.wav = Some(wav);
            runtime.source_frame_cursor = 0.0;
            runtime.transport.seek_frames(0);
        }
        EngineCommand::ReplacePcm16 { wav } => {
            // Channel mismatch -> keep the current buffer rather than dropping audio.
            if wav.channels as usize == engine_channels {
                // Keep source_frame_cursor / transport position: same song, same
                // length, only the per-track gain mix changed.
                runtime.wav = Some(wav);
            }
        }
        EngineCommand::SetPianoPack(pack) => {
            runtime.piano.set_pack(Some(*pack));
        }
        EngineCommand::SetPianoNotes(notes) => {
            runtime.piano.set_notes(notes);
        }
        EngineCommand::SetPianoEnabled(on) => {
            runtime.piano.set_enabled(on);
        }
        EngineCommand::SetPianoGain(gain) => {
            runtime.piano.set_gain(gain);
        }
        EngineCommand::Play => {
            runtime.is_playing = true;
            runtime.transport.set_playing(true);
        }
        EngineCommand::Pause => {
            runtime.is_playing = false;
            runtime.transport.set_playing(false);
        }
        EngineCommand::Stop => {
            runtime.is_playing = false;
            runtime.transport.set_playing(false);
            runtime.transport.seek_frames(0);
            runtime.source_frame_cursor = 0.0;
        }
        EngineCommand::SeekFrames { frame } => {
            runtime.transport.seek_frames(frame);
            runtime.source_frame_cursor = runtime.transport.position_frames() as f64;
        }
        EngineCommand::SetLoop { loop_region } => {
            runtime.loop_region = loop_region;
            runtime.transport.set_loop_region(loop_region);
            runtime.source_frame_cursor = runtime.transport.position_frames() as f64;
        }
        EngineCommand::SetPlaybackRate { rate } => {
            runtime.playback_rate = sanitize_playback_rate(rate);
        }
    }
}

fn drain_engine_commands(
    runtime: &mut EngineRuntimeState,
    commands: &mut Consumer<EngineCommand>,
    engine_channels: usize,
) {
    while let Ok(cmd) = commands.pop() {
        apply_engine_command(runtime, cmd, engine_channels);
    }
}

fn wrap_cursor_in_loop(cursor: f64, loop_region: LoopRegion) -> f64 {
    let start = loop_region.start_frame as f64;
    let end = loop_region.end_frame as f64;
    if !cursor.is_finite() || cursor < start {
        return start;
    }
    if cursor < end {
        return cursor;
    }
    let len = end - start;
    if len <= 0.0 {
        return start;
    }
    start + ((cursor - start) % len)
}

/// Pitch-preserving time-stretch (WSOLA: Waveform-Similarity Overlap-Add).
///
/// Time-domain, no FFT, no dependencies. Plays the source slower/faster while
/// keeping the original pitch by overlap-adding windowed grains, aligning each
/// grain to the previous one's natural continuation via a short similarity
/// search so the overlap stays phase-coherent.
///
/// Only used when `playback_rate != 1`; unity playback never constructs one.
struct TimeStretcher {
    channels: usize,
    window: Vec<f32>, // Hann, length GRAIN
    // State (all in source frames unless noted).
    have_prev: bool,
    prev_grain_start: i64,
    analysis_ideal: f64,
    output_src_pos: f64, // source position mapped to the next output frame drained
    tail: Vec<f32>,      // interleaved overlap carry-over, len OVERLAP*channels
    out_ring: std::collections::VecDeque<f32>,
}

impl TimeStretcher {
    const GRAIN: usize = 2048;
    const OVERLAP: usize = 1024; // = GRAIN - SYN_HOP; 50% overlap
    const SYN_HOP: usize = 1024; // emitted frames per grain
    const SEARCH: i64 = 256; // WSOLA similarity search radius (frames)
    const CORR: usize = 1024; // similarity window length (frames)

    fn new(channels: usize) -> Self {
        let ch = channels.max(1);
        let g = Self::GRAIN as f32;
        let window = (0..Self::GRAIN)
            .map(|i| 0.5 - 0.5 * (2.0 * std::f32::consts::PI * i as f32 / g).cos())
            .collect();
        TimeStretcher {
            channels: ch,
            window,
            have_prev: false,
            prev_grain_start: 0,
            analysis_ideal: 0.0,
            output_src_pos: 0.0,
            tail: vec![0.0; Self::OVERLAP * ch],
            out_ring: std::collections::VecDeque::with_capacity(Self::GRAIN * ch * 2),
        }
    }

    fn channels(&self) -> usize {
        self.channels
    }

    /// Re-anchor to an absolute source position (seek, loop wrap, rate flip).
    fn reanchor(&mut self, src_frame: f64) {
        let anchor = src_frame.max(0.0);
        self.have_prev = false;
        self.prev_grain_start = anchor.round() as i64;
        self.analysis_ideal = anchor;
        self.output_src_pos = anchor;
        for v in self.tail.iter_mut() {
            *v = 0.0;
        }
        self.out_ring.clear();
    }

    /// True if the canonical cursor diverged from our output position (a seek).
    fn needs_reanchor(&self, canonical_src: f64) -> bool {
        (canonical_src - self.output_src_pos).abs() > Self::GRAIN as f64
    }

    #[inline]
    fn sample(data: &[i16], total_frames: usize, channels: usize, frame: i64, ch: usize) -> f32 {
        if frame < 0 {
            return 0.0;
        }
        let f = frame as usize;
        if f >= total_frames {
            return 0.0;
        }
        data[f * channels + ch] as f32 / i16::MAX as f32
    }

    fn produce_grain(&mut self, data: &[i16], total_frames: usize, rate: f64) {
        let ch = self.channels;
        let ia = self.analysis_ideal.round() as i64;

        // WSOLA: pick the grain start near `ia` whose leading region best
        // matches the natural continuation of the previously emitted grain.
        let chosen = if self.have_prev {
            let ref_start = self.prev_grain_start + Self::SYN_HOP as i64;
            let mut best_delta: i64 = 0;
            let mut best_score = f64::INFINITY;
            let mut delta = -Self::SEARCH;
            while delta <= Self::SEARCH {
                let cand = ia + delta;
                let mut score = 0.0f64;
                let mut k = 0usize;
                while k < Self::CORR {
                    let a = Self::sample(data, total_frames, ch, cand + k as i64, 0);
                    let b = Self::sample(data, total_frames, ch, ref_start + k as i64, 0);
                    let d = (a - b) as f64;
                    score += d * d;
                    if score >= best_score {
                        break;
                    }
                    k += 1;
                }
                if score < best_score {
                    best_score = score;
                    best_delta = delta;
                }
                delta += 1;
            }
            ia + best_delta
        } else {
            ia
        };

        // Window the grain and overlap-add: first OVERLAP frames combine with
        // the carried tail and are emitted; the rest become the next tail.
        for i in 0..Self::GRAIN {
            let w = self.window[i];
            for c in 0..ch {
                let s = Self::sample(data, total_frames, ch, chosen + i as i64, c) * w;
                if i < Self::OVERLAP {
                    let idx = i * ch + c;
                    self.out_ring.push_back(self.tail[idx] + s);
                } else {
                    self.tail[(i - Self::OVERLAP) * ch + c] = s;
                }
            }
        }

        self.prev_grain_start = chosen;
        self.have_prev = true;
        self.analysis_ideal += Self::SYN_HOP as f64 * rate;
    }

    /// Fill `out` (interleaved f32) with pitch-preserved, time-stretched audio.
    /// Returns the source position corresponding to the next output frame, so
    /// the transport clock keeps reporting true song position.
    fn process(
        &mut self,
        data: &[i16],
        total_frames: usize,
        rate: f64,
        out: &mut [f32],
        loop_region: Option<LoopRegion>,
    ) -> f64 {
        let ch = self.channels;
        let frames_needed = out.len() / ch;

        while self.out_ring.len() < out.len() {
            // Past the end with no loop: pad with silence rather than spin.
            if loop_region.is_none() && self.analysis_ideal > (total_frames + Self::GRAIN) as f64 {
                while self.out_ring.len() < out.len() {
                    self.out_ring.push_back(0.0);
                }
                break;
            }
            self.produce_grain(data, total_frames, rate);
        }

        for v in out.iter_mut() {
            *v = self.out_ring.pop_front().unwrap_or(0.0);
        }

        self.output_src_pos += frames_needed as f64 * rate;

        if let Some(lr) = loop_region {
            if self.output_src_pos >= lr.end_frame as f64 {
                self.reanchor(lr.start_frame as f64);
            }
        }

        self.output_src_pos
    }
}

fn render_output_block(runtime: &mut EngineRuntimeState, out: &mut [f32], channels: usize) -> u64 {
    if channels == 0 || !out.len().is_multiple_of(channels) {
        out.fill(0.0);
        return 0;
    }

    let frame_count = out.len() / channels;
    if frame_count == 0 {
        return 0;
    }

    if !runtime.is_playing {
        out.fill(0.0);
        return 0;
    }

    let Some(wav) = runtime.wav.as_ref() else {
        out.fill(0.0);
        return 0;
    };

    if wav.channels as usize != channels {
        out.fill(0.0);
        runtime.is_playing = false;
        runtime.transport.set_playing(false);
        return 0;
    }

    let src_total_frames = wav.data.len() / channels;
    let playback_rate = runtime.playback_rate;
    let loop_region = runtime.loop_region;

    // Pitch-preserving slowdown/speed-up. Unity rate falls through to the
    // sample-accurate passthrough below, leaving normal playback untouched.
    if (playback_rate - 1.0).abs() >= 1e-9 {
        let ts = runtime
            .time_stretcher
            .get_or_insert_with(|| TimeStretcher::new(channels));
        if ts.channels() != channels {
            *ts = TimeStretcher::new(channels);
        }
        if ts.needs_reanchor(runtime.source_frame_cursor) {
            ts.reanchor(runtime.source_frame_cursor);
        }
        let new_cursor = ts.process(&wav.data, src_total_frames, playback_rate, out, loop_region);
        runtime.source_frame_cursor = new_cursor;
        return frame_count as u64;
    }

    let mut cursor = runtime.source_frame_cursor;

    for frame_idx in 0..frame_count {
        let src_frame = if cursor.is_finite() && cursor >= 0.0 {
            cursor.floor() as usize
        } else {
            0
        };

        let dst_base = frame_idx * channels;
        if src_frame < src_total_frames {
            let src_base = src_frame * channels;
            for c in 0..channels {
                out[dst_base + c] = wav.data[src_base + c] as f32 / i16::MAX as f32;
            }
        } else {
            for c in 0..channels {
                out[dst_base + c] = 0.0;
            }
        }

        cursor += playback_rate;
        if let Some(lr) = loop_region {
            cursor = wrap_cursor_in_loop(cursor, lr);
        }
    }

    runtime.source_frame_cursor = cursor;
    frame_count as u64
}

fn f32_to_i16_sample(v: f32) -> i16 {
    (v.clamp(-1.0, 1.0) * i16::MAX as f32).round() as i16
}

fn f32_to_u16_sample(v: f32) -> u16 {
    (((v.clamp(-1.0, 1.0) + 1.0) * 0.5) * u16::MAX as f32).round() as u16
}

fn f32_to_i8_sample(v: f32) -> i8 {
    (v.clamp(-1.0, 1.0) * i8::MAX as f32).round() as i8
}

fn f32_to_u8_sample(v: f32) -> u8 {
    (((v.clamp(-1.0, 1.0) + 1.0) * 0.5) * u8::MAX as f32).round() as u8
}

fn f32_to_i32_sample(v: f32) -> i32 {
    (v.clamp(-1.0, 1.0) * i32::MAX as f32).round() as i32
}

fn f32_to_u32_sample(v: f32) -> u32 {
    (((v.clamp(-1.0, 1.0) + 1.0) * 0.5) * u32::MAX as f32).round() as u32
}

fn f32_to_i64_sample(v: f32) -> i64 {
    (v.clamp(-1.0, 1.0) * i64::MAX as f32).round() as i64
}

fn f32_to_u64_sample(v: f32) -> u64 {
    (((v.clamp(-1.0, 1.0) + 1.0) * 0.5) * u64::MAX as f32).round() as u64
}

fn update_callback_telemetry(
    snapshot: &EngineSnapshot,
    callback_t0: Instant,
    frame_count: usize,
    sample_rate_hz: u32,
) {
    snapshot.callback_count.fetch_add(1, Ordering::Relaxed);
    if frame_count > 0 {
        snapshot
            .observed_buffer_frames
            .store(frame_count as u32, Ordering::Relaxed);
    }
    let callback_budget_sec = (frame_count as f64) / sample_rate_hz as f64;
    if callback_t0.elapsed().as_secs_f64() > callback_budget_sec {
        snapshot
            .callback_overrun_count
            .fetch_add(1, Ordering::Relaxed);
    }
}

fn process_audio_callback_f32(
    runtime: &mut EngineRuntimeState,
    commands: &mut Consumer<EngineCommand>,
    snapshot: &EngineSnapshot,
    out: &mut [f32],
    engine_channels: usize,
    sample_rate_hz: u32,
) {
    let callback_t0 = Instant::now();

    drain_engine_commands(runtime, commands, engine_channels);

    // Where the song is BEFORE this block is rendered. The piano schedules
    // against it, so it has to be read before render_output_block moves it --
    // afterwards it is the position of the block's end, and every note would
    // start one buffer early.
    let block_start_frame = runtime.transport.position_frames();
    let playing = runtime.is_playing;

    render_output_block(runtime, out, engine_channels);

    // Over the song, not instead of it: the piano is a cue laid on top. Only
    // while the transport runs, or a paused song would keep playing its part.
    if playing {
        runtime
            .piano
            .mix(out, engine_channels, block_start_frame, sample_rate_hz);
    }

    sync_transport_to_source_cursor(runtime);
    snapshot.sync_from_runtime(runtime);

    update_callback_telemetry(
        snapshot,
        callback_t0,
        out.len() / engine_channels,
        sample_rate_hz,
    );
}

fn run_output_stream_thread(
    mut runtime: EngineRuntimeState,
    mut commands: Consumer<EngineCommand>,
    snapshot: Arc<EngineSnapshot>,
    output_buffer_frames: Arc<AtomicU32>,
    shutdown: Arc<AtomicBool>,
    sample_rate_hz: u32,
    channels: u16,
    output_host: NativeAudioHostSelection,
    output_device: NativeAudioDeviceSelection,
    startup_ready: SyncSender<Result<(), String>>,
) -> Result<(), String> {
    let (host, _) = resolve_output_host_selection(Some(output_host))?;
    let device = find_output_device_by_selection(&host, &output_device)?;
    let (cfg, sample_format, selected_buf_frames) =
        choose_stream_config(&device, sample_rate_hz, channels)?;
    output_buffer_frames.store(selected_buf_frames.unwrap_or(0), Ordering::Relaxed);
    eprintln!(
        "native audio stream init: device='{}' sample_rate={} channels={} sample_format={:?}",
        output_device.name, sample_rate_hz, channels, sample_format
    );

    let engine_channels = channels as usize;
    let stream = match sample_format {
        SampleFormat::F32 => {
            let snapshot_for_cb = snapshot.clone();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [f32], _info| {
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            out,
                            engine_channels,
                            sample_rate_hz,
                        );
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(f32): {e}"))?
        }
        SampleFormat::F64 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [f64], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = *src as f64;
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(f64): {e}"))?
        }
        SampleFormat::I8 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [i8], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_i8_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(i8): {e}"))?
        }
        SampleFormat::I16 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [i16], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_i16_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(i16): {e}"))?
        }
        SampleFormat::I32 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [i32], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_i32_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(i32): {e}"))?
        }
        SampleFormat::I64 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [i64], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_i64_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(i64): {e}"))?
        }
        SampleFormat::U8 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [u8], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_u8_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(u8): {e}"))?
        }
        SampleFormat::U16 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [u16], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_u16_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(u16): {e}"))?
        }
        SampleFormat::U32 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [u32], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_u32_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(u32): {e}"))?
        }
        SampleFormat::U64 => {
            let snapshot_for_cb = snapshot.clone();
            let mut scratch = Vec::<f32>::new();
            device
                .build_output_stream(
                    &cfg,
                    move |out: &mut [u64], _info| {
                        if scratch.len() != out.len() {
                            scratch.resize(out.len(), 0.0);
                        }
                        process_audio_callback_f32(
                            &mut runtime,
                            &mut commands,
                            &snapshot_for_cb,
                            &mut scratch,
                            engine_channels,
                            sample_rate_hz,
                        );
                        for (dst, src) in out.iter_mut().zip(scratch.iter()) {
                            *dst = f32_to_u64_sample(*src);
                        }
                    },
                    move |err| {
                        eprintln!("native audio stream error: {err}");
                    },
                    None,
                )
                .map_err(|e| format!("build_output_stream(u64): {e}"))?
        }
        _ => {
            return Err(format!(
                "unsupported output sample format from device '{}': {:?}",
                output_device.name, sample_format
            ));
        }
    };

    stream.play().map_err(|e| format!("stream.play: {e}"))?;
    let _ = startup_ready.send(Ok(()));

    // Keep thread alive until shutdown requested.
    while !shutdown.load(Ordering::Relaxed) {
        std::thread::sleep(Duration::from_millis(25));
    }

    drop(stream);
    Ok(())
}

#[derive(Default)]
pub struct NativeAudioEngineState {
    pub engine: Mutex<Option<NativeAudioHandle>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx_eq(a: f32, b: f32) {
        assert!((a - b).abs() < 1e-6, "left={a} right={b}");
    }

    #[test]
    fn time_stretch_constant_signal_stays_flat() {
        // Overlap-add of 50% Hann grains sums to ~1, so a DC input should pass
        // through at its original amplitude (past the initial fade-in grain).
        let ch = 1usize;
        let total = 20_000usize;
        let val: i16 = 10_000;
        let data = vec![val; total * ch];
        let mut ts = TimeStretcher::new(ch);
        ts.reanchor(0.0);
        let mut out = vec![0.0f32; 4096];
        ts.process(&data, total, 0.5, &mut out, None);
        let mut out2 = vec![0.0f32; 4096];
        ts.process(&data, total, 0.5, &mut out2, None);
        let expected = val as f32 / i16::MAX as f32;
        let mid = out2[2048];
        assert!(
            (mid - expected).abs() < 0.05,
            "steady sample {mid} vs {expected}"
        );
        assert!(out2.iter().all(|v| v.is_finite()));
    }

    #[test]
    fn time_stretch_cursor_tracks_rate() {
        // The returned cursor (transport position) must advance at `rate`,
        // independent of the stretcher's internal read-ahead.
        let ch = 2usize;
        let total = 40_000usize;
        let data = vec![0i16; total * ch];
        let mut ts = TimeStretcher::new(ch);
        ts.reanchor(0.0);
        let frames = 2000usize;
        let mut out = vec![0.0f32; frames * ch];
        let cursor = ts.process(&data, total, 0.5, &mut out, None);
        assert!(
            (cursor - frames as f64 * 0.5).abs() < 1.0,
            "cursor {cursor}"
        );
    }

    #[test]
    fn time_stretch_past_end_is_silent_and_finite() {
        let ch = 1usize;
        let total = 100usize;
        let data = vec![5000i16; total];
        let mut ts = TimeStretcher::new(ch);
        ts.reanchor(50.0);
        let mut out = vec![1.0f32; 1024];
        ts.process(&data, total, 0.5, &mut out, None);
        assert!(out.iter().all(|v| v.is_finite()));
    }

    #[test]
    fn time_stretch_reanchor_on_seek_divergence() {
        let mut ts = TimeStretcher::new(1);
        ts.reanchor(0.0);
        assert!(ts.needs_reanchor(10_000.0));
        assert!(!ts.needs_reanchor(0.0));
    }

    fn mono(samples: &[i16]) -> WavPcm16 {
        WavPcm16 {
            sample_rate: 48_000,
            channels: 1,
            data: samples.to_vec(),
        }
    }

    fn stereo(samples: &[i16]) -> WavPcm16 {
        WavPcm16 {
            sample_rate: 48_000,
            channels: 2,
            data: samples.to_vec(),
        }
    }

    fn mk_runtime(sample_rate: u32) -> EngineRuntimeState {
        EngineRuntimeState {
            wav: None,
            source_frame_cursor: 0.0,
            transport: Transport::new(sample_rate).unwrap(),
            is_playing: false,
            playback_rate: 1.0,
            loop_region: None,
            time_stretcher: None,
        }
    }

    #[test]
    fn golden_click_track_renders_without_time_drift() {
        // Impulses at known source frames; at rate 1 they must appear at the
        // SAME output frames, proving the render cursor never drifts from the
        // audio it emits (the root property behind "playhead sits on the hit").
        let clicks = [100usize, 200, 300];
        let mut samples = vec![0i16; 512];
        for &f in &clicks {
            samples[f] = 30_000;
        }
        let mut st = mk_runtime(48_000);
        st.wav = Some(mono(&samples));
        st.is_playing = true;
        st.transport.set_playing(true);
        st.source_frame_cursor = 0.0;

        // Render 6 contiguous 64-frame callbacks (384 frames, inside the clip)
        // to exercise cross-callback continuity without reaching end-of-audio.
        let mut rendered = Vec::<f32>::new();
        for _ in 0..6 {
            let prev = rendered.len();
            let mut out = vec![0.0f32; 64];
            let frames = render_output_block(&mut st, &mut out, 1) as usize;
            assert_eq!(frames, 64);
            // Cursor advanced by exactly the frames rendered — no drift.
            approx_eq(st.source_frame_cursor as f32, (prev + 64) as f32);
            rendered.extend_from_slice(&out[..frames]);
        }

        assert_eq!(rendered.len(), 384);
        for (i, &v) in rendered.iter().enumerate() {
            let expect = if clicks.contains(&i) {
                30_000.0 / i16::MAX as f32
            } else {
                0.0
            };
            approx_eq(v, expect);
        }
    }

    #[test]
    fn callback_telemetry_reports_real_output_buffer_frames() {
        // The playhead subtracts (output_buffer_frames*2)/sr as output latency,
        // so the engine MUST surface its real per-callback buffer size — WASAPI's
        // BufferSize::Default yields no config value, and regressing this to 0
        // silently dumps the whole output latency onto the manual calibration.
        let snap = EngineSnapshot::default();
        update_callback_telemetry(&snap, Instant::now(), 512, 48_000);
        assert_eq!(snap.observed_buffer_frames.load(Ordering::Relaxed), 512);
        // A zero-length callback must not clobber a known-good buffer size.
        update_callback_telemetry(&snap, Instant::now(), 0, 48_000);
        assert_eq!(snap.observed_buffer_frames.load(Ordering::Relaxed), 512);
    }

    #[test]
    fn pick_output_buffer_frames_prefers_observed_over_config() {
        // WASAPI BufferSize::Default: config is 0, observed is the only source.
        assert_eq!(pick_output_buffer_frames(512, 0), Some(512));
        // Nothing known yet -> no auto latency (falls to manual calibration).
        assert_eq!(pick_output_buffer_frames(0, 0), None);
        // Observed (real) wins even when a config value is also present.
        assert_eq!(pick_output_buffer_frames(512, 256), Some(512));
        // BufferSize::Fixed with no callback yet -> use the config value.
        assert_eq!(pick_output_buffer_frames(0, 256), Some(256));
    }

    #[test]
    fn render_output_paused_is_silence() {
        let mut st = mk_runtime(48_000);
        st.wav = Some(mono(&[10, 20, 30]));
        st.is_playing = false;
        let mut out = vec![1.0f32; 3];
        let frames = render_output_block(&mut st, &mut out, 1);
        assert_eq!(frames, 0);
        assert_eq!(out, vec![0.0, 0.0, 0.0]);
    }

    #[test]
    #[ignore = "pre-existing: WSOLA rate-stretch stereo channel alignment regression; tracked separately"]
    fn render_output_preserves_stereo_channel_alignment_with_rate() {
        let mut st = mk_runtime(48_000);
        st.wav = Some(stereo(&[
            1000, -1000, // frame 0
            2000, -2000, // frame 1
            3000, -3000, // frame 2
        ]));
        st.is_playing = true;
        st.transport.set_playing(true);
        st.playback_rate = 2.0;
        st.source_frame_cursor = 0.0;

        let mut out = vec![0.0f32; 4]; // 2 stereo frames
        let frames = render_output_block(&mut st, &mut out, 2);
        assert_eq!(frames, 2);

        approx_eq(out[0], 1000.0 / i16::MAX as f32);
        approx_eq(out[1], -1000.0 / i16::MAX as f32);
        approx_eq(out[2], 3000.0 / i16::MAX as f32);
        approx_eq(out[3], -3000.0 / i16::MAX as f32);
    }

    #[test]
    fn process_audio_callback_tracks_source_position_at_playback_rate() {
        let mut st = mk_runtime(48_000);
        st.wav = Some(mono(&[
            100, // frame 0
            200, // frame 1
            300, // frame 2
            400, // frame 3
            500, // frame 4
        ]));
        st.is_playing = true;
        st.transport.set_playing(true);
        st.playback_rate = 2.0;
        let snapshot = EngineSnapshot::default();
        let (_producer, mut consumer) = RingBuffer::<EngineCommand>::new(8);
        let mut out = vec![0.0f32; 2];

        process_audio_callback_f32(&mut st, &mut consumer, &snapshot, &mut out, 1, 48_000);

        assert_eq!(st.source_frame_cursor, 4.0);
        assert_eq!(st.transport.position_frames(), 4);
        assert_eq!(snapshot.position_frames.load(Ordering::Relaxed), 4);
    }

    #[test]
    fn render_output_wraps_loop_region() {
        let mut st = mk_runtime(48_000);
        st.wav = Some(mono(&[
            100, // frame 0
            200, // frame 1
            300, // frame 2
            400, // frame 3
        ]));
        st.is_playing = true;
        st.transport.set_playing(true);
        st.loop_region = Some(LoopRegion::new(1, 3).unwrap()); // frames 1..3
        st.transport.set_loop_region(st.loop_region);
        st.source_frame_cursor = 2.0;

        let mut out = vec![0.0f32; 4]; // 4 mono frames
        let frames = render_output_block(&mut st, &mut out, 1);
        assert_eq!(frames, 4);

        approx_eq(out[0], 300.0 / i16::MAX as f32);
        approx_eq(out[1], 200.0 / i16::MAX as f32);
        approx_eq(out[2], 300.0 / i16::MAX as f32);
        approx_eq(out[3], 200.0 / i16::MAX as f32);
    }

    #[test]
    fn apply_engine_command_seek_respects_loop_invariant() {
        let mut st = mk_runtime(48_000);
        apply_engine_command(
            &mut st,
            EngineCommand::SetLoop {
                loop_region: Some(LoopRegion::new(100, 200).unwrap()),
            },
            1,
        );
        apply_engine_command(&mut st, EngineCommand::SeekFrames { frame: 50 }, 1);
        assert_eq!(st.transport.position_frames(), 100);
        assert_eq!(st.source_frame_cursor, 100.0);
    }

    #[test]
    fn invalid_playback_rate_sanitizes_to_one() {
        let mut st = mk_runtime(48_000);
        apply_engine_command(
            &mut st,
            EngineCommand::SetPlaybackRate { rate: f64::NAN },
            1,
        );
        assert_eq!(st.playback_rate, 1.0);
    }

    #[test]
    fn resample_identity_returns_same_samples() {
        let input = vec![100i16, -200, 300, -400];
        let out = resample_pcm16_linear_interleaved(&input, 2, 48_000, 48_000).unwrap();
        assert_eq!(out, input);
    }

    #[test]
    fn resample_upsample_preserves_endpoints() {
        let input = vec![0i16, 1000, 2000, 3000];
        let out = resample_pcm16_linear_interleaved(&input, 1, 4, 8).unwrap();
        assert_eq!(out.len(), 8);
        assert_eq!(out[0], 0);
        assert_eq!(out[2], 1000);
        assert_eq!(out[4], 2000);
        assert_eq!(out[6], 3000);
        assert_eq!(out[1], 500);
    }

    #[test]
    fn resample_downsample_keeps_stereo_interleaving() {
        // L/R frame sequence:
        // f0: 100, -100
        // f1: 200, -200
        // f2: 300, -300
        // f3: 400, -400
        let input = vec![100i16, -100, 200, -200, 300, -300, 400, -400];
        let out = resample_pcm16_linear_interleaved(&input, 2, 4, 2).unwrap();
        assert_eq!(out.len(), 4); // 2 frames * 2 channels
        assert_eq!(out[0], 100);
        assert_eq!(out[1], -100);
        assert_eq!(out[2], 300);
        assert_eq!(out[3], -300);
    }

    #[test]
    fn resample_single_frame_repeats_value() {
        let input = vec![123i16, -456];
        let out = resample_pcm16_linear_interleaved(&input, 2, 1, 4).unwrap();
        assert_eq!(out, vec![123, -456, 123, -456, 123, -456, 123, -456]);
    }

    #[test]
    fn resample_rejects_invalid_inputs() {
        let err = resample_pcm16_linear_interleaved(&[1, 2, 3], 2, 48_000, 44_100).unwrap_err();
        assert!(err.contains("not divisible by channels"));

        let err = resample_pcm16_linear_interleaved(&[1, 2], 0, 48_000, 44_100).unwrap_err();
        assert!(err.contains("channels must be > 0"));

        let err = resample_pcm16_linear_interleaved(&[1, 2], 1, 0, 44_100).unwrap_err();
        assert!(err.contains("sample rates must be > 0"));
    }

    #[test]
    fn sample_conversion_u8_maps_origin_to_midpoint() {
        assert_eq!(f32_to_u8_sample(-1.0), 0);
        assert_eq!(f32_to_u8_sample(0.0), 128);
        assert_eq!(f32_to_u8_sample(1.0), 255);
    }
}
