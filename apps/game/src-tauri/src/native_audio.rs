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
    LoadPcm16 { wav: WavPcm16 },
    Play,
    Pause,
    Stop,
    SeekFrames { frame: u64 },
    SetLoop { loop_region: Option<LoopRegion> },
    SetPlaybackRate { rate: f64 },
}

struct EngineRuntimeState {
    wav: Option<WavPcm16>,
    // Floating-point cursor in source frames for nearest-neighbor rate changes.
    source_frame_cursor: f64,

    transport: Transport,
    is_playing: bool,
    playback_rate: f64,
    loop_region: Option<LoopRegion>,
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

    shutdown: Arc<AtomicBool>,
    thread: Mutex<Option<std::thread::JoinHandle<()>>>,
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
            source_frame_cursor: 0.0,
            transport,
            is_playing: false,
            playback_rate: 1.0,
            loop_region: None,
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
            shutdown,
            thread: Mutex::new(Some(th)),
        })
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
        let outbuf = self.output_buffer_frames.load(Ordering::Relaxed);

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
            output_buffer_frames: if outbuf == 0 { None } else { Some(outbuf) },
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
                    best = Some((default_rank, rank, selected, sample_format, preferred_buffer));
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
    render_output_block(runtime, out, engine_channels);
    sync_transport_to_source_cursor(runtime);
    snapshot.sync_from_runtime(runtime);

    update_callback_telemetry(snapshot, callback_t0, out.len() / engine_channels, sample_rate_hz);
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
        }
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
