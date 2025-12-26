//! Utilities for creating a tiny non-copyright demo SongPack.
//!
//! Goal: make the very first run of the desktop app playable without requiring
//! the user to import anything.

use crate::wav_mix::{write_wav_pcm16, WavPcm16};
use std::fs;
use std::path::{Path, PathBuf};

fn demo_songpack_dir(songs_folder: &Path) -> PathBuf {
    songs_folder.join("demo_sine_440hz.songpack")
}

fn write_demo_wav(path: &Path) -> Result<f64, String> {
    // Keep this short and deterministic.
    let sample_rate: u32 = 48_000;
    let channels: u16 = 2;
    let duration_sec: f64 = 2.0;
    let hz: f64 = 440.0;

    let frames = (sample_rate as f64 * duration_sec).round() as usize;
    let mut data: Vec<i16> = Vec::with_capacity(frames * channels as usize);

    for i in 0..frames {
        let t = (i as f64) / (sample_rate as f64);
        let s = (t * hz * std::f64::consts::TAU).sin();
        let v = (s * 6000.0).round() as i16;

        // stereo interleaved
        data.push(v);
        data.push(v);
    }

    let wav = WavPcm16 {
        sample_rate,
        channels,
        data,
    };

    write_wav_pcm16(path, &wav)?;
    Ok(duration_sec)
}

/// Ensure a demo SongPack exists in the songs folder.
///
/// Returns:
/// - `Ok(Some(path))` when it was created
/// - `Ok(None)` when it already existed
pub fn ensure_demo_songpack(songs_folder: &Path) -> Result<Option<PathBuf>, String> {
    let out_dir = demo_songpack_dir(songs_folder);
    if out_dir.exists() {
        return Ok(None);
    }

    fs::create_dir_all(out_dir.join("audio")).map_err(|e| format!("mkdir audio: {e}"))?;
    fs::create_dir_all(out_dir.join("features")).map_err(|e| format!("mkdir features: {e}"))?;
    fs::create_dir_all(out_dir.join("charts")).map_err(|e| format!("mkdir charts: {e}"))?;

    let mix_path = out_dir.join("audio").join("mix.wav");
    let duration_sec = write_demo_wav(&mix_path)?;

    // Minimal manifest.
    let manifest = serde_json::json!({
        "schema_version": "1.0.0",
        "song_id": "demo_sine_440hz",
        "title": "Demo Sine Wave",
        "artist": "AuralPrimer",
        "duration_sec": duration_sec,
        "source": {
            "kind": "demo",
            "generator": "demo_songpack::ensure_demo_songpack",
            "hz": 440,
            "duration_sec": duration_sec,
            "sample_rate_hz": 48000,
            "channels": 2,
        },
        "assets": {
            "audio": {"mix_path": "audio/mix.wav"}
        }
    });

    fs::write(
        out_dir.join("manifest.json"),
        serde_json::to_string_pretty(&manifest).map_err(|e| format!("manifest json: {e}"))?,
    )
    .map_err(|e| format!("write manifest.json: {e}"))?;

    Ok(Some(out_dir))
}

