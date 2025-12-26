//! Phase 1: Native (Rust) playback engine for SongPack audio.
//!
//! Scope:
//! - output-only playback (no input monitoring)
//! - WAV PCM16 only for now
//! - minimal play/pause/stop/seek + loop region
//! - transport clock based on frames rendered
//!
//! Implementation notes:
//! - We use CPAL for audio output.
//! - We keep a shared EngineState protected by a Mutex; the audio callback uses
//!   a short critical section to pull samples. This is not ideal for pro audio,
//!   but is acceptable for Phase 1 bring-up. Phase 2 will replace this with
//!   lock-free structures.

use crate::audio_engine::{LoopRegion, Transport};
use crate::wav_mix::{read_wav_pcm16_bytes, WavPcm16};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Debug, Clone, serde::Serialize)]
pub struct NativeAudioState {
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub is_playing: bool,
    pub t_sec: f64,
    pub playback_rate: f64,
    pub loop_t0_sec: Option<f64>,
    pub loop_t1_sec: Option<f64>,
    pub has_audio: bool,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct NativeAudioDeviceInfo {
    pub name: String,
    pub channels: u16,
    pub sample_rate_hz: u32,
}

struct EngineState {
    wav: Option<WavPcm16>,
    // Current sample index into wav.data (interleaved samples)
    sample_idx: usize,

    transport: Transport,
    is_playing: bool,
    playback_rate: f64,
    loop_region: Option<LoopRegion>,
}

/// List output devices (best-effort summary).
pub fn list_output_devices() -> Result<Vec<NativeAudioDeviceInfo>, String> {
    let host = cpal::default_host();
    let mut out = vec![];
    for dev in host
        .output_devices()
        .map_err(|e| format!("output_devices: {e}"))?
    {
        let name = dev.name().unwrap_or_else(|_| "(unknown)".to_string());
        if let Ok(cfg) = dev.default_output_config() {
            out.push(NativeAudioDeviceInfo {
                name,
                channels: cfg.channels(),
                sample_rate_hz: cfg.sample_rate().0,
            });
        }
    }
    Ok(out)
}

/// Handle stored in Tauri managed state.
///
/// IMPORTANT: `cpal::Stream` is **not Send/Sync**, so we do NOT store it here.
/// Instead, we spawn a dedicated thread that owns the stream for the app lifetime.
pub struct NativeAudioHandle {
    pub sample_rate_hz: u32,
    pub channels: u16,
    shared: Arc<Mutex<EngineState>>,
    shutdown: Arc<AtomicBool>,
    thread: Mutex<Option<std::thread::JoinHandle<()>>>,
}

impl NativeAudioHandle {
    pub fn new(sample_rate_hz: u32, channels: u16) -> Result<Self, String> {
        let transport = Transport::new(sample_rate_hz)?;
        let shared = Arc::new(Mutex::new(EngineState {
            wav: None,
            sample_idx: 0,
            transport,
            is_playing: false,
            playback_rate: 1.0,
            loop_region: None,
        }));

        let shutdown = Arc::new(AtomicBool::new(false));
        let shared_for_thread = shared.clone();
        let shutdown_for_thread = shutdown.clone();

        let th = std::thread::spawn(move || {
            // Own the CPAL stream on this thread.
            if let Err(e) = run_output_stream_thread(shared_for_thread, shutdown_for_thread, sample_rate_hz, channels) {
                eprintln!("native audio thread failed: {e}");
            }
        });

        Ok(Self {
            sample_rate_hz,
            channels,
            shared,
            shutdown,
            thread: Mutex::new(Some(th)),
        })
    }

    pub fn load_wav_bytes(&self, wav_bytes: &[u8]) -> Result<(), String> {
        let wav = read_wav_pcm16_bytes(wav_bytes)?;
        if wav.sample_rate != self.sample_rate_hz {
            return Err(format!(
                "wav sample_rate {} != engine sample_rate {} (resampling not implemented)",
                wav.sample_rate, self.sample_rate_hz
            ));
        }
        if wav.channels != self.channels {
            return Err(format!("wav channels {} != engine channels {}", wav.channels, self.channels));
        }

        let mut st = self.shared.lock().unwrap();
        st.wav = Some(wav);
        st.sample_idx = 0;
        st.transport.seek_frames(0);
        Ok(())
    }

    pub fn load_pcm16(&self, sample_rate_hz: u32, channels: u16, data: Vec<i16>) -> Result<(), String> {
        if sample_rate_hz != self.sample_rate_hz {
            return Err(format!(
                "decoded sample_rate {} != engine sample_rate {} (re-init required)",
                sample_rate_hz, self.sample_rate_hz
            ));
        }
        if channels != self.channels {
            return Err(format!(
                "decoded channels {} != engine channels {} (re-init required)",
                channels, self.channels
            ));
        }

        let mut st = self.shared.lock().unwrap();
        st.wav = Some(crate::wav_mix::WavPcm16 {
            sample_rate: sample_rate_hz,
            channels,
            data,
        });
        st.sample_idx = 0;
        st.transport.seek_frames(0);
        Ok(())
    }

    pub fn play(&self) {
        let mut st = self.shared.lock().unwrap();
        st.is_playing = true;
        st.transport.set_playing(true);
    }

    pub fn pause(&self) {
        let mut st = self.shared.lock().unwrap();
        st.is_playing = false;
        st.transport.set_playing(false);
    }

    pub fn stop(&self) {
        let mut st = self.shared.lock().unwrap();
        st.is_playing = false;
        st.transport.set_playing(false);
        st.transport.seek_frames(0);
        st.sample_idx = 0;
    }

    pub fn seek_seconds(&self, t_sec: f64) {
        let mut st = self.shared.lock().unwrap();
        st.transport.seek_seconds(t_sec);
        let frame = st.transport.position_frames() as usize;
        let ch = self.channels as usize;
        st.sample_idx = frame.saturating_mul(ch);
    }

    pub fn set_loop_seconds(&self, t0: Option<f64>, t1: Option<f64>) -> Result<(), String> {
        let lr = match (t0, t1) {
            (Some(a), Some(b)) => {
                let start = (a.max(0.0) * self.sample_rate_hz as f64).floor() as u64;
                let end = (b.max(0.0) * self.sample_rate_hz as f64).floor() as u64;
                Some(LoopRegion::new(start, end)?)
            }
            _ => None,
        };
        let mut st = self.shared.lock().unwrap();
        st.loop_region = lr;
        st.transport.set_loop_region(lr);
        Ok(())
    }

    pub fn set_playback_rate(&self, rate: f64) {
        let mut st = self.shared.lock().unwrap();
        st.playback_rate = if rate.is_finite() && rate > 0.0 { rate } else { 1.0 };
    }

    pub fn state(&self) -> NativeAudioState {
        let st = self.shared.lock().unwrap();
        let lr = st.loop_region;
        NativeAudioState {
            sample_rate_hz: self.sample_rate_hz,
            channels: self.channels,
            is_playing: st.is_playing,
            t_sec: st.transport.position_seconds(),
            playback_rate: st.playback_rate,
            loop_t0_sec: lr.map(|x| x.start_frame as f64 / self.sample_rate_hz as f64),
            loop_t1_sec: lr.map(|x| x.end_frame as f64 / self.sample_rate_hz as f64),
            has_audio: st.wav.is_some(),
        }
    }

    pub fn shutdown(&self) {
        self.shutdown.store(true, Ordering::Relaxed);
        if let Some(jh) = self.thread.lock().unwrap().take() {
            let _ = jh.join();
        }
    }
}

fn run_output_stream_thread(
    shared: Arc<Mutex<EngineState>>,
    shutdown: Arc<AtomicBool>,
    sample_rate_hz: u32,
    channels: u16,
) -> Result<(), String> {
    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .ok_or_else(|| "no default output device".to_string())?;
    let default_cfg = device
        .default_output_config()
        .map_err(|e| format!("default_output_config: {e}"))?;
    let mut cfg = default_cfg.config();
    cfg.channels = channels;
    cfg.sample_rate.0 = sample_rate_hz;

    let shared_for_cb = shared.clone();
    let stream = device
        .build_output_stream(
            &cfg,
            move |out: &mut [f32], _info| {
                let mut st = match shared_for_cb.lock() {
                    Ok(l) => l,
                    Err(poisoned) => poisoned.into_inner(),
                };

                // Silence unless playing and loaded.
                if !st.is_playing || st.wav.is_none() {
                    for s in out.iter_mut() {
                        *s = 0.0;
                    }
                    return;
                }

                // Pull immutable copies for this callback.
                let (data, ch, n_samples) = {
                    let wav = st.wav.as_ref().unwrap();
                    (wav.data.as_slice(), wav.channels as usize, wav.data.len())
                };

                let mut idx = st.sample_idx;
                let step = st.playback_rate;
                let lr = st.loop_region;

                let mut frames_written: u64 = 0;

                for (i, s) in out.iter_mut().enumerate() {
                    if i % ch == 0 {
                        frames_written += 1;
                    }

                    let v = if idx < n_samples {
                        data[idx] as f32 / i16::MAX as f32
                    } else {
                        0.0
                    };
                    *s = v;

                    let next = (idx as f64 + step).floor() as usize;
                    idx = next;

                    if let Some(lr) = lr {
                        let end_sample = (lr.end_frame as usize) * ch;
                        if idx >= end_sample {
                            idx = (lr.start_frame as usize) * ch;
                        }
                    }
                }

                st.sample_idx = idx;
                st.transport.advance_frames(frames_written);
            },
            move |err| {
                eprintln!("native audio stream error: {err}");
            },
            None,
        )
        .map_err(|e| format!("build_output_stream: {e}"))?;

    stream.play().map_err(|e| format!("stream.play: {e}"))?;

    // Keep thread alive until shutdown requested.
    while !shutdown.load(Ordering::Relaxed) {
        std::thread::sleep(std::time::Duration::from_millis(50));
    }

    drop(stream);
    Ok(())
}

#[derive(Default)]
pub struct NativeAudioEngineState {
    pub engine: Mutex<Option<NativeAudioHandle>>,
}
